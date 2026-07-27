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
    last_digest_date: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "RunState":
        file = Path(path)
        if not file.exists():
            return cls()
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls()  # a lost counter costs one extra push at worst
        return cls(
            push_date=str(raw.get("push_date", "")),
            push_count=int(raw.get("push_count", 0)),
            last_digest_date=str(raw.get("last_digest_date", "")),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "push_date": self.push_date,
                    "push_count": self.push_count,
                    "last_digest_date": self.last_digest_date,
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

    def digest_already_sent_today(self, *, today: date | None = None) -> bool:
        """Guard against overlapping triggers double-sending.

        cron-job.org is the primary trigger and GitHub's own schedule is left
        in place as a backup, so two runs landing in the same hour is expected
        rather than exceptional.
        """
        return self.last_digest_date == (today or date.today()).isoformat()

    def record_digest(self, *, today: date | None = None) -> None:
        self.last_digest_date = (today or date.today()).isoformat()
