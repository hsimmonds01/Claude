#!/usr/bin/env python3
"""
Santander Cycles dock-availability alerter.

Checks the TfL BikePoint API for Tooley Street, Bermondsey (BikePoints_278)
and pushes notifications to a phone via ntfy.sh:

  - Morning (outbound commute): alert when empty DOCKS get low, so you know
    whether you'll be able to return a bike there later.
  - Evening (return commute): alert when available BIKES get low, so you
    know whether you'll be able to pick one up. If bikes are low, also
    checks Snowsfields, London Bridge as a nearby backup and includes its
    bike count in the alert.

Designed to be run repeatedly (e.g. every 5 minutes) by a GitHub Actions
cron schedule. Because cron in GitHub Actions runs in UTC and the UK
switches between GMT and BST, this script does its own timezone-aware
check using Europe/London (see `determine_mode`) rather than trusting the
exact minute it was invoked. The workflow schedule casts a wider net in
UTC; this script decides whether "now" (in London time) actually falls
inside the windows we care about, and does nothing otherwise. That means
DST is handled correctly year-round with zero manual offset maths.

A mute.flag file (containing today's date, Europe/London) next to this
script silences all *automatic* (cron-driven) runs for the rest of the
day -- see `is_muted_today`. Manual runs via --force-mode bypass it.
"""

from __future__ import annotations

import argparse
import csv
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

TFL_BASE_URL = "https://api.tfl.gov.uk/BikePoint"

STATION_ID = "BikePoints_278"  # Tooley Street, Bermondsey
EXPECTED_NAME_FRAGMENT = "Tooley Street"  # sanity check against the API response

# Name to search for when looking up the evening backup station. Looked up
# by name (rather than a hardcoded BikePoint ID) at runtime via the TfL
# Search endpoint, since IDs aren't worth memorising and this is robust to
# any future renumbering.
SECONDARY_STATION_QUERY = "Snowsfields"

# Morning: alert when empty docks drop below this number.
LOW_DOCKS_THRESHOLD = 3
# Morning: send "all clear" once empty docks recover to at least this number
# (only if we'd previously sent a low-docks alert).
ALL_CLEAR_DOCKS_THRESHOLD = 5

# Evening: alert when available bikes drop below this number.
LOW_BIKES_THRESHOLD = 5
# Evening: second, more urgent alert when bikes drop even further.
CRITICAL_BIKES_THRESHOLD = 3
# Evening: send "all clear" once available bikes recover to at least this
# number (only if we'd previously sent a low-bikes alert).
ALL_CLEAR_BIKES_THRESHOLD = 6

# Set True to count only standard (non-electric) bikes. TfL returns NbEBikes
# alongside NbBikes; when True, the reported count is NbBikes - NbEBikes.
# If the NbEBikes field is ever missing from the API response, the script
# falls back silently to using NbBikes unchanged rather than erroring.
EXCLUDE_EBIKES = True

# Don't send more than one low-docks/low-bikes alert within this many minutes.
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
MORNING_BIKES_TIME = time(8, 20)  # one-off mid-morning bike count snapshot
MORNING_CHECK_START = time(8, 0)
MORNING_CHECK_END = time(8, 45)

# Evening monitoring window, in Europe/London local time.
EVENING_SUMMARY_TIME = time(17, 15)
EVENING_SECOND_SUMMARY_TIME = time(17, 40)  # one-off mid-evening bike count snapshot
EVENING_CHECK_START = time(17, 30)
EVENING_CHECK_END = time(18, 0)

# Run-day check: only Monday (0) through Thursday (3), for both windows.
ACTIVE_WEEKDAYS = {0, 1, 2, 3}

STATE_FILE = Path(__file__).parent / "state.json"
MUTE_FILE = Path(__file__).parent / "mute.flag"
HISTORY_FILE = Path(__file__).parent / "history.csv"
LONDON = ZoneInfo("Europe/London")
REQUEST_TIMEOUT_SECONDS = 10


@dataclass
class State:
    alerted: bool = False
    last_alert_time: str | None = None  # ISO 8601, UTC
    evening_alerted: bool = False
    evening_critical_alerted: bool = False  # True once the second-tier critical alert fires
    evening_last_alert_time: str | None = None  # ISO 8601, UTC

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


