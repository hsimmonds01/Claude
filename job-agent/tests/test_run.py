"""End-to-end wiring, with every outbound call stubbed.

These cover the joins between modules -- the places unit tests pass happily
while the assembled thing does nothing useful.
"""

import json
from argparse import Namespace
from pathlib import Path

import pytest

import run as run_module
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
        monkeypatch.setattr(run_module, "datetime", _clock(hour=18))
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong match")])

        assert run_module.run(_args()) == 0

        assert len(agent.sent["push"]) == 1
        assert len(agent.sent["email"]) == 1
        assert "1 job worth a look" in agent.sent["email"][0]["subject"]

    def test_the_role_is_remembered_so_it_never_repeats(self, agent, monkeypatch):
        monkeypatch.setattr(run_module, "datetime", _clock(hour=18))
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong match")])

        run_module.run(_args())

        seen = json.loads((agent.tmp_path / "seen.json").read_text(encoding="utf-8"))
        assert len(seen["seen"]) == 1
        assert seen["seen"][0]["score"] == 9

    def test_a_second_run_sends_nothing_new(self, agent, monkeypatch):
        monkeypatch.setattr(run_module, "datetime", _clock(hour=18))
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
        monkeypatch.setattr(run_module, "datetime", _clock(hour=18))
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 3, "too junior")])

        run_module.run(_args())

        seen = json.loads((agent.tmp_path / "seen.json").read_text(encoding="utf-8"))
        assert seen["seen"][0]["score"] == 3
        assert agent.sent["push"] == []

    def test_a_middling_role_makes_the_digest_but_not_the_push(self, agent, monkeypatch):
        monkeypatch.setattr(run_module, "datetime", _clock(hour=18))
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 7, "decent")])

        run_module.run(_args())

        assert agent.sent["push"] == []
        assert len(agent.sent["email"]) == 1


class TestScheduling:
    def test_no_digest_outside_a_digest_hour(self, agent, monkeypatch):
        monkeypatch.setattr(run_module, "datetime", _clock(hour=12))
        job = _job()
        _supply(agent, [job], [Verdict(_fingerprint(job), 9, "strong")])

        run_module.run(_args())

        assert len(agent.sent["push"]) == 1  # push still fires
        assert agent.sent["email"] == []  # digest waits for 18:00

    def test_master_switch_off_sends_nothing(self, agent, monkeypatch):
        (agent.tmp_path / "config.yml").write_text(
            CONFIG.replace("enabled: true", "enabled: false", 1), encoding="utf-8"
        )
        monkeypatch.setattr(run_module, "datetime", _clock(hour=18))
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
        monkeypatch.setattr(run_module, "datetime", _clock(hour=18))

        def boom(*args, **kwargs):
            raise RuntimeError("adzuna exploded in a new and exciting way")

        monkeypatch.setattr(run_module.adzuna, "fetch", boom)
        monkeypatch.setattr("sys.argv", ["run.py", "--force"])

        assert run_module.main() == 1
        assert len(agent.sent["failure"]) == 1


class TestDryRun:
    def test_sends_nothing_and_writes_nothing(self, agent, monkeypatch):
        monkeypatch.setattr(run_module, "datetime", _clock(hour=18))
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


def _clock(hour):
    """A stand-in datetime whose .now() reports a fixed hour."""

    class _Clock:
        @staticmethod
        def now():
            from datetime import datetime as real

            return real(2026, 7, 27, hour, 0, 0)

    return _Clock
