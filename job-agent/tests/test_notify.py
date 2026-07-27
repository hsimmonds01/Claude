import smtplib
from datetime import date

import requests

from jobagent.models import Job, ScoredJob, merge
from jobagent.notify import mail, push
from jobagent.state import RunState


def _scored(score, title="Operations Associate", company="Example Ltd", **kwargs):
    merged = merge(
        [
            Job(
                source=kwargs.pop("source", "adzuna"),
                title=title,
                company=company,
                url=kwargs.pop("url", "https://example.com/job"),
                **kwargs,
            )
        ]
    )[0]
    return ScoredJob(job=merged, score=score, reason="looks like a good fit")


class TestDiscretion:
    """Lock-screen text must never identify a company or a role. She is still
    employed; this is a hard requirement, not a style choice."""

    def test_vague_wording_hides_everything_identifying(self):
        jobs = [_scored(9, title="Head of Operations", company="Distinctive Ltd")]

        title, body = push.build_message(jobs, vague=True)

        assert "Distinctive" not in title + body
        assert "Head of Operations" not in title + body
        assert body == "1 new match"

    def test_vague_wording_pluralises(self):
        _, body = push.build_message([_scored(9), _scored(8)], vague=True)

        assert body == "2 new matches"

    def test_detail_only_appears_when_vague_is_switched_off(self):
        jobs = [_scored(9, title="Head of Operations", company="Distinctive Ltd")]

        _, body = push.build_message(jobs, vague=False)

        assert "Head of Operations" in body

    def test_topic_is_never_logged_on_failure(self, monkeypatch, caplog):
        # The topic name is the only thing protecting her notifications --
        # public ntfy topics have no authentication at all.
        def boom(*args, **kwargs):
            raise requests.ConnectionError("network down")

        monkeypatch.setattr(push.requests, "post", boom)

        push.send("hj-9f4k2xq7m3", [_scored(9)], vague=True)

        assert "hj-9f4k2xq7m3" not in caplog.text


class TestPushSelection:
    def test_only_roles_at_or_above_the_threshold(self):
        chosen = push.choose(
            [_scored(9), _scored(8), _scored(7)], threshold=8, allowance=5, quiet=False
        )

        assert [job.score for job in chosen] == [9, 8]

    def test_respects_the_daily_allowance(self):
        chosen = push.choose(
            [_scored(10), _scored(9), _scored(9)], threshold=8, allowance=2, quiet=False
        )

        assert len(chosen) == 2
        assert [job.score for job in chosen] == [10, 9]

    def test_nothing_during_quiet_hours(self):
        assert push.choose([_scored(10)], threshold=8, allowance=5, quiet=True) == []

    def test_nothing_when_the_allowance_is_spent(self):
        assert push.choose([_scored(10)], threshold=8, allowance=0, quiet=False) == []

    def test_strongest_first(self):
        chosen = push.choose(
            [_scored(8), _scored(10), _scored(9)], threshold=8, allowance=3, quiet=False
        )

        assert [job.score for job in chosen] == [10, 9, 8]


class TestPushSending:
    def test_sets_a_tap_target_and_a_feedback_action(self, monkeypatch):
        captured = {}

        def capture(url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs["headers"]
            return type("R", (), {"raise_for_status": lambda self: None})()

        monkeypatch.setattr(push.requests, "post", capture)

        push.send(
            "topic", [_scored(9)], vague=True, feedback_url="https://github.com/x/edit"
        )

        assert captured["url"] == "https://ntfy.sh/topic"
        assert captured["headers"]["Click"] == "https://example.com/job"
        assert "https://github.com/x/edit" in captured["headers"]["Actions"]

    def test_missing_topic_is_survivable(self):
        assert push.send("", [_scored(9)], vague=True) is False

    def test_network_failure_is_survivable(self, monkeypatch):
        monkeypatch.setattr(
            push.requests,
            "post",
            lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()),
        )

        assert push.send("topic", [_scored(9)], vague=True) is False


