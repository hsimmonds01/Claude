#!/usr/bin/env python3
"""Watch the Between The Bridges World Cup page for the England v Argentina
semi-final (Wed 15 July 2026) event going live, and push a phone notification
via ntfy.sh the moment tickets appear.

How it works, in order of what you'll be notified about:

1. "Event page is live" (urgent) -- a new /events-btb/ link matching the
   England v Argentina match appears on the World Cup hub page or the
   ticketed-events page.
2. "Tickets ON SALE" (urgent) -- the event page contains a link to a known
   ticket platform (DICE, Eventbrite, etc.) or a book/buy button. This can
   fire in the same run as #1 (single combined notification) or later if the
   page goes up before sales open.
3. "Sold out" (default) -- the event page later shows sold-out wording, so
   you know to stop hoping.
4. "New event appeared" (default) -- any OTHER new event link shows up on the
   watched pages. Safety net in case the venue names the page something this
   script's target matching doesn't anticipate.
5. "Monitor can't reach the site" (high) -- several consecutive runs failed
   to fetch any watched page, i.e. the monitor is blind, not just quiet.

Every notification is sent at most once (tracked in state.json, which the
GitHub Actions workflow commits back to the repo, same pattern as the
dock-alerter). The whole monitor self-disables after the match has started.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://www.betweenthebridges.co.uk"
HUB_URLS = [
    f"{BASE_URL}/fifa-world-cup-2026",
    f"{BASE_URL}/ticketed",
]

# Stop checking once the match has started (semi-final is Wed 15 July 2026,
# expected ~20:00 UK kick-off). After this moment every run no-ops, so the
# scheduled workflow can be left in place and deleted at leisure.
MONITOR_END = datetime(2026, 7, 15, 22, 0, tzinfo=ZoneInfo("Europe/London"))

# A link counts as THE England v Argentina event if its URL and link text,
# taken together, look like it. "argentina" alone is not enough -- the hub
# page still links the old "Round of 16: Argentina v Egypt" event -- so
# require a second corroborating signal.
def is_target(haystack: str) -> bool:
    h = haystack.lower()
    if "argentina" in h and ("england" in h or "semi" in h or "15" in h):
        return True
    if "semi" in h and ("final" in h or "england" in h or "15" in h):
        return True
    if re.search(r"15[\s-]*jul|jul[a-z]*[\s-]*15", h):
        return True
    return False


TICKET_PLATFORM_PATTERN = re.compile(
    r"https?://[^\"'\s<>]*(?:dice\.fm|eventbrite\.[a-z.]+|seetickets\.com|"
    r"designmynight\.com|fatsoma\.com|fixr\.co|skiddle\.com|ticketweb\.[a-z.]+|"
    r"ticketmaster\.[a-z.]+|eventim\.[a-z.]+|wegottickets\.com)[^\"'\s<>]*",
    re.IGNORECASE,
)
BUY_TEXT_PATTERN = re.compile(r"\b(book now|buy tickets|get tickets|tickets on sale)\b", re.IGNORECASE)
SOLD_OUT_PATTERN = re.compile(r"\bsold[\s-]*out\b", re.IGNORECASE)

EVENT_LINK_PATTERN = re.compile(
    r"<a\b[^>]*href=\"([^\"]*/events?-?btb/[^\"]+|/events/[^\"]+)\"[^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
TAG_STRIP_PATTERN = re.compile(r"<[^>]+>")

REQUEST_TIMEOUT_SECONDS = 20
FETCH_FAILURES_BEFORE_ALERT = 3

# Look like a normal browser: the venue's site (Squarespace) fronts requests
# with bot filtering that 403s obvious non-browser clients.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Same ntfy fallback-topic pattern as dock-alerter/check_docks.py: GitHub
# Actions sets NTFY_TOPIC to an empty string (not absent) when the secret is
# missing, so use `or`, not .get() default.
DEFAULT_NTFY_TOPIC = "harry-tooley-docks-5494e935"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or DEFAULT_NTFY_TOPIC
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

STATE_FILE = Path(__file__).parent / "state.json"


@dataclass
class State:
    seeded: bool = False
    known_event_urls: list[str] = field(default_factory=list)
    notified: dict[str, str] = field(default_factory=dict)
    consecutive_fetch_failures: int = 0
    fetch_failure_notified: bool = False

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.__dict__, indent=2) + "\n")


def fetch_html(url: str) -> str | None:
    for attempt in (1, 2):
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            print(f"WARNING: fetch attempt {attempt} failed for {url}: {exc}", file=sys.stderr)
    return None


def extract_event_links(html: str) -> list[tuple[str, str]]:
    """Return (absolute_url, link_text) pairs for event-page links."""
    links = []
    for href, inner in EVENT_LINK_PATTERN.findall(html):
        url = href if href.startswith("http") else BASE_URL + href
        url = url.split("?")[0].rstrip("/")
        text = unescape(TAG_STRIP_PATTERN.sub(" ", inner))
        text = re.sub(r"\s+", " ", text).strip()
        links.append((url, text))
    return links


def find_ticket_link(html: str) -> str | None:
    match = TICKET_PLATFORM_PATTERN.search(html)
    if match:
        return match.group(0)
    if BUY_TEXT_PATTERN.search(html):
        return "on the event page"
    return None


def send_notification(title: str, message: str, priority: str = "default", tags: str = "soccer") -> None:
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    response = requests.post(NTFY_URL, data=message.encode("utf-8"), headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    # Don't echo the URL or response body: this repo is public, so Actions
    # logs are public too, and both would reveal the ntfy topic string.
    print(f"ntfy POST -> {response.status_code}")
    response.raise_for_status()


def notify_once(state: State, key: str, title: str, message: str, priority: str, dry_run: bool, tags: str = "soccer") -> None:
    if key in state.notified:
        print(f"[skip] already notified: {key}")
        return
    if dry_run:
        print(f"DRY RUN -- would send [{priority}] {title}: {message}")
    else:
        send_notification(title, message, priority=priority, tags=tags)
        state.notified[key] = datetime.now(ZoneInfo("UTC")).isoformat()


def run(dry_run: bool, force: bool) -> None:
    now = datetime.now(ZoneInfo("Europe/London"))
    if now >= MONITOR_END and not force:
        print(f"Nothing to do at {now:%Y-%m-%d %H:%M %Z} (monitoring ended {MONITOR_END:%Y-%m-%d %H:%M}).")
        return

    state = State.load(STATE_FILE)

    pages: dict[str, str] = {}
    for url in HUB_URLS:
        html = fetch_html(url)
        if html:
            pages[url] = html

    if not pages:
        state.consecutive_fetch_failures += 1
        print(f"ERROR: all hub page fetches failed ({state.consecutive_fetch_failures} consecutive).", file=sys.stderr)
        if state.consecutive_fetch_failures >= FETCH_FAILURES_BEFORE_ALERT and not state.fetch_failure_notified:
            if dry_run:
                print("DRY RUN -- would send fetch-failure warning")
            else:
                send_notification(
                    "Ticket monitor can't reach the site",
                    f"{state.consecutive_fetch_failures} runs in a row failed to load "
                    "betweenthebridges.co.uk - the England v Argentina watch is blind. "
                    "It may be blocking automated checks; worth checking the page yourself.",
                    priority="high",
                    tags="warning",
                )
                state.fetch_failure_notified = True
        if not dry_run:
            state.save(STATE_FILE)
        return

    if state.fetch_failure_notified:
        # We warned that the monitor was blind; say it can see again.
        if dry_run:
            print("DRY RUN -- would send recovery notice")
        else:
            send_notification(
                "Ticket monitor back online",
                "Reaching betweenthebridges.co.uk again; watching for England v Argentina tickets as normal.",
                priority="default",
                tags="white_check_mark",
            )
        state.fetch_failure_notified = False
    state.consecutive_fetch_failures = 0

    all_links: dict[str, str] = {}
    for html in pages.values():
        for url, text in extract_event_links(html):
            # Keep the longest text seen for a URL (some links are images with
            # no text; others carry the event title).
            if len(text) > len(all_links.get(url, "")):
                all_links[url] = text

    first_run = not state.seeded
    known = set(state.known_event_urls)
    target_urls = []
    for url, text in sorted(all_links.items()):
        target = is_target(f"{url} {text}")
        if target:
            target_urls.append(url)
        if url in known:
            continue
        known.add(url)
        print(f"[new link] {url} ({text or 'no text'}){' [TARGET]' if target else ''}")
        if target:
            # Ticket check below sends the (possibly combined) urgent alert.
            continue
        if not first_run:
            notify_once(
                state, f"{url}::event",
                "New event on BTB World Cup page",
                f"Not obviously the England v Argentina match, but new: {text or url}\n{url}",
                priority="default", dry_run=dry_run,
            )

    state.known_event_urls = sorted(known)
    state.seeded = True

    for url in target_urls:
        event_html = fetch_html(url)
        ticket_link = find_ticket_link(event_html) if event_html else None
        sold_out = bool(event_html and SOLD_OUT_PATTERN.search(event_html))
        title_text = all_links.get(url) or "England v Argentina at Between The Bridges"

        if ticket_link and not sold_out:
            notify_once(
                state, f"{url}::tickets",
                "England v Argentina TICKETS ON SALE",
                f"{title_text}\nBook: {ticket_link if ticket_link.startswith('http') else url}\nEvent page: {url}",
                priority="urgent", dry_run=dry_run,
                tags="rotating_light,soccer",
            )
        elif sold_out:
            notify_once(
                state, f"{url}::soldout",
                "England v Argentina - showing as SOLD OUT",
                f"{title_text}\n{url}",
                priority="default", dry_run=dry_run,
                tags="no_entry",
            )
        else:
            notify_once(
                state, f"{url}::event",
                "England v Argentina event page is LIVE",
                f"{title_text}\nNo ticket link on it yet - will keep watching and alert again when tickets appear.\n{url}",
                priority="urgent", dry_run=dry_run,
                tags="rotating_light,soccer",
            )

    if not target_urls:
        print(f"No England v Argentina event yet ({len(all_links)} event links seen). Watching.")

    if not dry_run:
        state.save(STATE_FILE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="don't send notifications or write state")
    parser.add_argument("--force", action="store_true", help="run even after the monitoring end time")
    args = parser.parse_args()
    run(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
