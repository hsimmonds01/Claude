#!/usr/bin/env python3
"""Watch the VOXI Drop page (https://www.voxi.co.uk/voxi-drop) and push a
phone notification via ntfy.sh when the monthly Drop goes live.

The Drop lands on a random day each month, first-come first-served, so the
watch runs all month and re-arms itself each month. Notifications:

1. "VOXI Drop is LIVE" (urgent) -- the page switches to its claimable state.
   Sent once per calendar month.
2. "VOXI Drop page changed" (default) -- the page's drop-related wording
   changed but the script can't tell what it means (safety net; also likely
   to catch a "dropping tomorrow" teaser). Once per distinct change.
3. "Monitor can't reach the site" (high) -- several consecutive runs failed
   to fetch the page, i.e. the watch is blind, not just quiet.

Detection is keyword-based over the raw HTML (including any embedded JSON),
classifying the page as live / closed / teaser / unknown. Use --recon to
print everything the classifier sees (status, title, matched keywords,
button/link texts) for tuning against the real page from GitHub Actions,
where the site is reachable.

State (one-shot alert tracking, keyed by month) lives in state.json and is
committed back by the workflow -- same pattern as dock-alerter and the
retired ticket-alerter this is adapted from.
"""

from __future__ import annotations

import argparse
import hashlib
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

DROP_URL = "https://www.voxi.co.uk/voxi-drop"

# Keyword sets for classifying the page. Deliberately generous on first
# deploy -- tune against --recon output from a real run. Matched against
# lowercased raw HTML, so embedded JSON/app-state counts too.
LIVE_KEYWORDS = [
    "claim now",
    "claim your reward",
    "claim reward",
    "get your code",
    "get the code",
    "grab yours",
    "redeem now",
    "sign in to claim",
    "log in to claim",
    "drop is live",
    "it's live",
]
CLOSED_KEYWORDS = [
    "you missed",
    "you've missed",
    "missed this month",
    "come back next month",
    "next drop",
    "drop has ended",
    "drop is closed",
    "all gone",
    "been claimed",
    "keep your eyes peeled",
    "keep an eye on",
]
TEASER_KEYWORDS = [
    "dropping tomorrow",
    "drops tomorrow",
    "dropping soon",
    "drops soon",
    "coming soon",
    "get ready",
    "countdown",
]

CTA_TEXT_PATTERN = re.compile(
    r"<(?:a|button)\b[^>]*>(.*?)</(?:a|button)>", re.IGNORECASE | re.DOTALL
)
TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TAG_STRIP_PATTERN = re.compile(r"<[^>]+>")

REQUEST_TIMEOUT_SECONDS = 20
FETCH_FAILURES_BEFORE_ALERT = 3

# Look like a normal browser -- big-brand sites often 403 obvious bots.
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
    notified: dict[str, str] = field(default_factory=dict)
    last_classification: str = ""
    last_fingerprint: str = ""
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


def matched_keywords(html_lower: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if k in html_lower]


def classify(html: str) -> tuple[str, dict[str, list[str]]]:
    """Return (classification, matches). Classification is one of
    live / closed / teaser / unknown. Live wins over teaser; a page showing
    both live and closed wording is ambiguous -> unknown (better a soft
    'page changed' alert than a wrong LIVE one).
    """
    h = html.lower()
    matches = {
        "live": matched_keywords(h, LIVE_KEYWORDS),
        "closed": matched_keywords(h, CLOSED_KEYWORDS),
        "teaser": matched_keywords(h, TEASER_KEYWORDS),
    }
    if matches["live"] and not matches["closed"]:
        return "live", matches
    if matches["closed"] and not matches["live"]:
        return "closed", matches
    if matches["teaser"]:
        return "teaser", matches
    return "unknown", matches


