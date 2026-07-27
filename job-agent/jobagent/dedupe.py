"""The "already seen" memory, committed back to her repo between runs.

Two jobs here:

1. Never show the same role twice.
2. **Freeze each role's score the first time it's seen.** Asking an AI to mark
   the same job twice reliably gives two different numbers, and with a phone
   alert hanging off a threshold that means a borderline role either pings her
   on separate days or flickers just under the bar forever. Scoring once and
   storing the result removes the wobble entirely and makes re-runs free.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class SeenEntry:
    fingerprint: str
    score: int
    reason: str
    first_seen: str  # ISO date
    notified: bool = False

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "score": self.score,
            "reason": self.reason,
            "first_seen": self.first_seen,
            "notified": self.notified,
        }

    @staticmethod
    def from_dict(raw: dict) -> SeenEntry | None:
        """Parse one row, or None if it's unusable.

        Structurally valid JSON can still hold a nonsense score after a hand
        edit or a bad merge. Catching only JSONDecodeError at the file level
        left that case crashing the whole run -- which contradicts the reason
        that handler exists. One dropped row costs one repeated job.
        """
        if not isinstance(raw, dict):
            return None
        fingerprint = str(raw.get("fingerprint", "")).strip()
        if not fingerprint:
            return None
        try:
            score = int(raw.get("score", 0))
        except (TypeError, ValueError):
            return None
        return SeenEntry(
            fingerprint=fingerprint,
            score=score,
            reason=str(raw.get("reason", "")),
            first_seen=str(raw.get("first_seen", "")),
            notified=bool(raw.get("notified", False)),
        )


class SeenStore:
    """Fingerprint -> previous verdict, persisted as JSON."""

    def __init__(self, entries: dict[str, SeenEntry] | None = None):
        self._entries: dict[str, SeenEntry] = dict(entries or {})

    @classmethod
    def load(cls, path: str | Path) -> SeenStore:
        file = Path(path)
        if not file.exists():
            return cls()
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A corrupt state file must not take the whole run down -- losing
            # the memory costs one duplicated digest, whereas crashing costs
            # every alert until someone notices.
            return cls()
        entries = {}
        for item in raw.get("seen", []) or []:
            entry = SeenEntry.from_dict(item)
            if entry is not None:
                entries[entry.fingerprint] = entry
        return cls(entries)

    def save(self, path: str | Path) -> None:
        payload = {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "seen": [entry.to_dict() for entry in self._entries.values()],
        }
        Path(path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, fingerprint: str) -> bool:
        return fingerprint in self._entries

    def get(self, fingerprint: str) -> SeenEntry | None:
        return self._entries.get(fingerprint)

    def remember(
        self,
        fingerprint: str,
        score: int,
        reason: str,
        *,
        today: date | None = None,
        notified: bool = False,
    ) -> SeenEntry:
        """Record a verdict. An existing verdict is never overwritten."""
        existing = self._entries.get(fingerprint)
        if existing is not None:
            return existing
        entry = SeenEntry(
            fingerprint=fingerprint,
            score=score,
            reason=reason,
            first_seen=(today or date.today()).isoformat(),
            notified=notified,
        )
        self._entries[fingerprint] = entry
        return entry

    def mark_notified(self, fingerprint: str) -> None:
        entry = self._entries.get(fingerprint)
        if entry is not None:
            self._entries[fingerprint] = SeenEntry(
                fingerprint=entry.fingerprint,
                score=entry.score,
                reason=entry.reason,
                first_seen=entry.first_seen,
                notified=True,
            )

    def prune(self, retention_days: int, *, today: date | None = None) -> int:
        """Drop entries older than the retention window.

        Without this the file grows forever, is rewritten and committed four
        times a day, and bloats the repo. A role that's still open after two
        months and resurfaces is arguably worth another look anyway.
        """
        cutoff = (today or date.today()) - timedelta(days=retention_days)
        keep = {}
        for fingerprint, entry in self._entries.items():
            try:
                seen_on = date.fromisoformat(entry.first_seen)
            except ValueError:
                continue  # undated entries are legacy junk; let them go
            if seen_on >= cutoff:
                keep[fingerprint] = entry
        removed = len(self._entries) - len(keep)
        self._entries = keep
        return removed


def split_new(jobs, store: SeenStore):
    """Partition merged jobs into (never seen before, already known)."""
    fresh, known = [], []
    for job in jobs:
        (known if job.fingerprint in store else fresh).append(job)
    return fresh, known
