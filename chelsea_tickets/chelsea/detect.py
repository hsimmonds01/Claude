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

from .model import OPEN, HomeFixture, SaleWindow

NEW_FIXTURE = "new_fixture"
WINDOW_OPEN = "window_open"


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
        ) + tuple(f"{fixture.id}::{WINDOW_OPEN}::{w.key}" for w in newly_open)

        alerts.append(
            FixtureAlert(fixture=fixture, is_new=is_new, newly_open=newly_open, keys=keys)
        )
    return alerts
