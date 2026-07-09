#!/usr/bin/env python3
"""
Northern line busyness logger.

Polls the TfL Unified API's near-real-time crowding feed for the seven
Northern line stations along a Balham/Clapham commute corridor and appends
each reading to history.csv, building up a record of how busy each station
is through the morning and evening commute windows.

  Live endpoint:    https://api.tfl.gov.uk/crowding/{naptan}/Live
  Typical endpoint: https://api.tfl.gov.uk/crowding/{naptan}   (day/time-band profile)

What percentageOfBaseline means (per TfL's own announcement):
  It is NOT a headcount. It is a station-level busyness index derived from
  anonymised, aggregated station WiFi data, expressed as a fraction of the
  busiest WEEK that station has seen since data collection began in July
  2019. So 1.0 = "as busy as this station's busiest-ever week"; the baseline
  is per-station, so values are NOT comparable between stations -- only a
  station against its own history/typical profile. It "is generally between
  0 and 1, but can be over 1 in some circumstances."

  TfL's own busyness bands for the value:
    < 0.4        quiet
    0.4 - 0.7    busy
    > 0.7        very busy

Data-availability note (found during probing):
  The Live endpoint can return {"dataAvailable": false, "percentageOfBaseline": 0}
  with null timestamps -- this means "no reading in the last 5 min", NOT that
  the station is empty. Tooting Bec/Broadway did this off-peak. We record such
  readings as blank (no value), never as 0, so they don't pollute the trends.

Designed to be run repeatedly (every ~5 min) by a GitHub Actions cron
schedule. As with the sibling dock-alerter, GitHub cron runs in UTC and has
no notion of the UK's GMT/BST switch, so the workflow casts a wide net in
UTC and this script decides -- using Europe/London via zoneinfo, which knows
about DST -- whether "now" actually falls inside a commute window. Runs
outside the windows exit immediately without writing anything.

Modes:
  live    (default) window-gated; append a row per station to history.csv.
  status  ungated; print current live busyness for every station and exit
          without writing (for "what's it like right now?" checks).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time as _time
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# --------------------------------------------------------------------------
# Config -- tweak these without touching the logic below.
# --------------------------------------------------------------------------

TFL_BASE_URL = "https://api.tfl.gov.uk/crowding"

# (naptan, display name) -- the commute corridor, in geographic order
# (southbound Colliers Wood -> northbound Clapham North). All Northern line.
# Every ID here was verified live against StopPoint + the crowding feed before
# committing (see the temporary probe workflow in the branch history).
STATIONS: list[tuple[str, str]] = [
    ("940GZZLUCSD", "Colliers Wood"),
    ("940GZZLUTBY", "Tooting Broadway"),
    ("940GZZLUTBC", "Tooting Bec"),
    ("940GZZLUBLM", "Balham"),
    ("940GZZLUCPS", "Clapham South"),
    ("940GZZLUCPC", "Clapham Common"),
    ("940GZZLUCPN", "Clapham North"),
]

# TfL's own busyness bands for percentageOfBaseline.
QUIET_BELOW = 0.4       # < 0.4        -> quiet
BUSY_BELOW = 0.7        # 0.4 - 0.7    -> busy; > 0.7 -> very busy

# Commute monitoring windows, in Europe/London local time. Inclusive of start,
# exclusive of end. Chosen to match a ~7:00-9:30 / 16:30-19:00 commute.
MORNING_START = time(7, 0)
MORNING_END = time(9, 30)
EVENING_START = time(16, 30)
EVENING_END = time(19, 0)

# Monday(0) .. Friday(4). All weekdays.
ACTIVE_WEEKDAYS = {0, 1, 2, 3, 4}

# An optional Unified API application key. Not required (the feed works
# keyless), but including one gives politeness/rate-limit headroom. Set via
# the TFL_APP_KEY env var / repo secret; empty string is treated as unset.
TFL_APP_KEY = os.environ.get("TFL_APP_KEY") or ""

HISTORY_FILE = Path(__file__).parent / "history.csv"
LONDON = ZoneInfo("Europe/London")
REQUEST_TIMEOUT_SECONDS = 15

CSV_HEADER = [
    "timestamp_utc",       # when we polled (ISO 8601, UTC)
    "timestamp_local",     # when we polled (Europe/London)
    "naptan",
    "station",
    "percentage_of_baseline",  # blank when dataAvailable is false
    "data_available",          # true / false
    "band",                    # quiet / busy / very busy / none
    "reading_time_utc",        # the feed's own timestamp for the value (may lag ~5-10 min)
]


# --------------------------------------------------------------------------
# Core logic
# --------------------------------------------------------------------------


@dataclass
class Reading:
    naptan: str
    station: str
    data_available: bool
    percentage: float | None
    reading_time_utc: str | None


def band_for(percentage: float | None) -> str:
    """Map a percentageOfBaseline value to TfL's quiet/busy/very busy label."""
    if percentage is None:
        return "none"
    if percentage < QUIET_BELOW:
        return "quiet"
    if percentage <= BUSY_BELOW:
        return "busy"
    return "very busy"