class TestDigestRendering:
    def test_shows_score_title_company_and_reason(self):
        html = mail.render([_scored(9)])

        assert "9/10" in html
        assert "Operations Associate" in html
        assert "Example Ltd" in html
        assert "looks like a good fit" in html

    def test_escapes_html_in_job_data(self):
        # Job adverts are third-party text going into an HTML email.
        html = mail.render([_scored(7, company="<script>alert(1)</script>")])

        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_lists_every_source_that_carried_the_role(self):
        merged = merge(
            [
                Job(source="adzuna", title="Ops", company="X", url="https://a"),
                Job(source="reed", title="Ops", company="X", url="https://b"),
            ]
        )[0]

        html = mail.render([ScoredJob(job=merged, score=8, reason="")])

        assert "adzuna" in html and "reed" in html

    def test_includes_the_feedback_link(self):
        html = mail.render([_scored(8)], feedback_url="https://github.com/x/edit")

        assert "https://github.com/x/edit" in html

    def test_empty_digest_explains_itself(self):
        html = mail.render_empty()

        assert "Nothing new today" in html


class TestEmailSending:
    def test_missing_credentials_is_survivable(self):
        assert (
            mail.send(address="", app_password="", to="a@b.c", subject="x", body_html="y")
            is False
        )

    def test_missing_destination_is_survivable(self):
        assert (
            mail.send(address="a@b.c", app_password="p", to="", subject="x", body_html="y")
            is False
        )

    def test_smtp_failure_is_survivable(self, monkeypatch):
        def boom(*args, **kwargs):
            raise smtplib.SMTPAuthenticationError(535, b"bad app password")

        monkeypatch.setattr(mail.smtplib, "SMTP_SSL", boom)

        assert (
            mail.send(
                address="a@b.c", app_password="p", to="d@e.f", subject="x", body_html="y"
            )
            is False
        )


class TestRunState:
    def test_push_allowance_resets_on_a_new_day(self):
        run_state = RunState()
        run_state.record_pushes(2, today=date(2026, 7, 27))

        assert run_state.pushes_left(2, today=date(2026, 7, 27)) == 0
        assert run_state.pushes_left(2, today=date(2026, 7, 28)) == 2

    def test_digest_guard_blocks_a_repeat_of_the_same_slot(self):
        # cron-job.org is primary and GitHub's schedule is a backup, so
        # overlapping triggers are expected rather than exceptional.
        run_state = RunState()
        run_state.record_digest(7, today=date(2026, 7, 27))

        assert run_state.digest_already_sent(7, today=date(2026, 7, 27)) is True
        assert run_state.digest_already_sent(7, today=date(2026, 7, 28)) is False

    def test_the_morning_digest_does_not_block_the_evening_one(self):
        # Regression. A day-level guard meant the 7am digest silently
        # suppressed the 6pm one for the rest of the day, so she quietly got
        # one digest instead of the two she configured.
        run_state = RunState()
        run_state.record_digest(7, today=date(2026, 7, 27))

        assert run_state.digest_already_sent(18, today=date(2026, 7, 27)) is False

        run_state.record_digest(18, today=date(2026, 7, 27))

        assert run_state.digest_already_sent(18, today=date(2026, 7, 27)) is True

    def test_digest_hours_reset_on_a_new_day(self):
        run_state = RunState()
        run_state.record_digest(7, today=date(2026, 7, 27))
        run_state.record_digest(18, today=date(2026, 7, 27))

        run_state.record_digest(7, today=date(2026, 7, 28))

        assert run_state.digest_hours == (7,)

    def test_round_trips_through_a_file(self, tmp_path):
        path = tmp_path / "state.json"
        run_state = RunState()
        run_state.record_pushes(1, today=date(2026, 7, 27))
        run_state.record_digest(7, today=date(2026, 7, 27))
        run_state.record_digest(18, today=date(2026, 7, 27))
        run_state.save(path)

        reloaded = RunState.load(path)

        assert reloaded.push_count == 1
        assert reloaded.digest_date == "2026-07-27"
        assert reloaded.digest_hours == (7, 18)

    def test_corrupt_state_costs_at_most_one_extra_push(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("not json", encoding="utf-8")

        assert RunState.load(path).pushes_left(2) == 2
