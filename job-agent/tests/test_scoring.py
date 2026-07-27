import json

import requests

from jobagent import scoring
from jobagent.models import Job, merge
from jobagent.steering import Steering


def _jobs(n=2):
    return merge(
        [
            Job(
                source="adzuna",
                title=f"Operations Associate {i}",
                company=f"Company {i}",
                url=f"https://example.com/{i}",
                description="Support the operations team. " * 5,
                location="London",
                salary_min=32000,
                salary_max=38000,
            )
            for i in range(n)
        ]
    )


def _steering(**overrides):
    defaults = dict(
        cv="Alex Fictional, operations.",
        profile="Wants operations roles in London.",
        standing_rules=("No agencies.",),
        recent_reactions=("too junior",),
    )
    defaults.update(overrides)
    return Steering(**defaults)


class _FakeResponse:
    def __init__(self, text="", status=200):
        self._text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}


def _reply(rows):
    return json.dumps({"scores": rows})


class TestPromptBuilding:
    def test_includes_cv_profile_and_both_feedback_sections(self):
        prompt = scoring.build_prompt(_jobs(1), _steering())

        assert "Alex Fictional" in prompt
        assert "operations roles in London" in prompt
        assert "No agencies." in prompt
        assert "too junior" in prompt

    def test_standing_rules_are_marked_as_overriding(self):
        prompt = scoring.build_prompt(_jobs(1), _steering())

        assert "absolute and override" in prompt

    def test_says_later_reactions_win(self):
        prompt = scoring.build_prompt(_jobs(1), _steering())

        assert "later ones win" in prompt

    def test_truncates_a_very_long_description(self):
        jobs = merge(
            [
                Job(
                    source="adzuna",
                    title="Ops",
                    company="Example",
                    url="https://a",
                    description="x" * 10_000,
                )
            ]
        )

        prompt = scoring.build_prompt(jobs, _steering())

        assert len(prompt) < 5_000

    def test_handles_missing_cv_and_profile(self):
        prompt = scoring.build_prompt(_jobs(1), _steering(cv="", profile=""))

        assert "(not provided)" in prompt

    def test_instructs_the_model_to_be_strict(self):
        # Guards the failure mode where everything scores 8 and the push
        # threshold becomes meaningless.
        prompt = scoring.build_prompt(_jobs(1), _steering())

        assert "Be strict" in prompt
        assert "Do not inflate" in prompt


class TestResponseParsing:
    def test_maps_scores_back_onto_fingerprints(self, monkeypatch):
        jobs = _jobs(2)
        monkeypatch.setattr(
            scoring.requests,
            "post",
            lambda *a, **k: _FakeResponse(
                _reply(
                    [
                        {"job": 0, "score": 9, "reason": "strong ops match"},
                        {"job": 1, "score": 4, "reason": "too junior"},
                    ]
                )
            ),
        )

        verdicts = scoring.score("key", jobs, _steering())

        assert [v.score for v in verdicts] == [9, 4]
        assert verdicts[0].fingerprint == jobs[0].fingerprint
        assert verdicts[1].fingerprint == jobs[1].fingerprint

    def test_tolerates_a_json_code_fence(self, monkeypatch):
        # Models add these regardless of being told to return raw JSON.
        fenced = "```json\n" + _reply([{"job": 0, "score": 7, "reason": "ok"}]) + "\n```"
        monkeypatch.setattr(
            scoring.requests, "post", lambda *a, **k: _FakeResponse(fenced)
        )

        assert scoring.score("key", _jobs(1), _steering())[0].score == 7

    def test_drops_a_hallucinated_job_index(self, monkeypatch):
        # Attributing a score to the wrong job would be worse than losing it.
        monkeypatch.setattr(
            scoring.requests,
            "post",
            lambda *a, **k: _FakeResponse(
                _reply(
                    [
                        {"job": 0, "score": 7, "reason": "ok"},
                        {"job": 99, "score": 10, "reason": "does not exist"},
                    ]
                )
            ),
        )

        verdicts = scoring.score("key", _jobs(1), _steering())

        assert len(verdicts) == 1

    def test_clamps_out_of_range_scores(self, monkeypatch):
        monkeypatch.setattr(
            scoring.requests,
            "post",
            lambda *a, **k: _FakeResponse(
                _reply([{"job": 0, "score": 47, "reason": ""}])
            ),
        )

        assert scoring.score("key", _jobs(1), _steering())[0].score == 10

    def test_accepts_a_float_score(self, monkeypatch):
        monkeypatch.setattr(
            scoring.requests,
            "post",
            lambda *a, **k: _FakeResponse(
                _reply([{"job": 0, "score": 7.6, "reason": ""}])
            ),
        )

        assert scoring.score("key", _jobs(1), _steering())[0].score == 8

    def test_skips_rows_missing_a_score(self, monkeypatch):
        monkeypatch.setattr(
            scoring.requests,
            "post",
            lambda *a, **k: _FakeResponse(
                _reply([{"job": 0, "reason": "forgot the score"}])
            ),
        )

        assert scoring.score("key", _jobs(1), _steering()) == []


class TestFailureHandling:
    def test_falls_back_to_the_next_model(self, monkeypatch):
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResponse(status=404)  # key can't reach model one
            return _FakeResponse(_reply([{"job": 0, "score": 6, "reason": "ok"}]))

        monkeypatch.setattr(scoring.requests, "post", flaky)
        monkeypatch.setattr(scoring.time, "sleep", lambda *_: None)

        verdicts = scoring.score("key", _jobs(1), _steering())

        assert verdicts[0].score == 6
        assert calls["n"] == 2

    def test_a_failed_batch_is_skipped_not_fatal(self, monkeypatch):
        # Unscored jobs are never recorded as seen, so the next run retries
        # them. A delay, not a loss -- and never a crash.
        monkeypatch.setattr(
            scoring.requests, "post", lambda *a, **k: _FakeResponse(status=503)
        )
        monkeypatch.setattr(scoring.time, "sleep", lambda *_: None)

        assert scoring.score("key", _jobs(1), _steering()) == []

    def test_unparseable_response_is_survivable(self, monkeypatch):
        monkeypatch.setattr(
            scoring.requests, "post", lambda *a, **k: _FakeResponse("not json at all")
        )
        monkeypatch.setattr(scoring.time, "sleep", lambda *_: None)

        assert scoring.score("key", _jobs(1), _steering()) == []

    def test_no_api_key_scores_nothing(self):
        assert scoring.score("", _jobs(1), _steering()) == []

    def test_no_jobs_makes_no_call(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("should not call the API with no jobs")

        monkeypatch.setattr(scoring.requests, "post", explode)

        assert scoring.score("key", [], _steering()) == []

    def test_never_attaches_the_search_tool(self, monkeypatch):
        """Grounded search is the one feature Google wants a billing card for.

        The whole project is built on there being no card anywhere, so this
        must never regress.
        """
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs.get("json", {}))
            return _FakeResponse(_reply([{"job": 0, "score": 5, "reason": ""}]))

        monkeypatch.setattr(scoring.requests, "post", capture)

        scoring.score("key", _jobs(1), _steering())

        assert "tools" not in captured


class TestBatching:
    def test_splits_large_input_into_batches(self, monkeypatch):
        calls = {"n": 0}

        def counting(*args, **kwargs):
            calls["n"] += 1
            return _FakeResponse(_reply([{"job": 0, "score": 5, "reason": ""}]))

        monkeypatch.setattr(scoring.requests, "post", counting)

        scoring.score("key", _jobs(scoring.BATCH_SIZE + 1), _steering())

        assert calls["n"] == 2
