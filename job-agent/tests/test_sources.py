"""Source parsing, with the network stubbed.

The sandbox has no network, and these must pass offline anyway. Fixtures use
each API's documented response shape -- worth re-checking against one real
call once the keys exist, since a renamed field would show up here as a
passing test and an empty digest in real life.
"""

import requests

from jobagent.config import Config, QuietHours
from jobagent.sources import adzuna, reed


def _config(**overrides):
    defaults = dict(
        enabled=True,
        run_hours=(7, 18),
        timezone="Europe/London",
        min_score_to_keep=6,
        push_threshold=8,
        explain_scores=True,
        push_enabled=True,
        push_max_per_day=2,
        push_vague_wording=True,
        quiet_hours=QuietHours("21:30", "07:30"),
        email_enabled=True,
        email_digest_hours=(7, 18),
        email_max_roles=12,
        email_send_when_empty=False,
        search_terms=("operations associate",),
        locations=("London",),
        max_distance_miles=20,
        max_age_days=14,
        adzuna_enabled=True,
        reed_enabled=True,
        companies_enabled=True,
        companies=(),
        inbox_enabled=True,
        inbox_max_age_days=3,
        inbox_trusted_senders=(),
        seen_retention_days=60,
        alert_on_failure=True,
    )
    defaults.update(overrides)
    return Config(**defaults)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


ADZUNA_PAYLOAD = {
    "results": [
        {
            "title": "Operations Associate",
            "company": {"display_name": "Example Ltd"},
            "location": {"display_name": "London, City of London"},
            "description": "Support the operations team.",
            "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/123",
            "created": "2026-07-20T09:00:00Z",
            "salary_min": 32000,
            "salary_max": 38000,
        }
    ]
}

REED_PAYLOAD = {
    "results": [
        {
            "jobTitle": "Operations Associate",
            "employerName": "Example Limited",
            "locationName": "London",
            "jobDescription": "Support the operations team.",
            "jobUrl": "https://www.reed.co.uk/jobs/ops-associate/456",
            "date": "20/07/2026",
            "minimumSalary": 32000,
            "maximumSalary": 38000,
        }
    ]
}


class TestAdzuna:
    def test_parses_the_documented_shape(self, monkeypatch):
        monkeypatch.setattr(
            adzuna.requests, "get", lambda *a, **k: _FakeResponse(ADZUNA_PAYLOAD)
        )

        jobs = adzuna.fetch("id", "key", _config())

        assert len(jobs) == 1
        job = jobs[0]
        assert job.source == "adzuna"
        assert job.company == "Example Ltd"
        assert job.location == "London, City of London"
        assert job.posted == "2026-07-20"
        assert job.salary_min == 32000

    def test_skips_cleanly_without_credentials(self):
        assert adzuna.fetch("", "", _config()) == []

    def test_one_failing_term_does_not_lose_the_others(self, monkeypatch):
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("boom")
            return _FakeResponse(ADZUNA_PAYLOAD)

        monkeypatch.setattr(adzuna.requests, "get", flaky)

        jobs = adzuna.fetch("id", "key", _config(search_terms=("a", "b")))

        assert len(jobs) == 1  # second term still delivered

    def test_missing_nested_fields_do_not_crash(self, monkeypatch):
        monkeypatch.setattr(
            adzuna.requests,
            "get",
            lambda *a, **k: _FakeResponse({"results": [{"title": "Ops"}]}),
        )

        jobs = adzuna.fetch("id", "key", _config())

        assert jobs[0].company == ""
        assert jobs[0].salary_min is None


class TestReed:
    def test_parses_the_documented_shape(self, monkeypatch):
        monkeypatch.setattr(
            reed.requests, "get", lambda *a, **k: _FakeResponse(REED_PAYLOAD)
        )

        jobs = reed.fetch("key", _config())

        assert len(jobs) == 1
        assert jobs[0].source == "reed"
        assert jobs[0].company == "Example Limited"
        assert jobs[0].posted == "2026-07-20"  # converted from dd/mm/yyyy

    def test_uses_basic_auth_with_an_empty_password(self, monkeypatch):
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            return _FakeResponse(REED_PAYLOAD)

        monkeypatch.setattr(reed.requests, "get", capture)

        reed.fetch("my-key", _config())

        assert captured["auth"] == ("my-key", "")

    def test_skips_cleanly_without_a_key(self):
        assert reed.fetch("", _config()) == []

    def test_handles_a_malformed_date(self, monkeypatch):
        payload = {"results": [dict(REED_PAYLOAD["results"][0], date="")]}
        monkeypatch.setattr(reed.requests, "get", lambda *a, **k: _FakeResponse(payload))

        assert reed.fetch("key", _config())[0].posted == ""


class TestCrossSource:
    def test_adzuna_and_reed_versions_of_one_role_merge(self, monkeypatch):
        """The end-to-end case this whole design exists for.

        Note both modules import the *same* requests module, so patching
        adzuna.requests.get and reed.requests.get separately silently leaves
        only the second one in place. Dispatch on the URL instead.
        """
        from jobagent.models import merge

        def by_url(url, *args, **kwargs):
            payload = ADZUNA_PAYLOAD if "adzuna" in url else REED_PAYLOAD
            return _FakeResponse(payload)

        monkeypatch.setattr(adzuna.requests, "get", by_url)

        found = adzuna.fetch("id", "key", _config()) + reed.fetch("key", _config())
        merged = merge(found)

        assert len(found) == 2
        assert len(merged) == 1
        assert set(merged[0].sources) == {"adzuna", "reed"}
        # Reed's agency link loses to Adzuna's, per SOURCE_RANK.
        assert merged[0].url == ADZUNA_PAYLOAD["results"][0]["redirect_url"]
