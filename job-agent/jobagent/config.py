"""Load and validate config.yml.

She edits this file from her phone with no way to run it first, so validation
is strict and the error messages are written for someone who is not a
developer. A run that stops with "digest hours must be run hours" is far
kinder than one that succeeds silently and never emails her again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """Raised with a message intended to be read by her, not by a developer."""


def _to_minutes(clock: str) -> int:
    """'21:30' -> 1290. Tolerates a bare hour ('21') and stray whitespace."""
    parts = clock.strip().split(":")
    hours = int(parts[0] or 0)
    minutes = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    return hours * 60 + minutes


@dataclass(frozen=True)
class QuietHours:
    start: str
    end: str

    def covers(self, hour: int) -> bool:
        """True when a run at `hour`:00 falls inside the quiet window.

        Compared in minutes, not whole hours: with a 07:30 end, a run at 07:00
        is still inside the quiet window, and an hours-only comparison would
        wrongly let it buzz. Also handles windows wrapping past midnight
        (21:30 -> 07:30), which is the normal case here and the one a naive
        start <= h < end gets wrong.
        """
        start = _to_minutes(self.start)
        end = _to_minutes(self.end)
        now = hour * 60
        if start <= end:
            return start <= now < end
        return now >= start or now < end


@dataclass(frozen=True)
class Config:
    enabled: bool
    run_hours: tuple[int, ...]
    timezone: str
    min_score_to_keep: int
    push_threshold: int
    explain_scores: bool
    push_enabled: bool
    push_max_per_day: int
    push_vague_wording: bool
    quiet_hours: QuietHours
    email_enabled: bool
    email_digest_hours: tuple[int, ...]
    email_max_roles: int
    email_send_when_empty: bool
    search_terms: tuple[str, ...]
    locations: tuple[str, ...]
    max_distance_miles: int
    max_age_days: int
    adzuna_enabled: bool
    reed_enabled: bool
    companies_enabled: bool
    companies: tuple[dict, ...]
    inbox_enabled: bool
    inbox_max_age_days: int
    inbox_trusted_senders: tuple[str, ...]
    seen_retention_days: int
    alert_on_failure: bool

    def is_digest_hour(self, hour: int) -> bool:
        return hour in self.email_digest_hours


def _get(mapping: dict, path: str, default):
    """Walk a dotted path, tolerating missing intermediate sections.

    Missing keys fall back to the default rather than raising, so adding a new
    setting later doesn't break a config file she hasn't updated yet.
    """
    node = mapping
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node is not None else default


def _clean_list(values) -> tuple[str, ...]:
    """Strings from a YAML list, dropping blanks.

    A bare "-" in the template parses as None, and `str(None)` is the truthy
    string "None" -- which would otherwise sail through as a real search term
    and have the agent hunting for jobs called "None". Filter before casting.
    """
    out = []
    for value in values or []:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            out.append(text)
    return tuple(out)


def _hours(values, label: str) -> tuple[int, ...]:
    if not isinstance(values, list) or not values:
        raise ConfigError(f"'{label}' must be a list of hours, e.g. [7, 12, 16, 18].")
    out = []
    for value in values:
        if not isinstance(value, int) or not 0 <= value <= 23:
            raise ConfigError(
                f"'{label}' contains {value!r}. Hours must be whole numbers "
                f"from 0 to 23 on a 24-hour clock (so 6pm is 18, not 6)."
            )
        out.append(value)
    return tuple(sorted(set(out)))


def load(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(
            "config.yml doesn't look like a settings file. Check the "
            "indentation hasn't been changed."
        )

    run_hours = _hours(_get(raw, "schedule.run_hours", [7, 12, 16, 18]), "run_hours")
    digest_hours = _hours(
        _get(raw, "email.send_on_run_hours", [7, 18]), "send_on_run_hours"
    )

    # The guard that matters most. The agent only exists at run_hours; a digest
    # hour outside that set means the digest silently never sends, which looks
    # exactly like the agent being broken. Caught here, loudly, on every run.
    orphans = sorted(set(digest_hours) - set(run_hours))
    if orphans:
        raise ConfigError(
            "Email digests are set to send at "
            + ", ".join(f"{h}:00" for h in orphans)
            + ", but the agent doesn't run at "
            + ("that time" if len(orphans) == 1 else "those times")
            + ". It only runs at "
            + ", ".join(f"{h}:00" for h in run_hours)
            + ". Either add the missing time to schedule.run_hours, or change "
            "email.send_on_run_hours to a time that's already in that list. "
            "(No email would ever be sent as things stand.)"
        )

    min_score = int(_get(raw, "scoring.min_score_to_keep", 6))
    push_threshold = int(_get(raw, "scoring.push_threshold", 8))
    if not 0 <= min_score <= 10 or not 0 <= push_threshold <= 10:
        raise ConfigError("Scores must be between 0 and 10.")
    if push_threshold < min_score:
        raise ConfigError(
            f"push_threshold ({push_threshold}) is lower than min_score_to_keep "
            f"({min_score}), so jobs could qualify for a phone alert while being "
            "dropped for being too weak. Raise push_threshold, or lower "
            "min_score_to_keep."
        )

    # Blank "-" placeholders parse as None; drop them rather than nagging her
    # about an entry she hasn't filled in yet.
    companies = tuple(
        entry for entry in (_get(raw, "sources.companies.list", []) or []) if entry
    )
    for entry in companies:
        if not isinstance(entry, dict) or "careers_url" not in entry:
            raise ConfigError(
                "Every company needs a 'name' and a 'careers_url'. One entry "
                f"is missing its careers_url: {entry!r}"
            )

    return Config(
        enabled=bool(_get(raw, "enabled", True)),
        run_hours=run_hours,
        timezone=str(_get(raw, "schedule.timezone", "Europe/London")),
        min_score_to_keep=min_score,
        push_threshold=push_threshold,
        explain_scores=bool(_get(raw, "scoring.explain_scores", True)),
        push_enabled=bool(_get(raw, "push.enabled", True)),
        push_max_per_day=int(_get(raw, "push.max_per_day", 2)),
        push_vague_wording=bool(_get(raw, "push.vague_wording", True)),
        quiet_hours=QuietHours(
            start=str(_get(raw, "push.quiet_hours.start", "21:30")),
            end=str(_get(raw, "push.quiet_hours.end", "07:30")),
        ),
        email_enabled=bool(_get(raw, "email.enabled", True)),
        email_digest_hours=digest_hours,
        email_max_roles=int(_get(raw, "email.max_roles_per_digest", 12)),
        email_send_when_empty=bool(_get(raw, "email.send_when_empty", False)),
        search_terms=_clean_list(_get(raw, "sources.search_terms", [])),
        locations=_clean_list(_get(raw, "sources.locations", ["London"])),
        max_distance_miles=int(_get(raw, "sources.max_distance_miles", 20)),
        max_age_days=int(_get(raw, "sources.max_age_days", 14)),
        adzuna_enabled=bool(_get(raw, "sources.adzuna.enabled", True)),
        reed_enabled=bool(_get(raw, "sources.reed.enabled", True)),
        companies_enabled=bool(_get(raw, "sources.companies.enabled", True)),
        companies=companies,
        inbox_enabled=bool(_get(raw, "sources.alert_inbox.enabled", True)),
        inbox_max_age_days=int(_get(raw, "sources.alert_inbox.max_age_days", 3)),
        inbox_trusted_senders=tuple(
            s.casefold()
            for s in _clean_list(_get(raw, "sources.alert_inbox.trusted_senders", []))
        ),
        seen_retention_days=int(_get(raw, "housekeeping.seen_retention_days", 60)),
        alert_on_failure=bool(_get(raw, "housekeeping.alert_on_failure", True)),
    )
