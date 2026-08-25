"""Tests for the change-detection layer.

These walk a fixture's real lifecycle: it appears on the page, its ballot
opens, the ballot closes, and it eventually drops off the feed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chelsea.detect import REMINDER_AFTER, detect, due_reminders, window_alert_key
from chelsea.model import CLOSED, OPEN, SOLD_OUT, HomeFixture, SaleWindow


def window(key: str = "ticket-application-window-open", state: str = OPEN) -> SaleWindow:
    return SaleWindow(
        key=key,
        title="Ticket Application Window Open",
        on_sale_label="On-sale: Aug 21, 2026",
        state=state,
    )


def additional_tickets_window(state: str = OPEN) -> SaleWindow:
    # A real Chelsea window: no "application" in the title, no end date of
    # its own -- distinct from the ballot, whose close date must not be
    # attached to this window's alert.
    return SaleWindow(
        key="additional-tickets",
        title="Season ticket holders & Members can purchase an additional four tickets",
        on_sale_label="On-sale: Now",
        state=state,
    )


def fixture(
    entry_id: str = "hull1",
    opponent: str = "Hull City",
    windows: tuple[SaleWindow, ...] = (),
    closes: str = "Wednesday 26 August 12pm",
) -> HomeFixture:
    return HomeFixture(
        id=entry_id,
        opponent=opponent,
        competition="Premier League",
        date="Sat 12 Sept 2026",
        time="3:00 pm",
        venue="Stamford Bridge",
        windows=windows,
        application_opens="Friday 21 August 12pm",
        application_closes=closes,
    )


def snapshot(*fixtures: HomeFixture) -> dict:
    return {
        f.id: {"opponent": f.opponent, "windows": {w.key: w.state for w in f.windows}}
        for f in fixtures
    }


class TestFirstRun:
    def test_unseeded_run_stays_silent(self):
        # Arrange -- with no baseline, every listed fixture looks brand new
        current = [fixture(), fixture(entry_id="luton", opponent="Luton Town")]

        # Act
        alerts = detect(previous={}, current=current, seeded=False)

        # Assert
        assert alerts == []


class TestNewFixture:
    def test_new_fixture_without_open_windows_alerts_once(self):
        # Arrange
        before = snapshot(fixture(entry_id="luton", opponent="Luton Town"))
        current = [fixture(entry_id="luton", opponent="Luton Town"), fixture()]

        # Act
        alerts = detect(before, current, seeded=True)

        # Assert
        assert len(alerts) == 1
        assert alerts[0].is_new
        assert alerts[0].newly_open == ()
        assert "New home fixture listed" in alerts[0].title

    def test_new_fixture_is_not_urgent(self):
        # A newly listed fixture is a heads-up, not an act-now event.
        alerts = detect(snapshot(fixture(entry_id="other")), [fixture()], seeded=True)
        assert alerts[0].priority == "default"

    def test_new_fixture_message_carries_the_application_dates(self):
        alerts = detect(snapshot(fixture(entry_id="other")), [fixture()], seeded=True)
        assert "Friday 21 August 12pm" in alerts[0].message

    def test_new_fixture_says_so_when_no_dates_published_yet(self):
        # Arrange
        bare = fixture(closes="")
        bare = HomeFixture(**{**bare.__dict__, "application_opens": ""})

        # Act
        alerts = detect(snapshot(fixture(entry_id="other")), [bare], seeded=True)

        # Assert
        assert "No application dates published yet" in alerts[0].message


class TestWindowOpening:
    def test_window_opening_on_a_known_fixture_is_urgent(self):
        # Arrange -- the ballot was closed last run, it is open now
        before = snapshot(fixture(windows=(window(state=CLOSED),)))
        current = [fixture(windows=(window(state=OPEN),))]

        # Act
        alerts = detect(before, current, seeded=True)

        # Assert
        assert len(alerts) == 1
        assert alerts[0].priority == "urgent"
        assert not alerts[0].is_new
        assert "applications OPEN" in alerts[0].title

    def test_open_alert_includes_the_closing_deadline(self):
        before = snapshot(fixture(windows=(window(state=CLOSED),)))
        alerts = detect(before, [fixture(windows=(window(state=OPEN),))], seeded=True)
        assert "Applications close: Wednesday 26 August 12pm" in alerts[0].message

    def test_window_staying_open_does_not_re_alert(self):
        # Arrange -- same open state on both runs
        before = snapshot(fixture(windows=(window(state=OPEN),)))
        current = [fixture(windows=(window(state=OPEN),))]

        # Act / Assert
        assert detect(before, current, seeded=True) == []

    def test_window_closing_is_silent(self):
        # Harry asked to hear about openings only.
        before = snapshot(fixture(windows=(window(state=OPEN),)))
        current = [fixture(windows=(window(state=CLOSED),))]
        assert detect(before, current, seeded=True) == []

    def test_selling_out_is_silent(self):
        before = snapshot(fixture(windows=(window(state=OPEN),)))
        current = [fixture(windows=(window(state=SOLD_OUT),))]
        assert detect(before, current, seeded=True) == []

    def test_reopening_after_closing_alerts_again(self):
        # Arrange -- Chelsea occasionally releases a second batch
        before = snapshot(fixture(windows=(window(state=CLOSED),)))
        current = [fixture(windows=(window(state=OPEN),))]

        # Act / Assert
        assert len(detect(before, current, seeded=True)) == 1

    def test_non_ballot_window_opening_does_not_claim_the_ballots_deadline(self):
        # Regression: an "additional tickets" window opening (no deadline of
        # its own) must not state the real ballot's closing date, which is
        # unrelated to it and can already be well in the past.
        before = snapshot(fixture())
        current = [fixture(windows=(additional_tickets_window(),))]

        alerts = detect(before, current, seeded=True)

        assert len(alerts) == 1
        assert "Applications close" not in alerts[0].message

    def test_ballot_window_opening_alongside_a_non_ballot_one_still_shows_the_deadline(self):
        # Arrange -- both open in the same run; the real ballot's deadline
        # is still worth stating because one of the opened windows is it.
        before = snapshot(fixture())
        current = [fixture(windows=(window(state=OPEN), additional_tickets_window()))]

        alerts = detect(before, current, seeded=True)

        assert "Applications close: Wednesday 26 August 12pm" in alerts[0].message


class TestNewFixtureArrivingAlreadyOpen:
    def test_produces_a_single_combined_alert_not_two(self):
        # Arrange -- polling every 30 min, a fixture can first be seen with
        # its ballot already live. Two notifications for one event is noise.
        before = snapshot(fixture(entry_id="other", opponent="Luton Town"))
        current = [fixture(windows=(window(state=OPEN),))]

        # Act
        alerts = detect(before, current, seeded=True)

        # Assert
        assert len(alerts) == 1
        assert alerts[0].is_new and alerts[0].newly_open
        assert alerts[0].title == "New fixture + applications OPEN: Chelsea v Hull City"

    def test_combined_alert_is_urgent(self):
        before = snapshot(fixture(entry_id="other"))
        alerts = detect(before, [fixture(windows=(window(state=OPEN),))], seeded=True)
        assert alerts[0].priority == "urgent"

    def test_records_an_audit_key_for_each_event_it_covers(self):
        # Arrange
        before = snapshot(fixture(entry_id="other"))

        # Act
        alert = detect(before, [fixture(windows=(window(state=OPEN),))], seeded=True)[0]

        # Assert
        assert set(alert.keys) == {
            "hull1::new_fixture",
            "hull1::window_open::ticket-application-window-open",
        }


class TestMultipleWindows:
    def test_two_windows_opening_at_once_group_into_one_alert(self):
        # Arrange
        members = window("members-application", CLOSED)
        additional = window("additional-tickets", CLOSED)
        before = snapshot(fixture(windows=(members, additional)))
        current = [fixture(windows=(
            window("members-application", OPEN),
            window("additional-tickets", OPEN),
        ))]

        # Act
        alerts = detect(before, current, seeded=True)

        # Assert
        assert len(alerts) == 1
        assert len(alerts[0].newly_open) == 2
        assert len(alerts[0].keys) == 2

    def test_only_the_newly_opened_window_is_reported(self):
        # Arrange -- one already open, one just opened
        before = snapshot(fixture(windows=(
            window("members-application", OPEN),
            window("additional-tickets", CLOSED),
        )))
        current = [fixture(windows=(
            window("members-application", OPEN),
            window("additional-tickets", OPEN),
        ))]

        # Act
        alerts = detect(before, current, seeded=True)

        # Assert
        assert [w.key for w in alerts[0].newly_open] == ["additional-tickets"]


class TestNoiseControl:
    def test_unchanged_feed_produces_nothing(self):
        state = snapshot(fixture(windows=(window(state=OPEN),)))
        assert detect(state, [fixture(windows=(window(state=OPEN),))], seeded=True) == []

    def test_fixture_disappearing_from_the_feed_is_silent(self):
        # Chelsea drops a fixture once it has been played.
        before = snapshot(fixture(), fixture(entry_id="luton", opponent="Luton Town"))
        assert detect(before, [fixture()], seeded=True) == []

    @pytest.mark.parametrize("state", [CLOSED, SOLD_OUT, "unknown"])
    def test_non_open_states_never_alert_on_a_known_fixture(self, state):
        before = snapshot(fixture(windows=(window(state=CLOSED),)))
        current = [fixture(windows=(window(state=state),))]
        assert detect(before, current, seeded=True) == []


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
KEY = window_alert_key("hull1", "ticket-application-window-open")


class TestReminders:
    """The one-off 6h follow-up in case the primary alert gets missed."""

    def test_no_reminder_before_the_threshold(self):
        open_since = {KEY: (NOW - REMINDER_AFTER + timedelta(minutes=1)).isoformat()}
        current = [fixture(windows=(window(state=OPEN),))]

        kept, reminders = due_reminders(open_since, current, NOW)

        assert reminders == []
        assert kept == open_since

    def test_reminder_fires_once_the_threshold_is_reached(self):
        open_since = {KEY: (NOW - REMINDER_AFTER).isoformat()}
        current = [fixture(windows=(window(state=OPEN),))]

        kept, reminders = due_reminders(open_since, current, NOW)

        assert len(reminders) == 1
        assert reminders[0].fixture.opponent == "Hull City"
        assert reminders[0].priority == "urgent"
        assert "still OPEN" in reminders[0].title

    def test_key_is_dropped_once_the_reminder_fires(self):
        # So a second poll later the same day does not remind again.
        open_since = {KEY: (NOW - REMINDER_AFTER).isoformat()}
        current = [fixture(windows=(window(state=OPEN),))]

        kept, _ = due_reminders(open_since, current, NOW)

        assert kept == {}

    def test_key_is_dropped_once_the_window_closes(self):
        # The clock stops the moment there is nothing left to remind about.
        open_since = {KEY: (NOW - timedelta(minutes=5)).isoformat()}
        current = [fixture(windows=(window(state=CLOSED),))]

        kept, reminders = due_reminders(open_since, current, NOW)

        assert kept == {} and reminders == []

    def test_key_is_dropped_when_the_fixture_disappears(self):
        open_since = {KEY: (NOW - timedelta(minutes=5)).isoformat()}

        kept, reminders = due_reminders(open_since, current=[], now=NOW)

        assert kept == {} and reminders == []

    def test_reminder_message_includes_the_closing_deadline(self):
        open_since = {KEY: (NOW - REMINDER_AFTER).isoformat()}
        current = [fixture(windows=(window(state=OPEN),))]

        _, reminders = due_reminders(open_since, current, NOW)

        assert "Wednesday 26 August 12pm" in reminders[0].message

    def test_reminder_for_a_non_ballot_window_omits_the_ballots_deadline(self):
        # Regression: the same misattribution bug as the primary alert --
        # an "additional tickets" reminder must not state the ballot's
        # closing date, which is unrelated and can already be well in the past.
        key = window_alert_key("hull1", "additional-tickets")
        open_since = {key: (NOW - REMINDER_AFTER).isoformat()}
        current = [fixture(windows=(additional_tickets_window(),))]

        _, reminders = due_reminders(open_since, current, NOW)

        assert len(reminders) == 1
        assert "Applications close" not in reminders[0].message

    def test_untracked_open_windows_are_never_reminded(self):
        # A window that was already open before the primary alert ever fired
        # for it (e.g. an always-open "additional tickets" window) must not
        # spontaneously start a clock -- only `run()` adds keys, on send.
        current = [fixture(windows=(window(state=OPEN),))]

        kept, reminders = due_reminders(open_since={}, current=current, now=NOW)

        assert kept == {} and reminders == []
