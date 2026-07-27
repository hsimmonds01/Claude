"""End-to-end wiring, with every outbound call stubbed.

These cover the joins between modules -- the places unit tests pass happily
while the assembled thing does nothing useful.
"""

import json
from argparse import Namespace

import pytest

import run as run_module
from jobagent import config as config_module
from jobagent.models import Job
from jobagent.scoring import Verdict

CONFIG = """
enabled: true
schedule:
  run_hours: [7, 12, 16, 18]
scoring:
  min_score_to_keep: 6
  push_threshold: 8
push:
  enabled: true
  max_per_day: 2
  quiet_hours:
    start: "21:30"
    end: "07:30"
email:
  enabled: true
  send_on_run_hours: [7, 18]
sources:
  search_terms:
    - operations associate
"""


@pytest.fixture
def agent(tmp_path, monkeypatch):
    """A configured agent in a temp directory, with all I/O stubbed."""
    (tmp_path / "config.yml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "cv.md").write_text("Alex Fictional, operations.", encoding="utf-8")
    (tmp_path / "profile.md").write_text("Operations roles in London.", encoding="utf-8")
    (tmp_path / "feedback.md").write_text(
        "## Standing rules\n- No agencies.\n", encoding="utf-8"
    )

    monkeypatch.setattr(run_module, "ROOT", tmp_path)
    monkeypatch.setattr(run_module, "CONFIG_PATH", tmp_path / "config.yml")
    monkeypatch.setattr(run_module, "SEEN_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(run_module, "STATE_PATH", tmp_path / "state.json")

    sent = {"push": [], "email": [], "failure": []}

    monkeypatch.setattr(
        run_module.push,
        "send",
        lambda topic, jobs, **kw: sent["push"].append(jobs) or True,
    )
    monkeypatch.setattr(
        run_module.mail,
        "send",
        lambda **kw: sent["email"].append(kw) or True,
    )
    monkeypatch.setattr(
        run_module.mail,
        "send_failure_notice",
        lambda **kw: sent["failure"].append(kw) or True,
    )
    monkeypatch.setattr(run_module.reed, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(run_module.inbox, "fetch", lambda *a, **k: [])

    monkeypatch.setenv("NTFY_TOPIC", "topic")
    monkeypatch.setenv("GMAIL_ADDRESS", "agent@example.invalid")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("DIGEST_TO", "her@example.invalid")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setenv("GITHUB_REPOSITORY", "her/job-agent")

    return Namespace(tmp_path=tmp_path, sent=sent, monkeypatch=monkeypatch)


def _supply(agent, jobs, verdicts):
    agent.monkeypatch.setattr(run_module.adzuna, "fetch", lambda *a, **k: jobs)
    agent.monkeypatch.setattr(run_module.scoring, "score", lambda *a, **k: verdicts)


def _job(title="Operations Associate", company="Example Ltd", url="https://x/1"):
    return Job(
        source="adzuna",
        title=title,
        company=company,
        url=url,
        description="Support the operations team.",
        location="London",
    )


def _args(**overrides):
    defaults = dict(dry_run=False, force=True)
    defaults.update(overrides)
    return Namespace(**defaults)


class TestHappyPath:
    def test_strong_role_pushes_and_lands_in_the_digest(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong match")])

        assert run_module.run(_args()) == 0

        assert len(agent.sent["push"]) == 1
        assert len(agent.sent["email"]) == 1
        assert "1 job worth a look" in agent.sent["email"][0]["subject"]

    def test_the_role_is_remembered_so_it_never_repeats(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong match")])

        run_module.run(_args())

        seen = json.loads((agent.tmp_path / "seen.json").read_text(encoding="utf-8"))
        assert len(seen["seen"]) == 1
        assert seen["seen"][0]["score"] == 9

    def test_a_second_run_sends_nothing_new(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong match")])
        run_module.run(_args())
        agent.sent["push"].clear()
        agent.sent["email"].clear()

        # Scoring is never asked about an already-seen role, so return nothing.
        _supply(agent, [job], [])
        run_module.run(_args())

        assert agent.sent["push"] == []


class TestThresholds:
    def test_a_weak_role_is_dropped_but_still_remembered(self, agent, monkeypatch):
        # Recorded so it's never scored again -- that's the saving.
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 3, "too junior")])

        run_module.run(_args())

        seen = json.loads((agent.tmp_path / "seen.json").read_text(encoding="utf-8"))
        assert seen["seen"][0]["score"] == 3
        assert agent.sent["push"] == []

    def test_a_middling_role_makes_the_digest_but_not_the_push(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 7, "decent")])

        run_module.run(_args())

        assert agent.sent["push"] == []
        assert len(agent.sent["email"]) == 1


class TestTwoDigestsADay:
    def test_both_configured_digests_send_on_the_same_day(self, agent, monkeypatch):
        """Regression, and the reason this is an end-to-end test.

        The duplicate-trigger guard was scoped to the day rather than the
        digest slot, so the 7am send silently suppressed the 6pm one. Every
        unit test still passed: she just quietly got one digest a day instead
        of two, which reads as "the agent is a bit quiet" rather than a bug.
        """
        morning = _job(title="Morning Role", url="https://x/am")
        _freeze(monkeypatch, hour=7)
        _supply(agent, [morning], [Verdict(_fingerprint(morning), 9, "strong")])
        run_module.run(_args())

        assert len(agent.sent["email"]) == 1

        evening = _job(title="Evening Role", url="https://x/pm")
        _freeze(monkeypatch, hour=18)
        _supply(agent, [evening], [Verdict(_fingerprint(evening), 9, "strong")])
        run_module.run(_args())

        assert len(agent.sent["email"]) == 2

    def test_a_repeat_trigger_in_the_same_slot_does_not_resend(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=7)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong")])
        run_module.run(_args())
        run_module.run(_args())

        assert len(agent.sent["email"]) == 1


class TestScheduling:
    def test_no_digest_outside_a_digest_hour(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=12)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong")])

        run_module.run(_args())

        assert len(agent.sent["push"]) == 1  # push still fires
        assert agent.sent["email"] == []  # digest waits for 18:00

    def test_master_switch_off_sends_nothing(self, agent, monkeypatch):
        (agent.tmp_path / "config.yml").write_text(
            CONFIG.replace("enabled: true", "enabled: false", 1), encoding="utf-8"
        )
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong")])

        assert run_module.run(_args(force=False)) == 0
        assert agent.sent["push"] == []
        assert agent.sent["email"] == []


class TestFailureReporting:
    def test_a_broken_config_emails_her_rather_than_failing_silently(
        self, agent, monkeypatch
    ):
        # The plan's original bug: digests scheduled for an hour with no run.
        (agent.tmp_path / "config.yml").write_text(
            """
schedule:
  run_hours: [7, 12, 16, 20]
email:
  send_on_run_hours: [8, 18]
""",
            encoding="utf-8",
        )

        assert run_module.run(_args()) == 2
        assert len(agent.sent["failure"]) == 1
        assert "config.yml" in agent.sent["failure"][0]["reason"]

    def test_an_unexpected_crash_still_reaches_her(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=18)

        def boom(*args, **kwargs):
            raise RuntimeError("adzuna exploded in a new and exciting way")

        monkeypatch.setattr(run_module.adzuna, "fetch", boom)
        monkeypatch.setattr("sys.argv", ["run.py", "--force"])

        assert run_module.main() == 1
        assert len(agent.sent["failure"]) == 1


class TestDryRun:
    def test_sends_nothing_and_writes_nothing(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong")])

        assert run_module.run(_args(dry_run=True)) == 0

        assert agent.sent["push"] == []
        assert agent.sent["email"] == []
        assert not (agent.tmp_path / "seen.json").exists()


class TestFeedbackLink:
    def test_points_at_the_mobile_edit_view(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPOSITORY", "her/job-agent")

        assert (
            run_module.feedback_url()
            == "https://github.com/her/job-agent/edit/main/feedback.md"
        )

    def test_empty_when_not_running_in_actions(self, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

        assert run_module.feedback_url() == ""


def _fingerprint(job):
    return job.fingerprint


def _freeze(monkeypatch, hour):
    """Pin the agent's clock to a given hour in *her* timezone.

    Patches Config.now rather than the datetime module. That is now the only
    place the clock is read, and it's the seam that matters: the previous
    version stubbed run.datetime, which meant the timezone conversion this
    whole thing hinges on was never exercised at all.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def fixed(self):
        return datetime(2026, 7, 27, hour, 0, 0, tzinfo=ZoneInfo(self.timezone))

    monkeypatch.setattr(config_module.Config, "now", fixed)


class TestSettingsThatUsedToDoNothing:
    """explain_scores, alert_on_failure and notified were all parsed,
    documented in config.yml as working controls, and read by nothing."""

    def test_explain_scores_off_stops_asking_for_a_reason(self, agent, monkeypatch):
        (agent.tmp_path / "config.yml").write_text(
            CONFIG + "\nscoring:\n  explain_scores: false\n", encoding="utf-8"
        )
        _freeze(monkeypatch, hour=18)
        captured = {}

        def capture(api_key, jobs, guidance, *, explain=True):
            captured["explain"] = explain
            return []

        monkeypatch.setattr(run_module.scoring, "score", capture)
        monkeypatch.setattr(run_module.adzuna, "fetch", lambda *a, **k: [_job()])

        run_module.run(_args())

        assert captured["explain"] is False

    def test_explain_scores_defaults_to_on(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=18)
        captured = {}

        def capture(api_key, jobs, guidance, *, explain=True):
            captured["explain"] = explain
            return []

        monkeypatch.setattr(run_module.scoring, "score", capture)
        monkeypatch.setattr(run_module.adzuna, "fetch", lambda *a, **k: [_job()])

        run_module.run(_args())

        assert captured["explain"] is True

    def test_alert_on_failure_off_suppresses_the_failure_email(self, agent, monkeypatch):
        (agent.tmp_path / "config.yml").write_text(
            CONFIG + "\nhousekeeping:\n  alert_on_failure: false\n", encoding="utf-8"
        )
        _freeze(monkeypatch, hour=18)

        def boom(*args, **kwargs):
            raise RuntimeError("adzuna exploded")

        monkeypatch.setattr(run_module.adzuna, "fetch", boom)
        monkeypatch.setattr("sys.argv", ["run.py", "--force"])

        assert run_module.main() == 1
        assert agent.sent["failure"] == []

    def test_a_broken_config_still_emails_even_though_it_cannot_be_read(
        self, agent, monkeypatch
    ):
        # There's no setting to consult when the settings file is the problem.
        (agent.tmp_path / "config.yml").write_text(
            "schedule:\n  run_hours: [7]\nemail:\n  send_on_run_hours: [18]\n",
            encoding="utf-8",
        )

        assert run_module.run(_args()) == 2
        assert len(agent.sent["failure"]) == 1

    def test_a_pushed_role_is_recorded_as_notified(self, agent, monkeypatch):
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong")])

        run_module.run(_args())

        seen = json.loads((agent.tmp_path / "seen.json").read_text(encoding="utf-8"))
        assert seen["seen"][0]["notified"] is True

    def test_a_digest_only_role_is_not_marked_notified(self, agent, monkeypatch):
        # 7 clears the keep threshold but not the push threshold of 8.
        _freeze(monkeypatch, hour=18)
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 7, "decent")])

        run_module.run(_args())

        seen = json.loads((agent.tmp_path / "seen.json").read_text(encoding="utf-8"))
        assert seen["seen"][0]["notified"] is False