def is_muted_today(now_london: datetime) -> bool:
    """True if mute.flag exists and names today's London date."""
    if not MUTE_FILE.exists():
        return False
    try:
        flagged_date = MUTE_FILE.read_text().strip()
    except OSError:
        return False
    return flagged_date == now_london.date().isoformat()


def determine_mode(now_london: datetime, force_mode: str | None) -> str | None:
    """Decide what to do right now: one of 'summary', 'check',
    'evening_summary', 'evening_check', or None (do nothing)."""
    if force_mode and force_mode != "auto":
        return force_mode

    if now_london.weekday() not in ACTIVE_WEEKDAYS:
        return None

    t = now_london.time()

    # 5-minute-wide windows starting at each summary time, so the summary
    # fires once even if the runner is a little late.
    def starts_window(start: time, width_minutes: int = 5) -> bool:
        end = (datetime.combine(now_london.date(), start) + timedelta(minutes=width_minutes)).time()
        return start <= t < end

    if starts_window(MORNING_SUMMARY_TIME):
        return "summary"
    if starts_window(MORNING_BIKES_TIME):
        return "morning_bikes"
    if MORNING_CHECK_START <= t <= MORNING_CHECK_END:
        return "check"
    if starts_window(EVENING_SUMMARY_TIME):
        return "evening_summary"
    if starts_window(EVENING_SECOND_SUMMARY_TIME):
        return "evening_second_summary"
    if EVENING_CHECK_START <= t <= EVENING_CHECK_END:
        return "evening_check"

    return None


