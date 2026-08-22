"""Tests for parsing Chelsea's ticket feed into home fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chelsea.model import (
    CLOSED,
    OPEN,
    SOLD_OUT,
    UNKNOWN,
    FeedError,
    is_relevant_window,
    parse_application_dates,
    parse_home_fixtures,
    strip_html,
)

REAL_RESPONSE = Path(__file__).parent.parent / "fixtures" / "api-response-2026-08-22.json"


def make_feed(*entries: dict) -> dict:
    return {"items": [{"monthName": "August", "year": 2026, "items": list(entries)}]}


def make_entry(
    entry_id: str = "abc123",
    opponent: str = "Hull City",
    venue: str = "Stamford Bridge",
    is_opposition_home: bool = False,
    tickets: list | None = None,
    info: str = "",
) -> dict:
    return {
        "id": entry_id,
        "fixture": {
            "home": {"name": "Chelsea", "isOpposition": is_opposition_home},
            "away": {"name": opponent, "isOpposition": True},
            "competition": "Premier League",
            "date": "Sat 12 Sept 2026",
            "time": "3:00 pm",
            "venue": venue,
        },
        "tickets": tickets if tickets is not None else [],
        "fullTicketInfoLink": {"content": info},
    }


class TestHomeFiltering:
    def test_keeps_chelsea_home_games_at_stamford_bridge(self):
        # Arrange
        feed = make_feed(make_entry(opponent="Hull City"))

        # Act
        fixtures = parse_home_fixtures(feed)

        # Assert
        assert [f.opponent for f in fixtures] == ["Hull City"]

    def test_drops_away_games(self):
        # Arrange -- an away game has Chelsea listed as the opposition side
        feed = make_feed(
            make_entry(opponent="Fulham", venue="Craven Cottage", is_opposition_home=True)
        )

        # Act / Assert
        assert parse_home_fixtures(feed) == []

    def test_drops_neutral_venue_games_even_when_chelsea_are_nominally_home(self):
        # Arrange -- a Wembley final is not the home game Harry is watching for
        feed = make_feed(make_entry(opponent="Arsenal", venue="Wembley Stadium"))

        # Act / Assert
        assert parse_home_fixtures(feed) == []


class TestWindowState:
    def test_missing_status_means_the_window_is_open(self):
        # Arrange -- Chelsea attaches no status object while a window is live
        feed = make_feed(make_entry(tickets=[{"title": "Ticket Application Window Open"}]))

        # Act
        window = parse_home_fixtures(feed)[0].windows[0]

        # Assert
        assert window.state == OPEN
        assert window.is_open

    def test_off_sale_status_means_closed(self):
        # Arrange
        feed = make_feed(make_entry(tickets=[
            {"title": "Members Ticket Application Window",
             "status": {"text": "Off Sale", "colour": "red"}},
        ]))

        # Act / Assert
        assert parse_home_fixtures(feed)[0].windows[0].state == CLOSED

    def test_sold_out_status_is_distinct_from_closed(self):
        # Arrange
        feed = make_feed(make_entry(tickets=[
            {"title": "Members can purchase one ticket",
             "status": {"text": "Sold Out", "colour": "red"}},
        ]))

        # Act / Assert
        assert parse_home_fixtures(feed)[0].windows[0].state == SOLD_OUT

    def test_unrecognised_status_is_unknown_not_open(self):
        # Arrange -- guessing "open" on unfamiliar wording would fire a false alert
        feed = make_feed(make_entry(tickets=[
            {"title": "Members application", "status": {"text": "Paused"}},
        ]))

        # Act / Assert
        assert parse_home_fixtures(feed)[0].windows[0].state == UNKNOWN


class TestWindowRelevance:
    @pytest.mark.parametrize("title", [
        "Ticket Application Window Open",
        "Members Ticket Application Window",
        "Season ticket holders & Members can purchase an additional two tickets",
    ])
    def test_members_and_application_windows_are_relevant(self, title):
        assert is_relevant_window(title, is_club_chelsea=False)

    @pytest.mark.parametrize("title", [
        "Ticket Exchange",
        "Club Chelsea Packages",
        "Season ticket holders can purchase one ticket per person (146 Loyalty Points)",
    ])
    def test_irrelevant_windows_are_excluded(self, title):
        # Harry is a True Blue member, not a season ticket holder, and has
        # explicitly ruled out Ticket Exchange and hospitality.
        assert not is_relevant_window(title, is_club_chelsea=False)

    def test_club_chelsea_flag_excludes_even_member_wording(self):
        assert not is_relevant_window("Members hospitality", is_club_chelsea=True)


class TestWindowKeys:
    def test_loyalty_point_edits_do_not_change_the_window_key(self):
        # Arrange -- Chelsea edits these thresholds between fixtures; if the
        # key moved, an edit would masquerade as a brand-new open window.
        a = make_feed(make_entry(tickets=[{"title": "Members with 117 loyalty points"}]))
        b = make_feed(make_entry(tickets=[{"title": "Members with 112 loyalty points"}]))

        # Act
        key_a = parse_home_fixtures(a)[0].windows[0].key
        key_b = parse_home_fixtures(b)[0].windows[0].key

        # Assert
        assert key_a == key_b


class TestApplicationDates:
    def test_extracts_open_and_close_datetimes(self):
        # Arrange -- tags stripped, this renders as one continuous line
        blob = (
            "<p>Ticket application window opens &#8211; Friday 21 August 12pm</p>"
            "<p>Ticket application window closes &#8211; Wednesday 26 August 12pm</p>"
            "<p>The window is open for True Blue members.</p>"
        )

        # Act
        opens, closes = parse_application_dates(blob)

        # Assert
        assert opens == "Friday 21 August 12pm"
        assert closes == "Wednesday 26 August 12pm"

    def test_does_not_bleed_the_following_sentence_into_the_date(self):
        # Arrange -- this exact case produced "Thursday 20 August 12pm Season"
        blob = (
            "Ticket application window closes - Thursday 20 August 12pm "
            "Season ticket holders can purchase from 4pm."
        )

        # Act
        _, closes = parse_application_dates(blob)

        # Assert
        assert closes == "Thursday 20 August 12pm"

    def test_returns_empty_strings_when_no_window_is_described(self):
        # Arrange -- normal for a fixture with no ballot; must not raise
        opens, closes = parse_application_dates("<p>Tickets are Category B.</p>")

        # Assert
        assert (opens, closes) == ("", "")

    def test_strip_html_undoes_double_escaping(self):
        assert strip_html("&lt;p&gt;Hello&lt;/p&gt;") == "Hello"


class TestMalformedFeeds:
    def test_missing_items_key_raises_rather_than_returning_nothing(self):
        # A silent empty result would look identical to "no fixtures listed",
        # and the watcher would never alert again.
        with pytest.raises(FeedError):
            parse_home_fixtures({"competitions": []})

    def test_items_of_wrong_type_raises(self):
        with pytest.raises(FeedError):
            parse_home_fixtures({"items": "nope"})

    def test_entries_without_an_id_are_skipped_not_fatal(self):
        # Arrange
        broken = make_entry()
        del broken["id"]
        feed = make_feed(broken, make_entry(entry_id="good", opponent="Hull City"))

        # Act
        fixtures = parse_home_fixtures(feed)

        # Assert
        assert [f.id for f in fixtures] == ["good"]


class TestAgainstTheRealSavedResponse:
    """Guards against regressions using a real captured API response."""

    @pytest.fixture
    def fixtures(self):
        return parse_home_fixtures(json.loads(REAL_RESPONSE.read_text(encoding="utf-8")))

    def test_finds_the_three_home_fixtures(self, fixtures):
        assert {f.opponent for f in fixtures} == {
            "Luton Town", "Brighton & Hove Albion", "Hull City",
        }

    def test_hull_city_application_window_is_open_with_a_close_date(self, fixtures):
        hull = next(f for f in fixtures if f.opponent == "Hull City")
        assert hull.application_closes == "Wednesday 26 August 12pm"
        assert [w.state for w in hull.windows] == [OPEN]

    def test_luton_members_window_reads_as_closed(self, fixtures):
        luton = next(f for f in fixtures if f.opponent == "Luton Town")
        members = luton.window("members-ticket-application-window")
        assert members is not None and members.state == CLOSED

    def test_brighton_ticket_exchange_is_filtered_out(self, fixtures):
        brighton = next(f for f in fixtures if f.opponent == "Brighton & Hove Albion")
        assert brighton.windows == ()
