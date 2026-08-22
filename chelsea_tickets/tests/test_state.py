"""Tests for the persisted snapshot and one-shot alert tracking."""

from __future__ import annotations

import json

from chelsea.model import OPEN, HomeFixture, SaleWindow
from chelsea.state import State


def fixture(entry_id: str = "hull1", opponent: str = "Hull City") -> HomeFixture:
    return HomeFixture(
        id=entry_id,
        opponent=opponent,
        competition="Premier League",
        date="Sat 12 Sept 2026",
        time="3:00 pm",
        venue="Stamford Bridge",
        windows=(SaleWindow(key="ballot", title="Ballot", on_sale_label="", state=OPEN),),
    )


class TestSeeding:
    def test_a_fresh_state_is_not_seeded(self):
        assert not State().is_seeded

    def test_state_is_seeded_once_a_snapshot_is_recorded(self):
        # Arrange
        state = State()

        # Act
        state.snapshot([fixture()])

        # Assert
        assert state.is_seeded


class TestRoundTrip:
    def test_saving_and_loading_preserves_the_snapshot(self, tmp_path):
        # Arrange
        path = tmp_path / "state.json"
        state = State()
        state.snapshot([fixture()])
        state.mark_notified("hull1::new_fixture")

        # Act
        state.save(path)
        reloaded = State.load(path)

        # Assert
        assert reloaded.fixtures["hull1"]["windows"] == {"ballot": OPEN}
        assert reloaded.has_notified("hull1::new_fixture")

    def test_loading_a_missing_file_gives_empty_state(self, tmp_path):
        assert State.load(tmp_path / "nope.json").fixtures == {}

    def test_corrupt_state_file_does_not_crash_the_watcher(self, tmp_path):
        # Arrange -- a half-written commit must not wedge the watcher forever
        path = tmp_path / "state.json"
        path.write_text("{not json", encoding="utf-8")

        # Act
        state = State.load(path)

        # Assert
        assert state.fixtures == {} and not state.is_seeded

    def test_unknown_keys_in_the_file_are_ignored(self, tmp_path):
        # Arrange -- lets the schema gain fields without breaking old files
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"fixtures": {}, "somethingNew": 1}), encoding="utf-8")

        # Act / Assert
        assert State.load(path).fixtures == {}


class TestPruning:
    def test_alert_keys_for_played_fixtures_are_dropped(self):
        # Arrange -- Chelsea removes a fixture from the feed once it is played
        state = State()
        state.mark_notified("hull1::new_fixture")
        state.mark_notified("gone99::new_fixture")

        # Act
        state.prune_notified({"hull1"})

        # Assert
        assert state.has_notified("hull1::new_fixture")
        assert not state.has_notified("gone99::new_fixture")

    def test_pruning_keeps_every_key_for_a_live_fixture(self):
        # Arrange
        state = State()
        state.mark_notified("hull1::new_fixture")
        state.mark_notified("hull1::window_open::ballot")

        # Act
        state.prune_notified({"hull1"})

        # Assert
        assert len(state.notified) == 2


class TestSnapshotContents:
    def test_snapshot_records_window_states_for_diffing(self):
        # Arrange
        state = State()

        # Act
        state.snapshot([fixture(), fixture(entry_id="luton", opponent="Luton Town")])

        # Assert
        assert set(state.fixtures) == {"hull1", "luton"}
        assert state.fixtures["luton"]["opponent"] == "Luton Town"

    def test_snapshot_replaces_rather_than_accumulates(self):
        # Arrange -- otherwise played fixtures would linger and never re-alert
        state = State()
        state.snapshot([fixture(), fixture(entry_id="old")])

        # Act
        state.snapshot([fixture()])

        # Assert
        assert set(state.fixtures) == {"hull1"}