def _with_key(url: str) -> str:
    if not TFL_APP_KEY:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}app_key={TFL_APP_KEY}"


def _redact(text: str) -> str:
    """Strip a leaked app_key from a string before it is printed.

    requests exception messages embed the request URL, which carries
    ?app_key=... -- keep it out of logs (which are public in CI) and local
    terminals. GitHub also masks registered secrets, but don't rely on that.
    """
    if not TFL_APP_KEY:
        return text
    return text.replace(TFL_APP_KEY, "***")


def fetch_live(naptan: str) -> dict:
    """GET the live crowding JSON for one station, with a single retry."""
    url = _with_key(f"{TFL_BASE_URL}/{naptan}/Live")
    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt == 0:
                print(
                    f"WARNING: live request for {naptan} failed "
                    f"({exc.__class__.__name__}), retrying in 3 s...",
                    file=sys.stderr,
                )
                _time.sleep(3)
            else:
                raise


def read_station(naptan: str, station: str) -> Reading:
    """Fetch and normalise one station's live reading.

    A dataAvailable:false response (or an unexpected shape) yields a Reading
    with percentage=None so it is logged as a blank, never as a real 0.
    """
    try:
        data = fetch_live(naptan)
    except requests.exceptions.RequestException as exc:
        print(f"WARNING: giving up on {station} ({naptan}): {_redact(str(exc))}", file=sys.stderr)
        return Reading(naptan, station, False, None, None)

    available = bool(data.get("dataAvailable"))
    if not available:
        return Reading(naptan, station, False, None, data.get("timeUtc"))

    raw = data.get("percentageOfBaseline")
    if not isinstance(raw, (int, float)):
        # dataAvailable true but no usable number -- treat as no reading.
        print(
            f"WARNING: {station} dataAvailable=true but percentageOfBaseline "
            f"missing/invalid ({raw!r}); logging as blank.",
            file=sys.stderr,
        )
        return Reading(naptan, station, True, None, data.get("timeUtc"))

    return Reading(naptan, station, True, float(raw), data.get("timeUtc"))


def within_window(now_london: datetime) -> bool:
    """True if `now` (Europe/London) is a weekday inside a commute window."""
    if now_london.weekday() not in ACTIVE_WEEKDAYS:
        return False
    t = now_london.time()
    return (MORNING_START <= t < MORNING_END) or (EVENING_START <= t < EVENING_END)


def collect_readings() -> list[Reading]:
    return [read_station(naptan, name) for naptan, name in STATIONS]


def append_history(readings: list[Reading], polled_utc: datetime, polled_local: datetime) -> None:
    new_file = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(CSV_HEADER)
        for r in readings:
            writer.writerow([
                polled_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                polled_local.strftime("%Y-%m-%d %H:%M:%S"),
                r.naptan,
                r.station,
                "" if r.percentage is None else f"{r.percentage:.6f}",
                "true" if r.data_available else "false",
                band_for(r.percentage),
                r.reading_time_utc or "",
            ])


def print_status(readings: list[Reading]) -> None:
    print(f"{'Station':<18} {'Value':>8}  Band")
    print("-" * 40)
    for r in readings:
        if r.percentage is None:
            val = "n/a"
        else:
            val = f"{r.percentage * 100:.0f}%"
        print(f"{r.station:<18} {val:>8}  {band_for(r.percentage)}")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["live", "status"],
        default="live",
        help="live: window-gated, append to history.csv. "
             "status: print current busyness for all stations and exit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="In live mode, bypass the weekday/window gate and log now.",
    )
    args = parser.parse_args()

    now_utc = datetime.now(ZoneInfo("UTC"))
    now_local = now_utc.astimezone(LONDON)

    if args.mode == "status":
        print_status(collect_readings())
        return 0

    # live mode
    if not args.force and not within_window(now_local):
        print(
            f"Nothing to do at {now_local:%Y-%m-%d %H:%M %Z} "
            f"(outside commute window or weekend).",
        )
        return 0

    readings = collect_readings()
    append_history(readings, now_utc, now_local)

    available = sum(1 for r in readings if r.data_available)
    print(
        f"Logged {len(readings)} stations "
        f"({available} with live data) at {now_local:%Y-%m-%d %H:%M %Z}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
