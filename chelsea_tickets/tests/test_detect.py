"""Tests for the change-detection layer.

These walk a fixture's real lifecycle: it appears on the page, its ballot
opens, the ballot closes, and it eventually drops off the feed.
"""

from __future__ import annotations

import pytest

from chelsea.detect import detect
from chelsea.model import CLOSED, OPEN, SOLD_OUT, HomeFixture, SaleWindow


def window(key: str = "ticket-application-window-open", state: str = OPEN) -> SaleWindow:
    return SaleWindow(
        key=key,
        title="Ticket Application Window Open",
        on_sale_label="On-sale: Aug 21, 2026",
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
