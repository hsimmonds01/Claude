"""End-to-end tests for the watcher, driving the real CLI entry point.

Network and ntfy are mocked; everything else -- parsing, diffing, one-shot
keying, state persistence -- is the real code path. The main test walks a
fixture's actual lifecycle across consecutive runs.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import check_tickets
from chelsea import notify
from chelsea.api import FetchError
from chelsea.detect import REMINDER_AFTER

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


class TestPlaceholderWindows:
    """End to end: a dateless placeholder must not look like an open ballot."""

    def placeholder(self, feed, label):
        entry = find_entry(feed, HULL_ID)
        for ticket in entry["tickets"]:
            ticket.pop("status", None)
            ticket["label"] = label
        return feed

    def test_placeholder_stays_silent_then_the_real_opening_alerts(self, watcher, feed):
        # --- Run 1: baseline with Hull listed as a dateless placeholder ----
        watcher.set_feed(self.placeholder(copy.deepcopy(feed), "TO BE ANNOUNCED"))
        watcher.run()
        assert watcher.sent == []

        # --- Run 2: nothing changed -- a placeholder is not an open ballot -
        watcher.run()
        assert watcher.sent == [], "a placeholder must not read as OPEN"

        # --- Run 3: Chelsea gives the window a real date and it goes live --
        # (regression: with the placeholder stored as OPEN this was OPEN ->
        #  OPEN, so the real opening never alerted)
        watcher.set_feed(self.placeholder(copy.deepcopy(feed), "On-sale: Now"))
        watcher.run()
        assert len(watcher.sent) == 1
        assert watcher.sent[0]["priority"] == "urgent"
        assert "applications OPEN" in watcher.sent[0]["title"]

    def test_a_fixture_first_listed_as_a_placeholder_is_only_a_heads_up(self, watcher, feed):
        # Arrange -- baseline without Hull, then Hull appears as a placeholder
        watcher.set_feed(drop_entry(copy.deepcopy(feed), HULL_ID))
        watcher.run()

        # Act
        watcher.set_feed(self.placeholder(copy.deepcopy(feed), ""))
        watcher.run()

        # Assert
        assert len(watcher.sent) == 1
        assert watcher.sent[0]["priority"] == "default"
        assert "New home fixture listed" in watcher.sent[0]["title"]

    def test_placeholders_never_trigger_a_reminder(self, watcher, feed):
        # Arrange
        t0 = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
        watcher.set_feed(self.placeholder(copy.deepcopy(feed), "TO BE ANNOUNCED"))
        watcher.run(now=t0)

        # Act -- long after the reminder threshold
        watcher.run(now=t0 + REMINDER_AFTER + timedelta(hours=1))

        # Assert
        assert watcher.sent == []


class TestNewcastleLifecycle:
    """Replays the real Newcastle sequence: TBA -> early hint -> dated -> open -> reminder."""

    OPENS = "Monday 23 November 12:00pm"

    def setup_window(self, feed, label, hint=False):
        entry = find_entry(feed, HULL_ID)
        entry["tickets"] = [{"title": "Ticket Application Window", "label": label}]
        blob = ""
        if hint:
            blob = (f"Ticket application window opens - {self.OPENS} "
                    "Ticket application window closes - Thursday 26 November 12:00pm")
        entry["fullTicketInfoLink"] = {"content": blob}
        return feed

    def test_every_stage_is_surfaced_once_and_only_the_last_is_urgent(self, watcher, feed):
        fri = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
        mon = datetime(2026, 11, 23, 11, 30, tzinfo=timezone.utc)
        tba = lambda hint=False: self.setup_window(copy.deepcopy(feed), "TO BE ANNOUNCED", hint)
        dated = lambda: self.setup_window(copy.deepcopy(feed), "OPEN: NOV 23. 2026", hint=True)

        # --- Baseline: placeholder, nothing known -------------------------
        watcher.set_feed(tba())
        watcher.run(now=fri)
        assert watcher.sent == []

        # --- The details start hinting at a date before the label does -----
        watcher.set_feed(tba(hint=True))
        watcher.run(now=fri)
        assert len(watcher.sent) == 1
        assert "Early look" in watcher.sent[0]["title"]
        assert watcher.sent[0]["priority"] == "default"
        watcher.run(now=fri + timedelta(minutes=30))
        assert len(watcher.sent) == 1, "a hint must not repeat"

        # --- Chelsea gives the window a real date (the alert that used to lie)
        watcher.set_feed(dated())
        watcher.run(now=fri + timedelta(hours=1))
        assert len(watcher.sent) == 2
        assert "Dates confirmed" in watcher.sent[1]["title"]
        assert watcher.sent[1]["priority"] == "default", "not open yet, so not urgent"
        assert "OPEN NOW" not in watcher.sent[1]["message"]

        # --- Six hours on, still not open: no reminder, nothing to remind about
        watcher.run(now=fri + timedelta(hours=8))
        assert len(watcher.sent) == 2

        # --- Monday, just before noon: still silent -------------------------
        watcher.run(now=mon)
        assert len(watcher.sent) == 2

        # --- Monday 12:01: it opens -----------------------------------------
        opened = datetime(2026, 11, 23, 12, 1, tzinfo=timezone.utc)
        watcher.run(now=opened)
        assert len(watcher.sent) == 3
        assert "applications OPEN" in watcher.sent[2]["title"]
        assert watcher.sent[2]["priority"] == "urgent"

        # --- No repeat on the next poll, then the one 6-hour reminder -------
        watcher.run(now=opened + timedelta(minutes=30))
        assert len(watcher.sent) == 3
        watcher.run(now=opened + REMINDER_AFTER)
        assert len(watcher.sent) == 4
        assert "still OPEN" in watcher.sent[3]["title"]
        watcher.run(now=opened + REMINDER_AFTER + timedelta(hours=1))
        assert len(watcher.sent) == 4

    def test_state_stored_as_open_by_the_old_rule_migrates_silently(self, watcher, feed, tmp_path):
        # Newcastle was saved as "open" when it was really only dated.
        fri = datetime(2026, 11, 20, 16, 0, tzinfo=timezone.utc)
        watcher.set_feed(self.setup_window(copy.deepcopy(feed), "OPEN: NOV 23. 2026", hint=True))
        watcher.run(now=fri)
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        for fixture_state in state["fixtures"].values():
            fixture_state["windows"] = {k: "open" for k in fixture_state["windows"]}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        watcher.sent.clear()

        watcher.run(now=fri + timedelta(minutes=30))

        assert watcher.sent == []
        monday = datetime(2026, 11, 23, 12, 1, tzinfo=timezone.utc)
        watcher.run(now=monday)
        assert len(watcher.sent) == 1 and watcher.sent[0]["priority"] == "urgent"


class TestReminders:
    """The one-off 6h follow-up in case the primary alert gets missed."""

    T0 = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)

    def test_reminder_fires_once_the_window_has_been_open_6h(self, watcher, feed):
        # Arrange -- baseline, then Hull appears with its ballot already open
        watcher.set_feed(drop_entry(copy.deepcopy(feed), HULL_ID))
        watcher.run(now=self.T0)
        watcher.set_feed(feed)
        watcher.run(now=self.T0)
        assert len(watcher.sent) == 1, "primary alert should have fired"

        # Act -- just short of the threshold: nothing yet
        watcher.run(now=self.T0 + REMINDER_AFTER - timedelta(minutes=1))
        assert len(watcher.sent) == 1

        # Act -- threshold reached: exactly one reminder
        watcher.run(now=self.T0 + REMINDER_AFTER)

        # Assert
        assert len(watcher.sent) == 2
        reminder = watcher.sent[1]
        assert "still OPEN" in reminder["title"] and "Hull City" in reminder["title"]
        assert reminder["priority"] == "urgent"

    def test_reminder_does_not_repeat_on_later_polls(self, watcher, feed):
        # Arrange
        watcher.set_feed(drop_entry(copy.deepcopy(feed), HULL_ID))
        watcher.run(now=self.T0)
        watcher.set_feed(feed)
        watcher.run(now=self.T0)
        watcher.run(now=self.T0 + REMINDER_AFTER)
        assert len(watcher.sent) == 2

        # Act -- several more 30-minute polls, window still open
        for minutes in (30, 60, 90):
            watcher.run(now=self.T0 + REMINDER_AFTER + timedelta(minutes=minutes))

        # Assert -- still just the one reminder, not one per poll
        assert len(watcher.sent) == 2

    def test_no_reminder_if_the_window_closes_before_the_threshold(self, watcher, feed):
        # Arrange -- primary alert fires, then the window closes
        watcher.set_feed(drop_entry(copy.deepcopy(feed), HULL_ID))
        watcher.run(now=self.T0)
        watcher.set_feed(feed)
        watcher.run(now=self.T0)
        assert len(watcher.sent) == 1
        watcher.set_feed(close_window(copy.deepcopy(feed), HULL_ID))
        watcher.run(now=self.T0 + timedelta(hours=1))
        assert len(watcher.sent) == 1

        # Act -- well past the threshold, but nothing left to remind about
        watcher.run(now=self.T0 + REMINDER_AFTER + timedelta(hours=1))

        # Assert
        assert len(watcher.sent) == 1

    def test_reopening_after_reminding_can_remind_again(self, watcher, feed):
        # Arrange -- full cycle: open, remind, close, reopen
        watcher.set_feed(drop_entry(copy.deepcopy(feed), HULL_ID))
        watcher.run(now=self.T0)
        watcher.set_feed(feed)
        watcher.run(now=self.T0)
        watcher.run(now=self.T0 + REMINDER_AFTER)
        assert len(watcher.sent) == 2

        watcher.set_feed(close_window(copy.deepcopy(feed), HULL_ID))
        watcher.run(now=self.T0 + REMINDER_AFTER + timedelta(hours=1))
        t1 = self.T0 + REMINDER_AFTER + timedelta(hours=2)
        watcher.set_feed(feed)
        watcher.run(now=t1)
        assert len(watcher.sent) == 3, "second batch reopening should alert again"

        # Act -- 6h after the *second* open, not the first
        watcher.run(now=t1 + REMINDER_AFTER)

        # Assert
        assert len(watcher.sent) == 4


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
