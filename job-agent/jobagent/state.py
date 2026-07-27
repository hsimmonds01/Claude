"""Run-level state: how many pushes have gone today, when the last digest went.

Kept separate from seen.json deliberately. That file is the long-lived memory
of every role ever judged; this one is small, churns daily, and losing it is
harmless. Mixing them would mean rewriting the big file for a counter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class RunState:
    push_date: str = ""
    push_count: int = 0
    digest_date: str = ""
    # Which digest hours have already gone today. Tracked per hour, not per
    # day: the config sends two digests (7am and 6pm), and a day-level guard
    # meant the morning one silently blocked the evening one forever.
    digest_hours: tuple[int, ...] = ()

    @classmethod
    def load(cls, path: str | Path) -> RunState:
        file = Path(path)
        if not file.exists():
            return cls()
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls()  # a lost counter costs one extra push at worst

        hours = []
        for value in raw.get("digest_hours", []) or []:
            try:
                hours.append(int(value))
            except (TypeError, ValueError):
                continue

        # Same reasoning as the JSONDecodeError above: valid JSON can still
        # hold a nonsense count after a hand edit or a bad merge, and losing
        # the counter is far cheaper than crashing the run.
        try:
            push_count = int(raw.get("push_count", 0) or 0)
        except (TypeError, ValueError):
            push_count = 0

        return cls(
            push_date=str(raw.get("push_date", "")),
            push_count=push_count,
            digest_date=str(raw.get("digest_date", "")),
            digest_hours=tuple(hours),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "push_date": self.push_date,
                    "push_count": self.push_count,
                    "digest_date": self.digest_date,
                    "digest_hours": list(self.digest_hours),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def pushes_left(self, cap: int, *, today: date | None = None) -> int:
        """Remaining push allowance, resetting automatically at midnight."""
        stamp = (today or date.today()).isoformat()
        if self.push_date != stamp:
            return cap
        return max(0, cap - self.push_count)

    def record_pushes(self, count: int, *, today: date | None = None) -> None:
        stamp = (today or date.today()).isoformat()
        if self.push_date != stamp:
            self.push_date = stamp
            self.push_count = 0
        self.push_count += count

    def digest_already_sent(self, hour: int, *, today: date | None = None) -> bool:
        """Has *this* digest slot already gone today?

        Guards against overlapping triggers double-sending: cron-job.org is
        primary and GitHub's own schedule is left in place as a backup, so two
        runs landing in the same window is expected rather than exceptional.

        Scoped to the hour, not the day, so the morning digest doesn't block
        the evening one.
        """
        stamp = (today or date.today()).isoformat()
        return self.digest_date == stamp and hour in self.digest_hours

    def record_digest(self, hour: int, *, today: date | None = None) -> None:
        stamp = (today or date.today()).isoformat()
        if self.digest_date != stamp:
            self.digest_date = stamp
            self.digest_hours = ()
        if hour not in self.digest_hours:
            self.digest_hours = tuple(sorted(self.digest_hours + (hour,)))
