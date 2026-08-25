"""Turn Chelsea's raw ticket-feed JSON into typed home fixtures.

Everything the watcher needs to make a decision is derived here, so the
detection layer never touches raw feed dictionaries. That split matters
because the feed is someone else's API that can change shape without notice:
if it does, it should blow up in one place with a clear message rather than
silently produce empty results (a watcher that quietly sees no fixtures is
worse than one that crashes -- it just never alerts).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# Window states, derived from the feed's `status` object.
OPEN = "open"
CLOSED = "closed"
SOLD_OUT = "sold_out"
UNKNOWN = "unknown"

# A sale window matters to Harry if it is a members' route into the ground.
# He is a True Blue member, NOT a season ticket holder, so season-ticket-only
# windows are noise -- but "Season ticket holders & Members can purchase an
# additional ticket" is relevant, which is why this is an inclusion test on
# member/application wording rather than an exclusion test on "season ticket".
RELEVANT_WINDOW_WORDS = ("member", "application", "ballot")

# Ticket Exchange is explicitly out of scope (Harry has never got a ticket on
# it -- it is effectively always empty). Club Chelsea is hospitality pricing.
EXCLUDED_WINDOW_WORDS = ("ticket exchange", "club chelsea", "hospitality", "corporate")

STAMFORD_BRIDGE = "stamford bridge"


class FeedError(RuntimeError):
    """The ticket feed did not look like the API we expect."""


@dataclass(frozen=True)
class SaleWindow:
    """One purchasing route for one fixture, e.g. the members' ballot."""

    key: str
    title: str
    on_sale_label: str
    state: str
    status_text: str = ""
    info_url: str = ""

    @property
    def is_open(self) -> bool:
        return self.state == OPEN


@dataclass(frozen=True)
class HomeFixture:
    """A men's home game at Stamford Bridge, with its members' sale windows."""

    id: str
    opponent: str
    competition: str
    date: str
    time: str
    venue: str
    windows: tuple[SaleWindow, ...] = field(default_factory=tuple)
    application_opens: str = ""
    application_closes: str = ""

    @property
    def describe(self) -> str:
        return f"Chelsea v {self.opponent} ({self.competition}) - {self.date}, {self.time}"

    def window(self, key: str) -> SaleWindow | None:
        return next((w for w in self.windows if w.key == key), None)


_NUMBER_WORD_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
)


def _window_key(title: str) -> str:
    """Stable identity for a sale window within a fixture.

    Digits are stripped deliberately: window titles embed loyalty-point
    thresholds ("...(146 Loyalty Points)") which Chelsea edits between
    fixtures. Keeping the numbers would make an edited threshold look like a
    brand-new window and fire a spurious alert.

    Spelled-out counts are stripped for the same reason: an "additional
    tickets" window's title embeds a running total ("...purchase an
    additional two tickets (maximum of three...)") that Chelsea increments
    as more allocation opens up ("...four tickets (maximum of five...)").
    Without this, every increment looked like a brand-new window opening --
    seen live: a stale "applications OPEN" alert days after the real ballot
    had already closed, because the count had simply ticked up again.
    """
    without_digits = re.sub(r"\d+", "", title.lower())
    without_number_words = _NUMBER_WORD_RE.sub("", without_digits)
    slug = re.sub(r"[^a-z]+", "-", without_number_words).strip("-")
    return slug or "window"


def _window_state(status: object) -> tuple[str, str]:
    """Map the feed's `status` object to (state, raw_text).

    The critical and easily-inverted detail: an OPEN window has **no status
    object at all**. Chelsea only attaches a status once the window has shut
    ("Off Sale") or the allocation is gone ("Sold Out"). Treating a missing
    status as "unknown" would mean never detecting an open ballot.
    """
    if not isinstance(status, dict):
        return OPEN, ""
    text = str(status.get("text") or "").strip()
    if not text:
        return OPEN, ""
    lowered = text.lower()
    if "sold out" in lowered:
        return SOLD_OUT, text
    if "off sale" in lowered:
        return CLOSED, text
    return UNKNOWN, text


def is_relevant_window(title: str, is_club_chelsea: bool) -> bool:
    lowered = title.lower()
    if is_club_chelsea or any(word in lowered for word in EXCLUDED_WINDOW_WORDS):
        return False
    return any(word in lowered for word in RELEVANT_WINDOW_WORDS)


