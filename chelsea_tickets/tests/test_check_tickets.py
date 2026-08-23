"""End-to-end tests for the watcher, driving the real CLI entry point.

Network and ntfy are mocked; everything else -- parsing, diffing, one-shot
keying, state persistence -- is the real code path. The main test walks a
fixture's actual lifecycle across consecutive runs.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import check_tickets
from chelsea import notify
from chelsea.api import FetchError

REAL_RESPONSE = Path(__file__).parent.parent / "fixtures" / "api-response-2026-08-22.json"
HULL_ID = "6rixcGFDEWH5V98iSqSPLa"


@pytest.fixture
def feed():
    return json.loads(REAL_RESPONSE.read_text(encoding="utf-8"))


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    """A watcher wired to a temp state file, with sends captured."""
    sent: list[dict] = []
    payload: dict = {}

    monkeypatch.setattr(check_tickets, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(
        notify, "send",
        lambda title, message, priority="default", tags="": sent.append(
            {"title": title, "message": message, "priority": priority}
        ),
    )

    def set_feed(new_payload):
        payload.clear()
        payload.update(copy.deepcopy(new_payload))

    monkeypatch.setattr(check_tickets, "fetch_ticket_feed", lambda *a, **k: payload)

    # SimpleNamespace rather than a class body: inside a `class`, the name on
    # the right of `sent = sent` does not resolve against the enclosing
    # function's scope, so the class-body version raises NameError.
    return SimpleNamespace(
        sent=sent,
        set_feed=set_feed,
        run=lambda **kwargs: check_tickets.run(**kwargs),
        state=lambda: json.loads((tmp_path / "state.json").read_text(encoding="utf-8")),
    )


def find_entry(payload: dict, entry_id: str) -> dict:
    for group in payload["items"]:
        for entry in group["items"]:
            if entry["id"] == entry_id:
                return entry
    raise AssertionError(f"{entry_id} not in payload")


def close_window(payload: dict, entry_id: str) -> dict:
    """Mark every sale window on a fixture as closed."""
    for ticket in find_entry(payload, entry_id)["tickets"]:
        ticket["status"] = {"text": "Off Sale", "colour": "red"}
    return payload


def drop_entry(payload: dict, entry_id: str) -> dict:
    for group in payload["items"]:
        group["items"] = [e for e in group["items"] if e["id"] != entry_id]
    return payload


class TestLifecycle:
    def test_full_fixture_lifecycle_across_consecutive_runs(self, watcher, feed):
        # --- Run 1: first ever run seeds a baseline and stays silent -------
        without_hull = drop_entry(copy.deepcopy(feed), HULL_ID)
        watcher.set_feed(without_hull)
        assert watcher.run() == 0
        assert watcher.sent == [], "first run must not alert on existing fixtures"

        # --- Run 2: nothing changed ---------------------------------------
        assert watcher.run() == 0
        assert watcher.sent == []

        # --- Run 3: Hull appears, ballot already open ----------------------
        watcher.set_feed(feed)
        watcher.run()
        assert len(watcher.sent) == 1, "one combined alert, not one per event"
        alert = watcher.sent[0]
        assert alert["priority"] == "urgent"
        assert "Hull City" in alert["title"]
        assert "Wednesday 26 August 12pm" in alert["message"]

        # --- Run 4: same feed again, must not repeat ----------------------
        watcher.run()
        assert len(watcher.sent) == 1, "an unchanged feed must not re-alert"

        # --- Run 5: the window closes -- silent by design ------------------
        watcher.set_feed(close_window(copy.deepcopy(feed), HULL_ID))
        watcher.run()
        assert len(watcher.sent) == 1

        # --- Run 6: a second batch opens -- must alert again ---------------
        # (regression: an audit key was once used as a suppression gate here,
        #  which permanently swallowed re-opened windows)
        watcher.set_feed(feed)
        watcher.run()
        assert len(watcher.sent) == 2
        assert watcher.sent[1]["priority"] == "urgent"

        # --- Run 7: fixture played and removed; keys pruned ---------------
        watcher.set_feed(drop_entry(copy.deepcopy(feed), HULL_ID))
        watcher.run()
        assert len(watcher.sent) == 2
        assert not any(k.startswith(HULL_ID) for k in watcher.state()["notified"])


class TestDryRun:
    def test_dry_run_sends_nothing_and_writes_no_state(self, watcher, feed, tmp_path):
        # Arrange
        watcher.set_feed(drop_entry(copy.deepcopy(feed), HULL_ID))
        watcher.run()
        watcher.sent.clear()
        before = watcher.state()

        # Act
        watcher.set_feed(feed)
        watcher.run(dry_run=True)

        # Assert
        assert watcher.sent == []
        assert watcher.state() == before

    def test_dry_run_still_reports_what_it_would_send(self, watcher, feed, capsys):
        # Arrange
        watcher.set_feed(drop_entry(copy.deepcopy(feed), HULL_ID))
        watcher.run()

        # Act
        watcher.set_feed(feed)
        watcher.run(dry_run=True)

        # Assert
        assert "DRY RUN" in capsys.readouterr().out


class TestRecon:
    def test_recon_writes_no_state_and_sends_nothing(self, watcher, feed, tmp_path):
        # Arrange
        watcher.set_feed(feed)

        # Act
        assert watcher.run(recon=True) == 0

        # Assert
        assert watcher.sent == []
        assert not (tmp_path / "state.json").exists()

    def test_recon_lists_the_home_fixtures(self, watcher, feed, capsys):
        # Arrange
        watcher.set_feed(feed)

        # Act
        watcher.run(recon=True)

        # Assert -- away games must not appear
        out = capsys.readouterr().out
        assert "Hull City" in out and "Fulham" not in out


class TestFetchFailures:
    @pytest.fixture
    def failing(self, watcher, monkeypatch):
        monkeypatch.setattr(
            check_tickets, "fetch_ticket_feed",
            lambda *a, **k: (_ for _ in ()).throw(FetchError("network down")),
        )
        return watcher

    def test_a_single_blip_does_not_alert(self, failing):
        # Act
        assert failing.run() == 1

        # Assert -- transient failures are normal and must stay quiet
        assert failing.sent == []

    def test_repeated_failures_warn_that_the_watch_is_blind(self, failing):
        # Act
        for _ in range(check_tickets.FETCH_FAILURES_BEFORE_ALERT):
            failing.run()

        # Assert
        assert len(failing.sent) == 1
        assert failing.sent[0]["priority"] == "high"

    def test_the_blind_warning_is_not_repeated(self, failing):
        # Act
        for _ in range(check_tickets.FETCH_FAILURES_BEFORE_ALERT + 3):
            failing.run()

        # Assert
        assert len(failing.sent) == 1

    def test_recovery_is_announced_once_the_feed_returns(self, watcher, feed, monkeypatch):
        # Arrange -- fail until the watcher declares itself blind
        monkeypatch.setattr(
            check_tickets, "fetch_ticket_feed",
            lambda *a, **k: (_ for _ in ()).throw(FetchError("down")),
        )
        for _ in range(check_tickets.FETCH_FAILURES_BEFORE_ALERT):
            watcher.run()
        watcher.sent.clear()

        # Act
        monkeypatch.setattr(check_tickets, "fetch_ticket_feed", lambda *a, **k: feed)
        watcher.run()

        # Assert
        assert len(watcher.sent) == 1
        assert "back online" in watcher.sent[0]["title"]


class TestFeedShapeChange:
    def test_a_changed_feed_shape_warns_rather_than_failing_silently(self, watcher):
        # Arrange -- retrying will not fix this, and silence would look
        # identical to "no fixtures listed"
        watcher.set_feed({"unexpected": "shape"})

        # Act
        assert watcher.run() == 1

        # Assert
        assert len(watcher.sent) == 1
        assert watcher.sent[0]["priority"] == "high"
        assert "needs attention" in watcher.sent[0]["title"]


class TestCliWiring:
    def test_test_notification_flag_sends_a_labelled_alert(self, monkeypatch):
        # Arrange
        sent = []
        monkeypatch.setattr(notify, "send", lambda t, m, priority="", tags="": sent.append(t))
        monkeypatch.setattr("sys.argv", ["check_tickets.py", "--test-notification"])

        # Act
        assert check_tickets.main() == 0

        # Assert
        assert sent and sent[0].startswith("TEST:")
