#!/usr/bin/env python3
"""
Santander Cycles dock-availability alerter.

Checks the TfL BikePoint API for Tooley Street, Bermondsey (BikePoints_278)
and pushes notifications to a phone via ntfy.sh when docks get full.

Designed to be run repeatedly (e.g. every 5 minutes) by a GitHub Actions
cron schedule. Because cron in GitHub Actions runs in UTC and the UK
switches between GMT and BST, this script does its own timezone-aware
check using Europe/London (see `determine_mode`) rather than trusting the
exact minute it was invoked. The workflow schedule casts a wider net in
UTC; this script decides whether "now" (in London time) actually falls
inside the windows we care about, and does nothing otherwise. That means
DST is handled correctly year-round with zero manual offset maths.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# --------------------------------------------------------------------------
# Config -- tweak these without touching the logic below.
# --------------------------------------------------------------------------

STATION_ID = "BikePoints_278"  # Tooley Street, Bermondsey
TFL_URL = f"https://api.tfl.gov.uk/BikePoint/{STATION_ID}"
EXPECTED_NAME_FRAGMENT = "Tooley Street"  # sanity check against the API response

# Alert when empty docks drop below this number.
LOW_DOCKS_THRESHOLD = 3

# Send "all clear" once empty docks recover to at least this number
# (only if we'd previously sent a low-docks alert).
ALL_CLEAR_THRESHOLD = 5

# Don't send more than one low-docks alert within this many minutes.
ALERT_COOLDOWN_MINUTES = 30

# ntfy.sh topic. Override with the NTFY_TOPIC env var if you want to change
# it without editing code (e.g. via a GitHub Actions repo variable).
#
# GitHub Actions sets NTFY_TOPIC to an empty string (not absent) when the
# repo variable isn't configured, so `os.environ.get(..., DEFAULT)` alone
# would silently send to the topic-less "https://ntfy.sh/" -- use `or` to
# treat an empty value the same as unset.
DEFAULT_NTFY_TOPIC = "harry-tooley-docks-5494e935"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or DEFAULT_NTFY_TOPIC
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

# Morning monitoring window, in Europe/London local time.
MORNING_SUMMARY_TIME = time(7, 45)
CHECK_WINDOW_START = time(8, 0)
CHECK_WINDOW_END = time(8, 45)

# Run-day check: only Monday (0) through Thursday (3).
ACTIVE_WEEKDAYS = {0, 1, 2, 3}

# TODO (later, not built yet): add a second evening schedule for the reverse
# commute -- summary at 17:00, checks every 5 min from 17:30 to 18:00 -- and
# a "mute today" toggle (e.g. a flag file checked at the top of main() that
# short-circuits everything until midnight). Keep it as a separate mode so
# the morning logic above is untouched.

STATE_FILE = Path(__file__).parent / "state.json"
LONDON = ZoneInfo("Europe/London")
REQUEST_TIMEOUT_SECONDS = 10


@dataclass
class State:
    alerted: bool = False
    last_alert_time: str | None = None  # ISO 8601, UTC

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(**data)
        except (json.JSONDecodeError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")


def determine_mode(now_london: datetime, force_mode: str | None) -> str | None:
    """Decide what to do right now: 'summary', 'check', or None (do nothing)."""
    if force_mode and force_mode != "auto":
        return force_mode

    if now_london.weekday() not in ACTIVE_WEEKDAYS:
        return None

    t = now_london.time()

    # 5-minute-wide window starting at the summary time, so the morning
    # summary fires once even if the runner is a little late.
    summary_end = (
        datetime.combine(now_london.date(), MORNING_SUMMARY_TIME) + timedelta(minutes=5)
    ).time()
    if MORNING_SUMMARY_TIME <= t < summary_end:
        return "summary"

    if CHECK_WINDOW_START <= t <= CHECK_WINDOW_END:
        return "check"

    return None


def fetch_empty_docks() -> tuple[int, str]:
    """Return (empty_dock_count, station_name) from the TfL BikePoint API."""
    response = requests.get(TFL_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()

    station_name = data.get("commonName", "")
    if EXPECTED_NAME_FRAGMENT not in station_name:
        print(
            f"WARNING: station name '{station_name}' does not contain "
            f"'{EXPECTED_NAME_FRAGMENT}' -- check STATION_ID is still correct.",
            file=sys.stderr,
        )

    props = {p["key"]: p["value"] for p in data.get("additionalProperties", [])}
    if "NbEmptyDocks" not in props:
        raise RuntimeError(
            "NbEmptyDocks not found in additionalProperties -- TfL API shape "
            "may have changed. Raw response: " + json.dumps(data)[:500]
        )

    return int(props["NbEmptyDocks"]), station_name


def send_notification(title: str, message: str, priority: str = "default", tags: str = "bike") -> None:
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }
    response = requests.post(NTFY_URL, data=message.encode("utf-8"), headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    print(f"ntfy POST to {NTFY_URL} -> {response.status_code}: {response.text[:300]}")
    response.raise_for_status()


def run(mode: str, dry_run: bool) -> None:
    empty_docks, station_name = fetch_empty_docks()
    print(f"[{mode}] {station_name}: {empty_docks} empty docks")

    state = State.load(STATE_FILE)
    now_utc = datetime.now(ZoneInfo("UTC"))

    if mode == "summary":
        title = "Tooley Street docks - morning check"
        message = f"{empty_docks} empty docks available right now."
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,sunny")

        # Fresh monitoring window starting -- clear any stale alert state.
        state = State()
        state.save(STATE_FILE)
        return

    if mode == "check":
        if empty_docks < LOW_DOCKS_THRESHOLD:
            cooldown_active = False
            if state.alerted and state.last_alert_time:
                last_alert = datetime.fromisoformat(state.last_alert_time)
                cooldown_active = (now_utc - last_alert) < timedelta(minutes=ALERT_COOLDOWN_MINUTES)

            if not cooldown_active:
                title = "Tooley Street docks - LOW"
                message = (
                    f"Only {empty_docks} empty docks left (threshold {LOW_DOCKS_THRESHOLD}). "
                    "Consider an alternative dock."
                )
                if dry_run:
                    print(f"DRY RUN -- would send: {title} / {message}")
                else:
                    send_notification(title, message, priority="high", tags="bike,warning")
                state.alerted = True
                state.last_alert_time = now_utc.isoformat()
            else:
                print("Low docks, but still within cooldown -- not re-alerting.")

        elif empty_docks >= ALL_CLEAR_THRESHOLD and state.alerted:
            title = "Tooley Street docks - all clear"
            message = f"Back up to {empty_docks} empty docks."
            if dry_run:
                print(f"DRY RUN -- would send: {title} / {message}")
            else:
                send_notification(title, message, priority="default", tags="bike,white_check_mark")
            state.alerted = False
            state.last_alert_time = None

        if not dry_run:
            state.save(STATE_FILE)
        return

    raise ValueError(f"Unknown mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force-mode",
        choices=["auto", "summary", "check"],
        default="auto",
        help="Override the time-based mode detection, e.g. for manual testing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print, but don't send a notification or write state.",
    )
    args = parser.parse_args()

    now_london = datetime.now(LONDON)
    mode = determine_mode(now_london, args.force_mode)

    if mode is None:
        print(
            f"Nothing to do at {now_london.isoformat()} (outside monitoring window or weekday)."
        )
        return

    run(mode, args.dry_run)


if __name__ == "__main__":
    main()
