"""Load and validate config.yml.

She edits this file from her phone with no way to run it first, so validation
is strict and the error messages are written for someone who is not a
developer. A run that stops with "digest hours must be run hours" is far
kinder than one that succeeds silently and never emails her again.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


class ConfigError(ValueError):
    """Raised with a message intended to be read by her, not by a developer."""


def _to_minutes(clock: str) -> int:
    """'21:30' -> 1290. Tolerates a bare hour ('21') and stray whitespace.

    Raises ConfigError rather than ValueError so a typo like "9pm" reaches her
    as a sentence instead of a traceback.
    """
    parts = str(clock).strip().split(":")
    try:
        hours = int(parts[0] or 0)
        minutes = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    except ValueError:
        raise ConfigError(
            f'Quiet hours should look like "21:30" on a 24-hour clock, but '
            f"one says {clock!r}. Use 21:30 rather than 9pm."
        ) from None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ConfigError(
            f"Quiet hours {clock!r} isn't a real time. Use a 24-hour clock, "
            "e.g. 21:30 for half past nine at night."
        )
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

    def now(self):
        """Current time in *her* timezone, not the runner's.

        GitHub's runners are UTC, but every hour in config.yml is labelled to
        her as UK time and cron-job.org is configured in Europe/London. Using
        the runner's clock meant that through British Summer Time the 07:00
        London trigger arrived as hour 6, matched nothing in run_hours, and
        the run exited having done nothing — successfully, so no failure
        email either. Read the timezone the config actually specifies.
        """
        from datetime import datetime

        return datetime.now(ZoneInfo(self.timezone))


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


def _int(value, label: str) -> int:
    """Whole number from config, with an error she can act on.

    A bare int() raises ValueError, and since ConfigError subclasses
    ValueError but not the other way round, that escaped the friendly handler
    and reached her as a traceback -- for something as ordinary as typing
    "twelve" into a numeric field on a phone.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"'{label}' should be a plain number, but it says {value!r}. "
            "Write it as digits with no quotes, units or commas — 12, not "
            '"twelve" and not "12 roles".'
        ) from None


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
        # `bool` is a subclass of `int`, and YAML reads bare `yes`/`on`/`true`
        # as booleans -- so without this an accidental `- yes` became hour 1.
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
            raise ConfigError(
                f"'{label}' contains {value!r}. Hours must be whole numbers "
                f"from 0 to 23 on a 24-hour clock (so 6pm is 18, not 6)."
            )
        out.append(value)
    return tuple(sorted(set(out)))


def _timezone(raw: dict) -> str:
    """The timezone every hour in this file is expressed in."""
    name = str(_get(raw, "schedule.timezone", "Europe/London")).strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(
            f"'{name}' isn't a timezone name the system recognises. Use "
            "Europe/London (that's the one that handles the clocks going "
            "forward and back by itself)."
        ) from None
    return name


def _validate_hours(run_hours, digest_hours) -> None:
    """The agent only exists at run_hours; a digest hour outside that set means
    the digest silently never sends, which looks exactly like the agent being
    broken. Caught loudly, on every run."""
    orphans = sorted(set(digest_hours) - set(run_hours))
    if not orphans:
        return
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


def _validate_scores(min_score: int, push_threshold: int) -> None:
    if not 0 <= min_score <= 10 or not 0 <= push_threshold <= 10:
        raise ConfigError("Scores must be between 0 and 10.")
    if push_threshold < min_score:
        raise ConfigError(
            f"push_threshold ({push_threshold}) is lower than min_score_to_keep "
            f"({min_score}), so jobs could qualify for a phone alert while being "
            "dropped for being too weak. Raise push_threshold, or lower "
            "min_score_to_keep."
        )


def _companies(raw: dict) -> tuple[dict, ...]:
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
    return companies


def _quiet_hours(raw: dict) -> QuietHours:
    """Build and eagerly validate the quiet window.

    Validated at load time rather than at first use, so a typo is reported on
    the next run instead of lying dormant until the one evening it matters.
    """
    quiet = QuietHours(
        start=str(_get(raw, "push.quiet_hours.start", "21:30")),
        end=str(_get(raw, "push.quiet_hours.end", "07:30")),
    )
    _to_minutes(quiet.start)
    _to_minutes(quiet.end)
    return quiet


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
    _validate_hours(run_hours, digest_hours)

    min_score = _int(_get(raw, "scoring.min_score_to_keep", 6), "min_score_to_keep")
    push_threshold = _int(_get(raw, "scoring.push_threshold", 8), "push_threshold")
    _validate_scores(min_score, push_threshold)

    companies = _companies(raw)

    return Config(
        enabled=bool(_get(raw, "enabled", True)),
        run_hours=run_hours,
        timezone=_timezone(raw),
        min_score_to_keep=min_score,
        push_threshold=push_threshold,
        explain_scores=bool(_get(raw, "scoring.explain_scores", True)),
        push_enabled=bool(_get(raw, "push.enabled", True)),
        push_max_per_day=_int(_get(raw, "push.max_per_day", 2), "push.max_per_day"),
        push_vague_wording=bool(_get(raw, "push.vague_wording", True)),
        quiet_hours=_quiet_hours(raw),
        email_enabled=bool(_get(raw, "email.enabled", True)),
        email_digest_hours=digest_hours,
        email_max_roles=_int(
            _get(raw, "email.max_roles_per_digest", 12), "max_roles_per_digest"
        ),
        email_send_when_empty=bool(_get(raw, "email.send_when_empty", False)),
        search_terms=_clean_list(_get(raw, "sources.search_terms", [])),
        locations=_clean_list(_get(raw, "sources.locations", ["London"])),
        max_distance_miles=_int(
            _get(raw, "sources.max_distance_miles", 20), "max_distance_miles"
        ),
        max_age_days=_int(_get(raw, "sources.max_age_days", 14), "max_age_days"),
        adzuna_enabled=bool(_get(raw, "sources.adzuna.enabled", True)),
        reed_enabled=bool(_get(raw, "sources.reed.enabled", True)),
        companies_enabled=bool(_get(raw, "sources.companies.enabled", True)),
        companies=companies,
        inbox_enabled=bool(_get(raw, "sources.alert_inbox.enabled", True)),
        inbox_max_age_days=_int(
            _get(raw, "sources.alert_inbox.max_age_days", 3), "alert_inbox.max_age_days"
        ),
        inbox_trusted_senders=tuple(
            s.casefold()
            for s in _clean_list(_get(raw, "sources.alert_inbox.trusted_senders", []))
        ),
        seen_retention_days=_int(
            _get(raw, "housekeeping.seen_retention_days", 60), "seen_retention_days"
        ),
        alert_on_failure=bool(_get(raw, "housekeeping.alert_on_failure", True)),
    )