def _parse_windows(raw_tickets: object) -> tuple[SaleWindow, ...]:
    if not isinstance(raw_tickets, list):
        return ()
    windows: list[SaleWindow] = []
    seen: set[str] = set()
    for raw in raw_tickets:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title or not is_relevant_window(title, bool(raw.get("isClubChelsea"))):
            continue
        key = _window_key(title)
        if key in seen:  # same route listed twice; first wins
            continue
        seen.add(key)
        state, status_text = _window_state(raw.get("status"))
        link = raw.get("moreInfoLink")
        windows.append(
            SaleWindow(
                key=key,
                title=title,
                on_sale_label=str(raw.get("label") or "").strip(),
                state=state,
                status_text=status_text,
                info_url=str(link.get("url") or "") if isinstance(link, dict) else "",
            )
        )
    return tuple(windows)


def strip_html(blob: str) -> str:
    """Flatten the View Details HTML blob into plain text.

    Unescaped twice on purpose: the feed delivers the blob already escaped,
    and the CMS stores it escaped again, so a single unescape leaves literal
    `&lt;p&gt;` markup behind.
    """
    text = html.unescape(html.unescape(blob or ""))
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# The blob renders as one long line once tags are stripped, so a greedy
# capture runs straight past the date into the following sentence (that is
# how "Thursday 20 August 12pm Season" turned up in testing). Matching the
# date shape explicitly and stopping at the time is far tighter than trying
# to guess where the next clause begins.
_DATETIME = re.compile(
    r"""(
        (?:\w+day\s+)?          # Friday
        \d{1,2}\s+\w+           # 21 August
        (?:\s+\d{4})?           # 2026 (Chelsea usually omits it)
        (?:\s*,?\s*(?:at\s+)?
           \d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)?   # 12pm / 11:30am
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Fallback when the date is not in the usual shape: stop at the words that
# reliably begin the next clause.
_CLAUSE_END = re.compile(
    r"\s{2,}|\.\s|\bTicket\b|\bThe\b|\bApplications\b|\bEligible\b|\bTickets\b|\bSeason\b",
    re.IGNORECASE,
)


def _date_after(text: str, phrase: str) -> str:
    match = re.search(phrase + r"\s*[^A-Za-z0-9]{0,4}\s*(.{4,60})", text, re.IGNORECASE)
    if not match:
        return ""
    tail = match.group(1)
    shaped = _DATETIME.search(tail)
    if shaped:
        return re.sub(r"\s+", " ", shaped.group(1)).strip()
    return _CLAUSE_END.split(tail)[0].strip(" .,:;-‐‑‒–—―")


def parse_application_dates(blob: str) -> tuple[str, str]:
    """Pull the ballot's open/close datetimes out of the View Details text.

    Returns ("", "") when the fixture has no application window described --
    that is normal, not an error, so callers must not treat it as failure.
    """
    text = strip_html(blob)
    return (
        _date_after(text, r"application window opens?"),
        _date_after(text, r"application window closes?"),
    )


def _is_home(fixture: dict) -> bool:
    """True when Chelsea are the home side at Stamford Bridge.

    Both checks are kept: `isOpposition` is the feed's own semantic flag, and
    the venue guard stops a neutral-venue cup tie (Wembley finals) counting as
    a home game, which is not what Harry is watching for.
    """
    home = fixture.get("home")
    if not isinstance(home, dict):
        return False
    if home.get("isOpposition") is not False:
        return False
    return str(fixture.get("venue") or "").strip().lower() == STAMFORD_BRIDGE


def parse_home_fixtures(payload: object) -> list[HomeFixture]:
    """Extract home fixtures from a `/en/api/fixtures/tickets` response."""
    if not isinstance(payload, dict) or "items" not in payload:
        raise FeedError("ticket feed has no 'items' key - the API shape has changed")
    groups = payload.get("items")
    if not isinstance(groups, list):
        raise FeedError("ticket feed 'items' was not a list - the API shape has changed")

    fixtures: list[HomeFixture] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for entry in group.get("items") or ():
            if not isinstance(entry, dict):
                continue
            fixture = entry.get("fixture")
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id or not isinstance(fixture, dict) or not _is_home(fixture):
                continue
            away = fixture.get("away") if isinstance(fixture.get("away"), dict) else {}
            info = entry.get("fullTicketInfoLink")
            blob = info.get("content") if isinstance(info, dict) else ""
            opens, closes = parse_application_dates(str(blob or ""))
            fixtures.append(
                HomeFixture(
                    id=entry_id,
                    opponent=str(away.get("name") or "Unknown opponent").strip(),
                    competition=str(fixture.get("competition") or "").strip(),
                    date=str(fixture.get("date") or "").strip(),
                    time=str(fixture.get("time") or "").strip(),
                    venue=str(fixture.get("venue") or "").strip(),
                    windows=_parse_windows(entry.get("tickets")),
                    application_opens=opens,
                    application_closes=closes,
                )
            )
    return fixtures