def _fetch_bikepoint(station_id: str) -> dict:
    url = f"{TFL_BASE_URL}/{station_id}"
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _search_bikepoint(name_query: str) -> dict | None:
    url = f"{TFL_BASE_URL}/Search"
    response = requests.get(url, params={"query": name_query}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    results = response.json()
    return results[0] if results else None


def _props(data: dict) -> dict:
    return {p["key"]: p["value"] for p in data.get("additionalProperties", [])}


def fetch_empty_docks() -> tuple[int, str]:
    """Return (empty_dock_count, station_name) for Tooley Street."""
    data = _fetch_bikepoint(STATION_ID)
    station_name = data.get("commonName", "")
    if EXPECTED_NAME_FRAGMENT not in station_name:
        print(
            f"WARNING: station name '{station_name}' does not contain "
            f"'{EXPECTED_NAME_FRAGMENT}' -- check STATION_ID is still correct.",
            file=sys.stderr,
        )

    props = _props(data)
    if "NbEmptyDocks" not in props:
        raise RuntimeError(
            "NbEmptyDocks not found in additionalProperties -- TfL API shape "
            "may have changed. Raw response: " + json.dumps(data)[:500]
        )
    return int(props["NbEmptyDocks"]), station_name


def _count_bikes(props: dict) -> int:
    """Return the bike count to use, optionally excluding e-bikes."""
    total = int(props.get("NbBikes", 0))
    if not EXCLUDE_EBIKES:
        return total
    ebikes = int(props.get("NbEBikes", 0))
    if "NbEBikes" not in props:
        print("WARNING: NbEBikes not in API response -- falling back to NbBikes total.", file=sys.stderr)
    return max(0, total - ebikes)


def fetch_available_bikes() -> tuple[int, str]:
    """Return (available_bike_count, station_name) for Tooley Street."""
    data = _fetch_bikepoint(STATION_ID)
    station_name = data.get("commonName", "")
    if EXPECTED_NAME_FRAGMENT not in station_name:
        print(
            f"WARNING: station name '{station_name}' does not contain "
            f"'{EXPECTED_NAME_FRAGMENT}' -- check STATION_ID is still correct.",
            file=sys.stderr,
        )

    props = _props(data)
    if "NbBikes" not in props:
        raise RuntimeError(
            "NbBikes not found in additionalProperties -- TfL API shape "
            "may have changed. Raw response: " + json.dumps(data)[:500]
        )
    return _count_bikes(props), station_name


def fetch_status() -> tuple[int, int, str]:
    """Return (empty_docks, available_bikes, station_name) for Tooley Street
    in a single API call -- for the anytime on-demand status check, which
    cares about both numbers and isn't tied to morning/evening semantics."""
    data = _fetch_bikepoint(STATION_ID)
    station_name = data.get("commonName", "")
    if EXPECTED_NAME_FRAGMENT not in station_name:
        print(
            f"WARNING: station name '{station_name}' does not contain "
            f"'{EXPECTED_NAME_FRAGMENT}' -- check STATION_ID is still correct.",
            file=sys.stderr,
        )

    props = _props(data)
    if "NbEmptyDocks" not in props or "NbBikes" not in props:
        raise RuntimeError(
            "NbEmptyDocks/NbBikes not found in additionalProperties -- TfL API "
            "shape may have changed. Raw response: " + json.dumps(data)[:500]
        )
    return int(props["NbEmptyDocks"]), _count_bikes(props), station_name


def fetch_secondary_bikes() -> tuple[int, str] | None:
    """Best-effort lookup of the backup station's bike count.

    Returns None on any failure -- this is a nice-to-have addition to the
    low-bikes alert, not something that should ever block it.
    """
    try:
        data = _search_bikepoint(SECONDARY_STATION_QUERY)
        if data is None:
            print(f"WARNING: no BikePoint found matching '{SECONDARY_STATION_QUERY}'.", file=sys.stderr)
            return None
        props = _props(data)
        if "NbBikes" not in props:
            return None
        return _count_bikes(props), data.get("commonName", SECONDARY_STATION_QUERY)
    except requests.RequestException as exc:
        print(f"WARNING: secondary station lookup failed: {exc}", file=sys.stderr)
        return None


def log_history(mode: str, metric: str, value: int, station_name: str) -> None:
    """Append one row to history.csv -- a running log of every reading, for
    spotting patterns later (e.g. a future dashboard or trend-based alerts).
    Writes a header row the first time the file is created.
    """
    is_new_file = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow(["timestamp_utc", "mode", "metric", "value", "station"])
        writer.writerow([datetime.now(ZoneInfo("UTC")).isoformat(), mode, metric, value, station_name])


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
    state = State.load(STATE_FILE)
    now_utc = datetime.now(ZoneInfo("UTC"))

    if mode == "summary":
        empty_docks, station_name = fetch_empty_docks()
        print(f"[{mode}] {station_name}: {empty_docks} empty docks")
        if not dry_run:
            log_history(mode, "empty_docks", empty_docks, station_name)

        title = "Tooley Street docks - morning check"
        message = f"{empty_docks} empty docks available right now."
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,sunny")

        # Fresh monitoring window starting -- clear any stale morning alert
        # state, leaving the evening state untouched.
        state.alerted = False
        state.last_alert_time = None
        if not dry_run:
            state.save(STATE_FILE)
        return

    if mode == "check":
        empty_docks, station_name = fetch_empty_docks()
        print(f"[{mode}] {station_name}: {empty_docks} empty docks")
        if not dry_run:
            log_history(mode, "empty_docks", empty_docks, station_name)

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

        elif empty_docks >= ALL_CLEAR_DOCKS_THRESHOLD and state.alerted:
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

    if mode == "morning_bikes":
        bikes, station_name = fetch_available_bikes()
        bike_label = "standard bikes" if EXCLUDE_EBIKES else "bikes"
        print(f"[{mode}] {station_name}: {bikes} {bike_label} available")
        if not dry_run:
            log_history(mode, "available_bikes", bikes, station_name)

        title = "Tooley Street bikes - 08:20 check"
        message = f"{bikes} {bike_label} available right now."
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,mag")
        return

    if mode == "evening_summary":
        bikes, station_name = fetch_available_bikes()
        print(f"[{mode}] {station_name}: {bikes} bikes available")
        if not dry_run:
            log_history(mode, "available_bikes", bikes, station_name)

        bike_label = "standard bikes" if EXCLUDE_EBIKES else "bikes"
        title = "Tooley Street bikes - evening check"
        message = f"{bikes} {bike_label} available right now."
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,sunny")

        # Fresh monitoring window starting -- clear any stale evening alert
        # state, leaving the morning state untouched.
        state.evening_alerted = False
        state.evening_last_alert_time = None
        if not dry_run:
            state.save(STATE_FILE)
        return

    if mode == "evening_second_summary":
        bikes, station_name = fetch_available_bikes()
        bike_label = "standard bikes" if EXCLUDE_EBIKES else "bikes"
        print(f"[{mode}] {station_name}: {bikes} {bike_label} available")
        if not dry_run:
            log_history(mode, "available_bikes", bikes, station_name)

        title = "Tooley Street bikes - 17:40 check"
        message = f"{bikes} {bike_label} available right now."
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,mag")
        return

    if mode == "evening_check":
        bikes, station_name = fetch_available_bikes()
        print(f"[{mode}] {station_name}: {bikes} bikes available")
        if not dry_run:
            log_history(mode, "available_bikes", bikes, station_name)

        bike_label = "standard bikes" if EXCLUDE_EBIKES else "bikes"

        def cooldown_active() -> bool:
            if state.evening_last_alert_time:
                last = datetime.fromisoformat(state.evening_last_alert_time)
                return (now_utc - last) < timedelta(minutes=ALERT_COOLDOWN_MINUTES)
            return False

        if bikes < CRITICAL_BIKES_THRESHOLD and not state.evening_critical_alerted:
            # Second-tier critical alert -- fires the moment we drop to critical
            # level even if still within cooldown of the first-tier low alert.
            title = "Tooley Street bikes - CRITICAL"
            message = f"Only {bikes} {bike_label} left at Tooley Street!"

            secondary = fetch_secondary_bikes()
            if secondary is not None:
                secondary_bikes, secondary_name = secondary
                message += f" {secondary_name} has {secondary_bikes} {bike_label} as a backup."

            if dry_run:
                print(f"DRY RUN -- would send: {title} / {message}")
            else:
                send_notification(title, message, priority="urgent", tags="bike,rotating_light")
            state.evening_alerted = True
            state.evening_critical_alerted = True
            state.evening_last_alert_time = now_utc.isoformat()

        elif bikes < LOW_BIKES_THRESHOLD:
            if not state.evening_alerted or not cooldown_active():
                title = "Tooley Street bikes - LOW"
                message = f"Only {bikes} {bike_label} left at Tooley Street (threshold {LOW_BIKES_THRESHOLD})."

                secondary = fetch_secondary_bikes()
                if secondary is not None:
                    secondary_bikes, secondary_name = secondary
                    message += f" {secondary_name} has {secondary_bikes} {bike_label} available as a backup."

                if dry_run:
                    print(f"DRY RUN -- would send: {title} / {message}")
                else:
                    send_notification(title, message, priority="high", tags="bike,warning")
                state.evening_alerted = True
                state.evening_last_alert_time = now_utc.isoformat()
            else:
                print("Low bikes, but still within cooldown -- not re-alerting.")

        elif bikes >= ALL_CLEAR_BIKES_THRESHOLD and state.evening_alerted:
            bike_label = "standard bikes" if EXCLUDE_EBIKES else "bikes"
            title = "Tooley Street bikes - all clear"
            message = f"Back up to {bikes} {bike_label} available."
            if dry_run:
                print(f"DRY RUN -- would send: {title} / {message}")
            else:
                send_notification(title, message, priority="default", tags="bike,white_check_mark")
            state.evening_alerted = False
            state.evening_critical_alerted = False
            state.evening_last_alert_time = None

        if not dry_run:
            state.save(STATE_FILE)
        return

    if mode == "status":
        empty_docks, bikes, station_name = fetch_status()
        print(f"[{mode}] {station_name}: {empty_docks} empty docks, {bikes} bikes available")
        if not dry_run:
            log_history(mode, "empty_docks", empty_docks, station_name)
            log_history(mode, "available_bikes", bikes, station_name)

        bike_label = "standard bikes" if EXCLUDE_EBIKES else "bikes"
        title = "Tooley Street - status check"
        message = f"{empty_docks} empty docks, {bikes} {bike_label} available right now."
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,mag")
        return

    raise ValueError(f"Unknown mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force-mode",
        choices=["auto", "summary", "morning_bikes", "check", "evening_summary", "evening_second_summary", "evening_check", "status"],
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

    if args.force_mode == "auto" and is_muted_today(now_london):
        print(f"Muted for today ({now_london.date().isoformat()}) -- skipping.")
        return

    run(mode, args.dry_run)


if __name__ == "__main__":
    main()