def fingerprint(matches: dict[str, list[str]]) -> str:
    """A stable digest of which drop-related keywords are on the page, so we
    can notice meaningful wording changes without diffing volatile HTML."""
    canonical = json.dumps(matches, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def recon(html: str | None) -> None:
    """Print everything the classifier sees, for tuning the keyword sets."""
    if html is None:
        print("RECON: fetch FAILED -- see warnings above.")
        return
    h = html.lower()
    title = TITLE_PATTERN.search(html)
    print(f"RECON: fetched {len(html)} chars")
    print(f"RECON: title: {title.group(1).strip() if title else '(none)'}")
    classification, matches = classify(html)
    print(f"RECON: classification: {classification}")
    for bucket, hits in matches.items():
        print(f"RECON: {bucket} keyword hits: {hits or '(none)'}")
    words = ("drop", "claim", "reward", "redeem", "code")
    ctas = []
    for inner in CTA_TEXT_PATTERN.findall(html):
        text = unescape(TAG_STRIP_PATTERN.sub(" ", inner))
        text = re.sub(r"\s+", " ", text).strip()
        if text and any(w in text.lower() for w in words):
            ctas.append(text)
    print(f"RECON: drop-related link/button texts ({len(ctas)}):")
    for t in dict.fromkeys(ctas):
        print(f"RECON:   - {t}")
    for w in words:
        print(f"RECON: raw count of '{w}': {h.count(w)}")
    visible = re.sub(r"\s+", " ", TAG_STRIP_PATTERN.sub(" ", re.sub(
        r"<(script|style)\b.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)))
    print(f"RECON: visible text ({len(visible)} chars), first 1500:")
    print("RECON: " + visible[:1500])


def send_notification(title: str, message: str, priority: str = "default", tags: str = "gift") -> None:
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    response = requests.post(NTFY_URL, data=message.encode("utf-8"), headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    # Don't echo the URL or response body: this repo is public, so Actions
    # logs are public too, and both would reveal the ntfy topic string.
    print(f"ntfy POST -> {response.status_code}")
    response.raise_for_status()


def notify_once(state: State, key: str, title: str, message: str, priority: str, dry_run: bool, tags: str = "gift") -> None:
    if key in state.notified:
        print(f"[skip] already notified: {key}")
        return
    if dry_run:
        print(f"DRY RUN -- would send [{priority}] {title}: {message}")
    else:
        send_notification(title, message, priority=priority, tags=tags)
        state.notified[key] = datetime.now(ZoneInfo("UTC")).isoformat()


def run(dry_run: bool, recon_mode: bool) -> None:
    state = State.load(STATE_FILE)
    month = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m")

    html = fetch_html(DROP_URL)

    if recon_mode:
        recon(html)
        return

    if html is None:
        state.consecutive_fetch_failures += 1
        print(f"ERROR: fetch failed ({state.consecutive_fetch_failures} consecutive).", file=sys.stderr)
        if state.consecutive_fetch_failures >= FETCH_FAILURES_BEFORE_ALERT and not state.fetch_failure_notified:
            if dry_run:
                print("DRY RUN -- would send fetch-failure warning")
            else:
                send_notification(
                    "VOXI Drop monitor can't reach the site",
                    f"{state.consecutive_fetch_failures} runs in a row failed to load "
                    "voxi.co.uk/voxi-drop - the Drop watch is blind. It may be "
                    "blocking automated checks; worth checking the page yourself.",
                    priority="high",
                    tags="warning",
                )
                state.fetch_failure_notified = True
        if not dry_run:
            state.save(STATE_FILE)
        return

    if state.fetch_failure_notified:
        if dry_run:
            print("DRY RUN -- would send recovery notice")
        else:
            send_notification(
                "VOXI Drop monitor back online",
                "Reaching voxi.co.uk again; watching for the Drop as normal.",
                priority="default",
                tags="white_check_mark",
            )
        state.fetch_failure_notified = False
    state.consecutive_fetch_failures = 0

    classification, matches = classify(html)
    fp = fingerprint(matches)
    print(f"classification={classification} fingerprint={fp} matches={matches}")

    if classification == "live":
        notify_once(
            state, f"{month}::live",
            "VOXI Drop is LIVE",
            f"This month's VOXI Drop looks claimable right now (first come, first served!)\n"
            f"Matched: {', '.join(matches['live'])}\n{DROP_URL}",
            priority="urgent", dry_run=dry_run,
            tags="rotating_light,gift",
        )
    elif classification == "teaser":
        notify_once(
            state, f"{month}::teaser",
            "VOXI Drop coming soon",
            f"The Drop page is teasing this month's drop.\n"
            f"Matched: {', '.join(matches['teaser'])}\n{DROP_URL}",
            priority="default", dry_run=dry_run,
        )
    elif (
        "unknown" in (classification, state.last_classification)
        and state.last_fingerprint
        and fp != state.last_fingerprint
    ):
        # Wording changed and we can't (or couldn't) tell what it means --
        # worth a look. Transitions between well-understood states (live ->
        # closed after a drop ends, etc.) stay quiet.
        notify_once(
            state, f"{month}::changed::{fp}",
            "VOXI Drop page changed",
            f"The Drop page's wording changed (now reads as '{classification}', "
            f"was '{state.last_classification}') - might be worth a look.\n{DROP_URL}",
            priority="default", dry_run=dry_run,
        )

    state.last_classification = classification
    state.last_fingerprint = fp

    # Drop old months' one-shot records so state.json doesn't grow forever.
    state.notified = {k: v for k, v in state.notified.items() if k.startswith(month)}

    if not dry_run:
        state.save(STATE_FILE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="don't send notifications or write state")
    parser.add_argument("--recon", action="store_true", help="print page analysis for tuning, no alerts, no state")
    args = parser.parse_args()
    run(dry_run=args.dry_run, recon_mode=args.recon)


if __name__ == "__main__":
    main()
