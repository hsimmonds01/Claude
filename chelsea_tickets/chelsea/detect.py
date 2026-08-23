"""Work out what changed since the last run.

Two things are worth waking Harry for, and only two:

1. A new home fixture appearing on the ticket page -- Chelsea only lists a
   fixture once ticket info exists, so its arrival means a sale is coming.
2. A members' application window (the ballot) becoming open.

Everything else -- windows closing, selling out, away games, Ticket Exchange,
hospitality -- is tracked in state but stays silent by design.

Alerts are grouped per fixture rather than per event, so a fixture that
appears with its ballot already open produces one notification, not two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import OPEN, HomeFixture, SaleWindow

NEW_FIXTURE = "new_fixture"
WINDOW_OPEN = "window_open"

# How long after the primary "applications OPEN" alert to send one follow-up
# nudge if the window is still open -- in case the first alert got missed.
# Deliberately a single, bounded reminder rather than a repeat every poll:
# see `due_reminders` below.
REMINDER_AFTER = timedelta(hours=6)


def window_alert_key(fixture_id: str, window_key: str) -> str:
    return f"{fixture_id}::{WINDOW_OPEN}::{window_key}"


@dataclass(frozen=True)
class FixtureAlert:
    """One notification's worth of news about a single fixture."""

    fixture: HomeFixture
    is_new: bool
    newly_open: tuple[SaleWindow, ...]
    keys: tuple[str, ...]

    @property
    def priority(self) -> str:
        # An open ballot is time-boxed and needs action; a newly listed
        # fixture is a heads-up that can wait until the phone is next picked up.
        return "urgent" if self.newly_open else "default"

    @property
    def tags(self) -> str:
        return "rotating_light,soccer" if self.newly_open else "soccer"

    @property
    def title(self) -> str:
        opponent = self.fixture.opponent
        if self.newly_open and self.is_new:
            return f"New fixture + applications OPEN: Chelsea v {opponent}"
        if self.newly_open:
            return f"Ticket applications OPEN: Chelsea v {opponent}"
        return f"New home fixture listed: Chelsea v {opponent}"

    @property
    def message(self) -> str:
        fixture = self.fixture
        lines = [fixture.describe]

        if self.newly_open:
            for window in self.newly_open:
                lines.append(f"\nOPEN NOW: {window.title.strip()}")
                if window.on_sale_label:
                    lines.append(window.on_sale_label)
            if fixture.application_closes:
                lines.append(f"\nApplications close: {fixture.application_closes}")
        elif self.is_new:
            lines.append("\nJust added to the ticket page - a sale window is coming.")
            if fixture.application_opens:
                lines.append(f"Applications open: {fixture.application_opens}")
            if fixture.application_closes:
                lines.append(f"Applications close: {fixture.application_closes}")
            if not fixture.application_opens and not fixture.application_closes:
                lines.append("No application dates published yet.")

        lines.append("\nhttps://www.eticketing.co.uk/chelseafc")
        return "\n".join(lines)


def detect(
    previous: dict[str, dict],
    current: list[HomeFixture],
    seeded: bool,
) -> list[FixtureAlert]:
    """Compare this run's fixtures against the stored snapshot.

    `seeded` is False on the very first run, when `previous` is empty. Every
    fixture would look new then, so nothing is emitted and the run simply
    records a baseline.
    """
    if not seeded:
        return []

    alerts: list[FixtureAlert] = []
    for fixture in current:
        before = previous.get(fixture.id)
        is_new = before is None
        previous_windows: dict[str, str] = {}
        if isinstance(before, dict) and isinstance(before.get("windows"), dict):
            previous_windows = before["windows"]

        # On a brand-new fixture every open window counts as newly open --
        # there is no prior state, and an already-open ballot is the single
        # most important thing to say.
        newly_open = tuple(
            window
            for window in fixture.windows
            if window.state == OPEN and previous_windows.get(window.key) != OPEN
        )

        if not is_new and not newly_open:
            continue

        keys = tuple(
            [f"{fixture.id}::{NEW_FIXTURE}"] if is_new else []
        ) + tuple(window_alert_key(fixture.id, w.key) for w in newly_open)

        alerts.append(
            FixtureAlert(fixture=fixture, is_new=is_new, newly_open=newly_open, keys=keys)
        )
    return alerts


@dataclass(frozen=True)
class ReminderAlert:
    """A single bounded follow-up, sent once if a ballot is still open."""

    fixture: HomeFixture
    window: SaleWindow
    priority: str = "urgent"
    tags: str = "rotating_light,soccer"

    @property
    def title(self) -> str:
        return f"Reminder: applications still OPEN - Chelsea v {self.fixture.opponent}"

    @property
    def message(self) -> str:
        lines = [
            self.fixture.describe,
            f"\nStill open: {self.window.title.strip()}",
            "(you were already alerted when this opened -- this is a one-off "
            "follow-up in case you missed it)",
        ]
        if self.fixture.application_closes:
            lines.append(f"\nApplications close: {self.fixture.application_closes}")
        lines.append("\nhttps://www.eticketing.co.uk/chelseafc")
        return "\n".join(lines)


def due_reminders(
    open_since: dict[str, str],
    current: list[HomeFixture],
    now: datetime,
) -> tuple[dict[str, str], list[ReminderAlert]]:
    """Age tracked open windows and return any one-off reminders now due.

    `open_since` only ever contains keys the primary "applications OPEN"
    alert has already fired for -- callers add a key the moment that alert is
    sent (see `run()` in check_tickets.py); this function never adds one.
    It only ages entries out, one of two ways: the window is no longer open
    (closed, sold out, or the fixture dropped off the feed entirely), or its
    reminder has just fired. Either way the key is dropped, so a later
    reopen (Chelsea's second-batch releases) starts a fresh clock via a fresh
    primary alert and can remind again -- exactly one reminder per open spell,
    never a repeat on every 30-minute poll.
    """
    by_key = {
        window_alert_key(fixture.id, window.key): (fixture, window)
        for fixture in current
        for window in fixture.windows
    }
    kept: dict[str, str] = {}
    reminders: list[ReminderAlert] = []
    for key, since_raw in open_since.items():
        match = by_key.get(key)
        if match is None or not match[1].is_open:
            continue
        fixture, window = match
        if now - datetime.fromisoformat(since_raw) >= REMINDER_AFTER:
            reminders.append(ReminderAlert(fixture=fixture, window=window))
        else:
            kept[key] = since_raw
    return kept, reminders
