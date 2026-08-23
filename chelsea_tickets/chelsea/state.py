"""Persisted snapshot of what the watcher saw last run.

GitHub Actions commits this file back to `main` after every run, so it is the
watcher's only memory between runs.

`fixtures` is the snapshot the next run diffs against -- it alone decides
whether something is worth alerting on. `notified` is an audit trail of what
was actually sent, kept for answering "did it alert for that game?" and
deliberately NOT used to suppress anything: an alert key like
`<id>::window_open::<window>` repeats legitimately when Chelsea closes a
window and re-opens it with a second batch.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .model import HomeFixture

SCHEMA_VERSION = 1


@dataclass
class State:
    # {fixture_id: {"opponent": str, "windows": {window_key: state}}}
    fixtures: dict[str, dict] = field(default_factory=dict)
    notified: dict[str, str] = field(default_factory=dict)
    # {window_alert_key: ISO timestamp the window was first seen open}. Only
    # ever holds keys the primary "applications OPEN" alert has already fired
    # for -- see `due_reminders` in detect.py. A key is removed the moment
    # either its one-off reminder fires or the window is no longer open,
    # whichever comes first, so a later reopen starts a fresh clock.
    open_since: dict[str, str] = field(default_factory=dict)
    consecutive_fetch_failures: int = 0
    fetch_failure_notified: bool = False
    schema_version: int = SCHEMA_VERSION

    @property
    def is_seeded(self) -> bool:
        """False on the very first run, when there is nothing to diff against.

        The first run must stay silent: every fixture already on the page
        would otherwise look brand new and fire an alert each.
        """
        return bool(self.fixtures)

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A corrupt state file must not wedge the watcher forever. Starting
            # clean costs one silent re-seed run, which is the safe failure.
            print(f"WARNING: could not read state ({exc}); starting from empty state")
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def has_notified(self, key: str) -> bool:
        return key in self.notified

    def mark_notified(self, key: str) -> None:
        self.notified[key] = datetime.now(timezone.utc).isoformat()

    def snapshot(self, fixtures: list[HomeFixture]) -> None:
        """Replace the stored snapshot with what this run saw."""
        self.fixtures = {
            fixture.id: {
                "opponent": fixture.opponent,
                "competition": fixture.competition,
                "date": fixture.date,
                "windows": {w.key: w.state for w in fixture.windows},
            }
            for fixture in fixtures
        }

    def prune_notified(self, live_fixture_ids: set[str]) -> None:
        """Drop alert keys for fixtures that have dropped off the feed.

        Chelsea removes a fixture once it has been played, so without this the
        file would grow for every game of every season.
        """
        self.notified = {
            key: value
            for key, value in self.notified.items()
            if key.split("::", 1)[0] in live_fixture_ids
        }
