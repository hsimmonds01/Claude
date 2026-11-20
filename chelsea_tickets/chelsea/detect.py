"""Work out what changed since the last run.

Harry wants to hear about each step a members' ticket application window takes,
as soon as the data shows it -- not only when it is live and not only when the
website does:

1. A new home fixture appearing on the ticket page -- Chelsea only lists a
   fixture once ticket info exists, so its arrival means a sale is coming.
2. PREVIEW: the match details already hint at an opening time, though the
   window still says "to be announced". Early information.
3. ANNOUNCED: the window gets a real date but is not open yet.
4. OPEN: the window genuinely opens (then one follow-up reminder, see
   `due_reminders`).

Windows closing, selling out, away games, Ticket Exchange and hospitality are
tracked in state but stay silent by design.

Alerts are grouped per fixture rather than per event, so a fixture that
appears with its window already open produces one notification, not two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import ANNOUNCED, OPEN, PREVIEW, SCHEDULED, HomeFixture, SaleWindow

NEW_FIXTURE = "new_fixture"
WINDOW_OPEN = "window_open"
WINDOW_ANNOUNCED = "window_announced"
WINDOW_PREVIEW = "window_preview"

# How far along a window is. An alert fires when a window moves *up* this
# ladder; anything not on it (closed, sold out, unknown, never seen) counts as
# the bottom, so a second batch re-opening from "closed" alerts again.
_PROGRESS = {SCHEDULED: 0, PREVIEW: 1, ANNOUNCED: 2, OPEN: 3}


def _progress(state: str | None) -> int:
    return _PROGRESS.get(state or "", 0)

# How long after the primary "applications OPEN" alert to send one follow-up
# nudge if the window is still open -- in case the first alert got missed.
# Deliberately a single, bounded reminder rather than a repeat every poll:
# see `due_reminders` below.
REMINDER_AFTER = timedelta(hours=6)


def window_alert_key(fixture_id: str, window_key: str, kind: str = WINDOW_OPEN) -> str:
    return f"{fixture_id}::{kind}::{window_key}"


def _is_ballot_window(window: SaleWindow) -> bool:
    """True for the actual members' ticket application window (the ballot).

    `fixture.application_closes` is parsed from the "Ticket application
    window closes" wording in the fixture's own info blob -- it describes
    *this* window specifically, not any other relevant-but-different window
    on the same fixture (e.g. a separate, undated "purchase additional
    tickets" route). Attaching it to the wrong window's alert states a
    deadline that has nothing to do with what just opened -- seen live: an
    "additional tickets" alert claiming a closing date that was for the
    ballot, and had already passed.
    """
    return "application" in window.title.lower()


@dataclass(frozen=True)
class FixtureAlert:
    """One notification's worth of news about a single fixture."""

    fixture: HomeFixture
    is_new: bool
    newly_open: tuple[SaleWindow, ...]
    keys: tuple[str, ...]
    newly_announced: tuple[SaleWindow, ...] = ()
    newly_preview: tuple[SaleWindow, ...] = ()

    @property
    def priority(self) -> str:
        # An open window is time-boxed and needs action. Everything earlier is
        # information for planning, which can wait until the phone is next
        # picked up.
        return "urgent" if self.newly_open else "default"

    @property
    def tags(self) -> str:
        if self.newly_open:
            return "rotating_light,soccer"
        if self.newly_announced or self.newly_preview:
            return "calendar,soccer"
        return "soccer"

    @property
    def title(self) -> str:
        fixture = self.fixture
        opponent = fixture.opponent
        if self.newly_open and self.is_new:
            return f"New fixture + applications OPEN: Chelsea v {opponent}"
        if self.newly_open:
            return f"Ticket applications OPEN: Chelsea v {opponent}"
        when = f" {fixture.application_opens}" if fixture.application_opens else ""
        if self.newly_announced:
            return f"Dates confirmed - applications open{when}: Chelsea v {opponent}"
        if self.newly_preview:
            return f"Early look - applications look set to open{when}: Chelsea v {opponent}"
        return f"New home fixture listed: Chelsea v {opponent}"

    def _dates(self) -> list[str]:
        fixture = self.fixture
        lines = []
        if fixture.application_opens:
            lines.append(f"Applications open: {fixture.application_opens}")
        if fixture.application_closes:
            lines.append(f"Applications close: {fixture.application_closes}")
        return lines

    @property
    def message(self) -> str:
        fixture = self.fixture
        lines = [fixture.describe]
        staged = self.newly_open or self.newly_announced or self.newly_preview

        if self.is_new and staged:
            lines.append("\nJust added to the ticket page.")

        if self.newly_open:
            for window in self.newly_open:
                lines.append(f"\nOPEN NOW: {window.title.strip()}")
                if window.on_sale_label:
                    lines.append(window.on_sale_label)
            if fixture.application_closes and any(_is_ballot_window(w) for w in self.newly_open):
                lines.append(f"\nApplications close: {fixture.application_closes}")
        elif self.newly_announced:
            lines.append("\nNOT OPEN YET - Chelsea has now given the window a date:")
            for window in self.newly_announced:
                lines.append(f"{window.title.strip()}")
                if window.on_sale_label:
                    lines.append(window.on_sale_label)
            lines.extend(self._dates())
            lines.append("You'll get another alert the moment it opens.")
        elif self.newly_preview:
            lines.append(
                "\nEARLY LOOK - not official yet. The ticket page still says 'to be "
                "announced', but the match details already point to:"
            )
            lines.extend(self._dates())
            lines.append("Worth setting a reminder. You'll hear again when it's confirmed.")
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

        def risen(state: str) -> tuple[SaleWindow, ...]:
            # A window is reported only at the highest stage it has reached, so
            # one that jumps straight to open does not also announce itself.
            return tuple(
                w for w in fixture.windows
                if w.state == state and _progress(previous_windows.get(w.key)) < _progress(state)
            )

        # On a brand-new fixture every window counts as risen -- there is no
        # prior state, and a window already open is the most important thing
        # to say.
        newly_open = risen(OPEN)
        newly_announced = risen(ANNOUNCED)
        newly_preview = risen(PREVIEW)

        if not is_new and not (newly_open or newly_announced or newly_preview):
            continue

        keys = (
            ([f"{fixture.id}::{NEW_FIXTURE}"] if is_new else [])
            + [window_alert_key(fixture.id, w.key, WINDOW_OPEN) for w in newly_open]
            + [window_alert_key(fixture.id, w.key, WINDOW_ANNOUNCED) for w in newly_announced]
            + [window_alert_key(fixture.id, w.key, WINDOW_PREVIEW) for w in newly_preview]
        )

        alerts.append(
            FixtureAlert(
                fixture=fixture,
                is_new=is_new,
                newly_open=newly_open,
                keys=tuple(keys),
                newly_announced=newly_announced,
                newly_preview=newly_preview,
            )
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
        if self.fixture.application_closes and _is_ballot_window(self.window):
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
