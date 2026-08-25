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
import io
import json
import math
import os
import statistics
import sys
import time as _time  # stdlib time module; `time` in this file is datetime.time
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

# Route-to-the-gym on-demand check: bikes at the pickup station, and empty
# docks at the drop-off station (falling back to a second drop-off station
# if the first is full). Looked up by name at runtime, same as above.
# Confirmed against the live Santander Cycles app on 2026-08-09: real
# station names are "Bricklayers Arms, Borough", "Empire Square, The
# Borough", and "Swan Street, The Borough" (not "Swan Square" -- that
# station doesn't exist, an earlier guess corrected after the first real
# run couldn't find it).
GYM_ROUTE_BIKE_QUERY = "Bricklayers Arms, Borough"
GYM_ROUTE_DOCK_QUERY = "Empire Square"
GYM_ROUTE_DOCK_BACKUP_QUERY = "Swan Street"

# "Pump time" only if there's comfortable margin on both ends -- at least
# this many bikes AND at least this many docks at whichever drop-off
# station is actually usable (Empire Square, or Swan Street if diverting).
GYM_ROUTE_GOOD_BIKES_THRESHOLD = 2
GYM_ROUTE_GOOD_DOCKS_THRESHOLD = 3

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
# Note both one-off snapshots sit INSIDE the 08:00-08:45 check window: a run
# landing in a snapshot's 5-minute slot does the snapshot instead of a
# threshold check that slot (the snapshot reports the count anyway).
MORNING_SUMMARY_TIME = time(8, 10)
MORNING_BIKES_TIME = time(8, 25)  # second one-off dock count snapshot
MORNING_CHECK_START = time(8, 0)
MORNING_CHECK_END = time(8, 45)

# Evening monitoring window, in Europe/London local time.
EVENING_SUMMARY_TIME = time(17, 15)
EVENING_SECOND_SUMMARY_TIME = time(17, 40)  # one-off mid-evening bike count snapshot
EVENING_CHECK_START = time(17, 30)
EVENING_CHECK_END = time(18, 0)

# Run-day check: only Monday (0) through Thursday (3), for both windows.
ACTIVE_WEEKDAYS = {0, 1, 2, 3}

# Weather logging. Open-Meteo is free and needs no API key. Recorded
# alongside every reading so that "how does rain affect availability?" can
# be answered later from real data -- there is no weather in the history
# before 2026-08, so any correlation work has to wait for that backlog to
# build up. Nothing reads these columns yet beyond the rain caveat in the
# summary notifications.
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
STATION_LAT = 51.5045   # Tooley Street, Bermondsey
STATION_LON = -0.0805
# Deliberately much shorter than REQUEST_TIMEOUT_SECONDS: this call sits
# in front of the threshold checks (history is logged before the alert
# decision), so a hanging weather API would delay a real LOW alert by
# however long we're willing to wait. Weather is decorative; four seconds
# is the most a "nice to have" gets to cost an actual alert.
WEATHER_TIMEOUT_SECONDS = 4
# Precipitation (mm in the last hour) at or above which we call it "wet".
WET_PRECIP_MM = 0.2

# Forecasting. Mirrors the dashboard's Forecast view exactly (same
# shrinkage blend, same minimum-days floor) so the phone notification and
# the dashboard can never disagree about the same window.
#
# For each 5-minute slot we blend that weekday's own mean with the mean
# across all recorded weekdays, weighted by how much same-weekday data
# exists: (wd_mean * n_wd + overall_mean * K) / (n_wd + K). With little
# same-weekday history the overall average dominates; as Tuesdays
# accumulate, Tuesdays take over.
FORECAST_SHRINK_K = 2
# Don't forecast at all below this many distinct recorded days -- a shaky
# guess erodes trust in the notification faster than no guess does.
FORECAST_MIN_DAYS = 3
# Ignore individual 5-minute slots with fewer readings than this. Real
# example (2026-08): the 18:00 slot held exactly two readings, both from
# one unusual evening, and dragged the predicted low from ~5 down to ~2 --
# a two-sample fluke presented as a forecast. Slots this thin are noise,
# not signal, so they don't get to set the headline or appear on the chart.
FORECAST_MIN_SLOT_READINGS = 4
# ...and don't forecast from fewer than this many qualifying slots. Matches
# the dashboard's own `fc.slots.length < 3` floor.
FORECAST_MIN_SLOTS = 3

# How many past days of similar starting availability to project from.
#
# A blanket historical average is not a forecast once you know what's
# actually on the board: with 3 bikes showing, "usually dips to ~5" is a
# contradiction, not a prediction (seen for real 2026-08-24). How far it
# falls depends almost entirely on where it starts -- evenings starting at
# 16-21 bikes have dropped by up to 16, while every recorded evening that
# started at 3 ended at 2. So instead of averaging all days, compare
# today's reading against the days that looked most like it at this time
# and see where those ended up.
FORECAST_NEIGHBOUR_DAYS = 6
# Flag today as unusual only when it's this far from the typical level for
# the time of day -- a caveat that fires every day stops being read.
FORECAST_UNUSUAL_MARGIN = 3
# A reading this far (minutes) from the anchor time isn't "the same moment".
FORECAST_ANCHOR_TOLERANCE = 10

STATE_FILE = Path(__file__).parent / "state.json"
MUTE_FILE = Path(__file__).parent / "mute.flag"
FRIDAY_FLAG_FILE = Path(__file__).parent / "friday.flag"
HISTORY_FILE = Path(__file__).parent / "history.csv"
PREDICTIONS_FILE = Path(__file__).parent / "predictions.csv"
LONDON = ZoneInfo("Europe/London")
REQUEST_TIMEOUT_SECONDS = 15

HISTORY_COLUMNS = ["timestamp_utc", "mode", "metric", "value", "station", "temp_c", "precip_mm"]
PREDICTION_COLUMNS = [
    "predicted_at_utc", "target_date", "window", "metric",
    # What was on the board when the projection was made, and at what time.
    # `anchor_at` matters for scoring: the projection is about the low from
    # that moment onward, so the actual must be measured over the same span.
    "anchor_at", "anchor_value",
    "predicted_low", "predicted_at", "basis_days", "basis_weekday_days",
    "actual_low", "actual_at", "error",
]


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


def is_friday_enabled(now_london: datetime) -> bool:
    """True if today is Friday and friday.flag contains today's date.

    The flag is set (via an iOS Shortcut or manually) with the target Friday's
    date, so it can be created on Thursday afternoon for the next morning and
    auto-resets -- tomorrow's date won't match, so no cleanup needed.
    """
    if now_london.weekday() != 4:  # 4 = Friday
        return False
    if not FRIDAY_FLAG_FILE.exists():
        return False
    try:
        flagged_date = FRIDAY_FLAG_FILE.read_text().strip()
    except OSError:
        return False
    return flagged_date == now_london.date().isoformat()


def determine_mode(now_london: datetime, force_mode: str | None) -> str | None:
    """Decide what to do right now: one of 'summary', 'check',
    'evening_summary', 'evening_check', or None (do nothing)."""
    if force_mode and force_mode != "auto":
        return force_mode

    if now_london.weekday() not in ACTIVE_WEEKDAYS:
        if not is_friday_enabled(now_london):
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
    for attempt in range(2):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt == 0:
                print(f"WARNING: TfL API request failed ({exc.__class__.__name__}), retrying in 3 s...", file=sys.stderr)
                _time.sleep(3)
            else:
                raise


def _search_bikepoint(name_query: str) -> dict | None:
    """Look up a BikePoint by name.

    The TfL Search endpoint's own embedded additionalProperties have been
    observed (2026-08-09) coming back completely empty for a station
    confirmed live via the Santander Cycles app and via TfL's direct
    by-ID endpoint at the same moment -- a retry through Search alone
    didn't help, so the Search result isn't a reliable source of live
    data. Search here only resolves the name to a station ID; the actual
    live counts always come from the same direct by-ID fetch already
    proven reliable for the main Tooley Street station (which has its
    own timeout/connection retry built in).
    """
    url = f"{TFL_BASE_URL}/Search"
    response = requests.get(url, params={"query": name_query}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    match = results[0]
    station_id = match.get("id")
    if not station_id:
        return match
    try:
        return _fetch_bikepoint(station_id)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as exc:
        print(f"WARNING: direct fetch of '{station_id}' failed ({exc.__class__.__name__}), using Search result instead.", file=sys.stderr)
        return match


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


def _search_bikepoint_or_raise(name_query: str) -> dict:
    data = _search_bikepoint(name_query)
    if data is None:
        raise RuntimeError(f"No BikePoint found matching '{name_query}'.")
    return data


def fetch_gym_route() -> tuple[int, str, int, str, str | None, int | None]:
    """Return (bikes, bike_station_name, docks, dock_station_name,
    backup_station_name_or_None, backup_docks_or_None) for the gym route:
    standard bikes available at the pickup station, and empty docks at the
    drop-off station -- only looking up the backup drop-off station if the
    first one has none, to avoid an unnecessary extra API call.
    """
    bike_data = _search_bikepoint_or_raise(GYM_ROUTE_BIKE_QUERY)
    bike_props = _props(bike_data)
    if "NbBikes" not in bike_props:
        raise RuntimeError(
            f"NbBikes not found for '{GYM_ROUTE_BIKE_QUERY}' -- raw: {json.dumps(bike_data)[:500]}"
        )
    bikes = _count_bikes(bike_props)
    bike_station_name = bike_data.get("commonName", GYM_ROUTE_BIKE_QUERY)

    dock_data = _search_bikepoint_or_raise(GYM_ROUTE_DOCK_QUERY)
    dock_props = _props(dock_data)
    if "NbEmptyDocks" not in dock_props:
        raise RuntimeError(
            f"NbEmptyDocks not found for '{GYM_ROUTE_DOCK_QUERY}' -- raw: {json.dumps(dock_data)[:500]}"
        )
    docks = int(dock_props["NbEmptyDocks"])
    dock_station_name = dock_data.get("commonName", GYM_ROUTE_DOCK_QUERY)

    backup_name = None
    backup_docks = None
    if docks == 0:
        try:
            backup_data = _search_bikepoint(GYM_ROUTE_DOCK_BACKUP_QUERY)
            if backup_data is None:
                print(f"WARNING: no BikePoint found matching '{GYM_ROUTE_DOCK_BACKUP_QUERY}'.", file=sys.stderr)
            else:
                backup_props = _props(backup_data)
                if "NbEmptyDocks" in backup_props:
                    backup_docks = int(backup_props["NbEmptyDocks"])
                    backup_name = backup_data.get("commonName", GYM_ROUTE_DOCK_BACKUP_QUERY)
        except requests.RequestException as exc:
            print(f"WARNING: backup dock station lookup failed: {exc}", file=sys.stderr)

    return bikes, bike_station_name, docks, dock_station_name, backup_name, backup_docks


_weather_cache: tuple[float | None, float | None] | None = None


def get_weather() -> tuple[float | None, float | None]:
    """Best-effort (temperature_c, precipitation_mm) for the station.

    Memoised so a single run makes at most one weather call even when it
    logs several rows (e.g. `status` logs both docks and bikes). Returns
    (None, None) on any failure -- weather is a nice-to-have annotation on
    the history log and must never *prevent* a reading or an alert. It can
    still delay one by up to WEATHER_TIMEOUT_SECONDS, since history is
    logged before the threshold decision; that timeout is kept short
    precisely to bound how much an alert can be held up.
    """
    global _weather_cache
    if _weather_cache is not None:
        return _weather_cache
    try:
        response = requests.get(
            WEATHER_URL,
            params={
                "latitude": STATION_LAT,
                "longitude": STATION_LON,
                "current": "temperature_2m,precipitation",
            },
            timeout=WEATHER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        current = response.json().get("current", {})
        temp = current.get("temperature_2m")
        precip = current.get("precipitation")
        _weather_cache = (
            float(temp) if isinstance(temp, (int, float)) else None,
            float(precip) if isinstance(precip, (int, float)) else None,
        )
    except Exception as exc:
        # Deliberately broad: weather is a decorative annotation on the
        # log and a caveat line. Nothing about it is worth failing a real
        # dock reading or alert over, whatever goes wrong.
        print(f"WARNING: weather lookup failed ({exc.__class__.__name__}) -- logging without it.", file=sys.stderr)
        _weather_cache = (None, None)
    return _weather_cache


def _ensure_history_header() -> None:
    """Upgrade a legacy 5-column history header in place, once.

    Rows logged before weather tracking have five fields; new rows have
    seven. Rewriting just the header line (leaving the short historical
    rows alone) keeps both old and new rows readable by `csv.DictReader`
    and by the dashboard's parser, without rewriting hundreds of rows of
    real data -- which would also risk conflicting with an in-flight run.
    """
    if not HISTORY_FILE.exists():
        return
    try:
        with HISTORY_FILE.open(newline="") as f:
            first = f.readline()
            if not first.startswith("timestamp_utc,") or first.rstrip("\r\n").count(",") >= len(HISTORY_COLUMNS) - 1:
                return  # already migrated, or not a header we recognise
            rest = f.read()
        _atomic_write(HISTORY_FILE, ",".join(HISTORY_COLUMNS) + "\n" + rest)
        print("Upgraded history.csv header to include weather columns.")
    except OSError as exc:
        print(f"WARNING: could not upgrade history.csv header ({exc}).", file=sys.stderr)


def log_history(mode: str, metric: str, value: int, station_name: str) -> None:
    """Append one row to history.csv -- a running log of every reading, for
    spotting patterns later (the dashboard, forecasts, weather correlation).
    Writes a header row the first time the file is created.

    Rows written before 2026-08 have only the first five columns; every
    reader here and in the dashboard tolerates those short rows rather than
    rewriting the historical file.
    """
    temp_c, precip_mm = get_weather()
    _ensure_history_header()
    is_new_file = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow(HISTORY_COLUMNS)
        writer.writerow([
            datetime.now(ZoneInfo("UTC")).isoformat(), mode, metric, value, station_name,
            "" if temp_c is None else temp_c,
            "" if precip_mm is None else precip_mm,
        ])


# --------------------------------------------------------------------------
# Forecasting + prediction quality tracking
# --------------------------------------------------------------------------

def _mins(t: time) -> int:
    return t.hour * 60 + t.minute


def _atomic_write(path: Path, text: str) -> None:
    """Write a file by rename, so it's never left half-written.

    Both files rewritten wholesale here (history.csv's header upgrade and
    predictions.csv) are the record the forecasts are built from, and they
    get committed back to the repo. A truncated write that survived would
    silently corrupt that record, so write to a sibling temp file and
    rename over the original -- rename is atomic on POSIX, so readers see
    either the whole old file or the whole new one.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _round_half_up(x: float) -> int:
    """Round .5 away from zero, like JavaScript's Math.round.

    Python's built-in round() rounds halves to even (round(6.5) == 6),
    so using it here would make this forecaster disagree with the
    dashboard's by one on exact halves.
    """
    return math.floor(x + 0.5)


# The two monitored windows, keyed by the name used in predictions.csv.
# (start, end, metric) -- the metric each window actually cares about.
#
# These MUST match the dashboard's MORN/EVE constants exactly, or the phone
# notification and the Forecast tab will quote different numbers for the
# same window. The evening span therefore starts at the 17:15 summary (the
# full monitored evening), not at the 17:30 threshold-check start.
WINDOWS = {
    "morning": (MORNING_CHECK_START, MORNING_CHECK_END, "empty_docks"),
    "evening": (EVENING_SUMMARY_TIME, EVENING_CHECK_END, "available_bikes"),
}


def load_history() -> list[dict]:
    """Parse history.csv into rows with London-local date/time attached.

    Tolerates short rows (pre-2026-08 rows have no weather columns) and
    skips anything unparseable rather than raising -- every caller treats
    history as best-effort context, never as something worth failing a
    real alert over.
    """
    if not HISTORY_FILE.exists():
        return []
    rows = []
    try:
        with HISTORY_FILE.open(newline="") as f:
            for rec in csv.DictReader(f):
                raw_ts = (rec.get("timestamp_utc") or "").strip()
                raw_value = (rec.get("value") or "").strip()
                if not raw_ts or not raw_value:
                    continue
                try:
                    ts = datetime.fromisoformat(raw_ts)
                    value = int(raw_value)
                except ValueError:
                    continue
                local = ts.astimezone(LONDON)
                rows.append({
                    "ts": ts,
                    "metric": (rec.get("metric") or "").strip(),
                    "value": value,
                    "date": local.date(),
                    "mins": local.hour * 60 + local.minute,
                    "weekday": local.weekday(),
                })
    except OSError as exc:
        print(f"WARNING: could not read history.csv ({exc}) -- skipping forecast.", file=sys.stderr)
        return []
    return rows


def forecast_window_low(window: str, target_weekday: int, history: list[dict] | None = None) -> dict | None:
    """Predict the LOW point of a monitored window, and when it happens.

    The low point (rather than the value at some fixed end-of-window time)
    is what actually matters: it's the tightest moment you'd hit if you
    left in the next few minutes. Returns None when there isn't enough
    history to say anything worth trusting.
    """
    start_t, end_t, metric = WINDOWS[window]
    start, end = _mins(start_t), _mins(end_t)
    rows = load_history() if history is None else history
    rows = [r for r in rows if r["metric"] == metric and start - 4 <= r["mins"] <= end + 4]
    if not rows:
        return None

    days = len({r["date"] for r in rows})
    if days < FORECAST_MIN_DAYS:
        return None
    wd_rows = [r for r in rows if r["weekday"] == target_weekday]
    wd_days = len({r["date"] for r in wd_rows})

    # Snap to the nearest 5-minute slot, then keep only slots inside the
    # window. (Don't clamp stragglers inward -- a reading at 18:04 belongs
    # to no slot, not to the 18:00 one. The dashboard does the same.)
    def slot_of(r):
        return round(r["mins"] / 5) * 5

    overall: dict[int, list[int]] = {}
    per_wd: dict[int, list[int]] = {}
    for r in rows:
        slot = slot_of(r)
        if start <= slot <= end:
            overall.setdefault(slot, []).append(r["value"])
    for r in wd_rows:
        slot = slot_of(r)
        if start <= slot <= end:
            per_wd.setdefault(slot, []).append(r["value"])

    best_slot, best_value = None, None
    eligible = 0
    for slot, values in sorted(overall.items()):
        if len(values) < FORECAST_MIN_SLOT_READINGS:
            continue  # too thin to trust -- see FORECAST_MIN_SLOT_READINGS
        eligible += 1
        # Round each slot mean to 1dp BEFORE blending, and the blend after,
        # exactly as the dashboard does (its slotStats rounds avg to 1dp).
        # Blending exact means here instead would let the two disagree by
        # one at a .x5 boundary.
        overall_mean = _round_half_up(sum(values) / len(values) * 10) / 10
        wd_values = per_wd.get(slot, [])
        if wd_values:
            wd_mean = _round_half_up(sum(wd_values) / len(wd_values) * 10) / 10
            blended = (wd_mean * len(wd_values) + overall_mean * FORECAST_SHRINK_K) / (len(wd_values) + FORECAST_SHRINK_K)
        else:
            blended = overall_mean
        blended = _round_half_up(blended * 10) / 10
        if best_value is None or blended < best_value:
            best_slot, best_value = slot, blended

    # The dashboard also refuses to forecast from fewer than three
    # qualifying slots; without this the phone would forecast on days the
    # Forecast tab still says "Still building up".
    if best_slot is None or eligible < FORECAST_MIN_SLOTS:
        return None
    return {
        "low": _round_half_up(best_value),
        "at": best_slot,
        "days": days,
        "weekday_days": wd_days,
        "metric": metric,
    }


def _window_days(window: str, history: list[dict], anchor: int, exclude_date=None) -> list[dict]:
    """Per-day summary of a window, seen from `anchor` minutes onward.

    Returns one entry per past day that has both a reading at roughly the
    anchor time and at least one reading at/after it:
    {date, weekday, anchor_value, low, low_at}.
    """
    _, _, metric = WINDOWS[window]
    start, end = _mins(WINDOWS[window][0]), _mins(WINDOWS[window][1])
    by_date: dict[object, list[dict]] = {}
    for r in history:
        if r["metric"] != metric or not (start - 4 <= r["mins"] <= end + 4):
            continue
        if exclude_date is not None and r["date"] == exclude_date:
            continue
        by_date.setdefault(r["date"], []).append(r)

    days = []
    for date, rows in by_date.items():
        near = min(rows, key=lambda r: abs(r["mins"] - anchor))
        if abs(near["mins"] - anchor) > FORECAST_ANCHOR_TOLERANCE:
            continue  # nothing recorded near this time of day
        after = [r for r in rows if r["mins"] >= near["mins"]]
        if not after:
            continue
        low = min(after, key=lambda r: r["value"])
        days.append({
            "date": date,
            "weekday": rows[0]["weekday"],
            "anchor_value": near["value"],
            "low": low["value"],
            "low_at": round(low["mins"] / 5) * 5,
        })
    return days


def project_window_low(window: str, current_value: int, now_london: datetime,
                       history: list[dict] | None = None) -> dict | None:
    """Project the low from NOW onward, anchored on today's actual reading.

    Two properties make this trustworthy where a plain historical average
    wasn't:

    1. It conditions on reality. Days are ranked by how close their reading
       at this time of day was to today's, and the projection is the median
       of what those most-similar days went on to do.
    2. The projection can never exceed what's on the board right now. The
       current reading is itself part of "from now onward", so the low over
       that period is at most the current value -- as a matter of
       arithmetic, not of estimation. Clamping to it makes the "3 now, dips
       to 5" contradiction structurally impossible rather than merely
       unlikely.
    """
    if window not in WINDOWS:
        return None
    start, end = _mins(WINDOWS[window][0]), _mins(WINDOWS[window][1])
    rows = load_history() if history is None else history
    now_mins = now_london.hour * 60 + now_london.minute
    anchor = int(min(max(round(now_mins / 5) * 5, start), end))

    days = _window_days(window, rows, anchor, exclude_date=now_london.date())
    if len(days) < FORECAST_MIN_DAYS:
        return None

    typical_now = statistics.median(d["anchor_value"] for d in days)
    # The most similar days, by how busy the station was at this same time.
    neighbours = sorted(days, key=lambda d: (abs(d["anchor_value"] - current_value), d["date"]))
    neighbours = neighbours[:FORECAST_NEIGHBOUR_DAYS]

    projected = statistics.median(d["low"] for d in neighbours)
    projected = int(max(0, min(current_value, _round_half_up(projected))))

    # When does the dip land? Only meaningful among neighbours that
    # actually fell; if none did, there's no dip to time.
    fell = [d for d in neighbours if d["low"] < d["anchor_value"]]
    low_at = int(statistics.median(d["low_at"] for d in fell)) if fell else None

    return {
        "metric": WINDOWS[window][2],
        "projected": projected,
        "at": low_at,
        "anchor_at": anchor,
        "anchor_value": current_value,
        "typical_now": int(_round_half_up(typical_now)),
        "days": len(days),
        "neighbours": len(neighbours),
    }


def _read_predictions() -> list[dict]:
    if not PREDICTIONS_FILE.exists():
        return []
    try:
        with PREDICTIONS_FILE.open(newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except OSError as exc:
        print(f"WARNING: could not read predictions.csv ({exc}).", file=sys.stderr)
        return []


def _write_predictions(rows: list[dict]) -> None:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=PREDICTION_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in PREDICTION_COLUMNS})
    _atomic_write(PREDICTIONS_FILE, buf.getvalue())


def record_prediction(window: str, target_date, forecast: dict) -> None:
    """Log a forecast so its accuracy can be scored once the real window
    has played out. One row per window per day -- re-recording the same
    window/date overwrites rather than duplicating, so a re-run or a late
    duplicate trigger can't skew the accuracy stats.
    """
    # Pure bookkeeping, and it runs *after* the notification has already
    # gone out -- so it must never raise. An exception here would skip the
    # state save that follows in the summary handlers, which could cause a
    # duplicate alert on the next check.
    try:
        rows = _read_predictions()
        key = (str(target_date), window)
        rows = [r for r in rows if (r.get("target_date"), r.get("window")) != key]
        rows.append({
            "predicted_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
            "target_date": str(target_date),
            "window": window,
            "metric": forecast["metric"],
            "anchor_at": fmt_slot(forecast["anchor_at"]),
            "anchor_value": forecast["anchor_value"],
            "predicted_low": forecast["projected"],
            "predicted_at": fmt_slot(forecast["at"]) if forecast["at"] is not None else "",
            "basis_days": forecast["days"],
            "basis_weekday_days": forecast["neighbours"],
            "actual_low": "", "actual_at": "", "error": "",
        })
        rows.sort(key=lambda r: (r.get("target_date", ""), r.get("window", "")))
        _write_predictions(rows)
    except Exception as exc:
        print(f"WARNING: could not record prediction ({exc.__class__.__name__}: {exc}).", file=sys.stderr)


def reconcile_predictions(now_london: datetime, history: list[dict] | None = None) -> int:
    """Fill in what actually happened for any prediction whose window has
    now finished. Self-healing: runs on every invocation and picks up any
    window it missed, so there's nothing extra to schedule.
    """
    rows = _read_predictions()
    pending = [r for r in rows if not (r.get("actual_low") or "").strip()]
    if not pending:
        return 0
    hist = load_history() if history is None else history
    filled = 0
    for row in pending:
        window = row.get("window")
        if window not in WINDOWS:
            continue
        try:
            target = datetime.strptime(row["target_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        start_t, end_t, metric = WINDOWS[window]
        # Only score a window that has actually finished.
        if target > now_london.date():
            continue
        if target == now_london.date() and now_london.time() <= end_t:
            continue
        # Score over the same span the projection covered: from the moment
        # it was made, onward. Older rows predate the anchor column and are
        # scored over the whole window, as they were made.
        span_start = _mins(start_t) - 4
        anchor_txt = (row.get("anchor_at") or "").strip()
        if anchor_txt:
            try:
                hh, mm = anchor_txt.split(":")
                span_start = int(hh) * 60 + int(mm)
            except ValueError:
                pass
        actuals = [
            r for r in hist
            if r["date"] == target and r["metric"] == metric
            and span_start <= r["mins"] <= _mins(end_t) + 4
        ]
        if not actuals:
            continue  # no readings that day (missed run, muted, holiday)
        low_row = min(actuals, key=lambda r: r["value"])
        row["actual_low"] = low_row["value"]
        row["actual_at"] = fmt_slot(round(low_row["mins"] / 5) * 5)
        try:
            row["error"] = int(low_row["value"]) - int(row["predicted_low"])
        except (TypeError, ValueError):
            row["error"] = ""
        filled += 1
    if filled:
        _write_predictions(rows)
    return filled


def accuracy_summary(rows: list[dict] | None = None) -> dict:
    """Aggregate how the forecasts have actually performed."""
    rows = _read_predictions() if rows is None else rows
    scored = []
    for r in rows:
        try:
            scored.append({
                "window": r.get("window", ""),
                "error": int(r["error"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not scored:
        return {"n": 0}

    def stats(subset):
        if not subset:
            return None
        errors = [s["error"] for s in subset]
        return {
            "n": len(errors),
            # Mean ABSOLUTE error: typical distance from the truth.
            "mae": round(sum(abs(e) for e in errors) / len(errors), 1),
            # Mean SIGNED error: is it consistently optimistic (+) or
            # pessimistic (-)? A big bias is the fixable kind of wrong.
            "bias": round(sum(errors) / len(errors), 1),
            "within_2": round(100 * sum(1 for e in errors if abs(e) <= 2) / len(errors)),
        }

    return {
        "n": len(scored),
        "overall": stats(scored),
        "morning": stats([s for s in scored if s["window"] == "morning"]),
        "evening": stats([s for s in scored if s["window"] == "evening"]),
    }


def fmt_slot(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def forecast_sentence(window: str, current_value: int, now_london: datetime,
                      history: list[dict] | None = None) -> tuple[str, dict | None]:
    """The extra text appended to a summary notification, plus the
    projection it came from (so the caller can log it for scoring).

    Reads as one continuous thought with the count that precedes it:
    what's there now, whether that's normal for the time, and where
    comparable days ended up. Returns ("", None) when there isn't enough
    history -- the notification then reads exactly as it did before this
    feature existed.
    """
    try:
        forecast = project_window_low(window, current_value, now_london, history)
    except Exception as exc:  # never let a forecast bug kill a real alert
        print(f"WARNING: forecast failed ({exc.__class__.__name__}: {exc}).", file=sys.stderr)
        return "", None
    if not forecast:
        return "", None

    unit = "docks" if forecast["metric"] == "empty_docks" else "bikes"
    day_word = "mornings" if window == "morning" else "evenings"
    parts = []

    # Context first: is today normal for this time of day?
    if abs(current_value - forecast["typical_now"]) >= FORECAST_UNUSUAL_MARGIN:
        low_or_high = "low" if current_value < forecast["typical_now"] else "high"
        parts.append(f" Unusually {low_or_high} (typically ~{forecast['typical_now']}).")

    projected, at = forecast["projected"], forecast["at"]
    if projected >= current_value or at is None:
        parts.append(f" Similar {day_word} held steady from here.")
    elif projected == 0:
        parts.append(f" Similar {day_word} ran out by {fmt_slot(at)}.")
    else:
        parts.append(f" Similar {day_word} dropped to ~{projected} by {fmt_slot(at)}.")

    return "".join(parts), forecast


def weather_caveat() -> str:
    """Mention rain only when it's actually raining -- a caveat that fires
    every day stops being read. Correlating rain with availability needs a
    backlog of wet days we don't have yet; this is just the honest
    'conditions today' note in the meantime.
    """
    _, precip = get_weather()
    if precip is not None and precip >= WET_PRECIP_MM:
        return " Raining -- availability may run lower than usual."
    return ""


def send_notification(title: str, message: str, priority: str = "default", tags: str = "bike") -> None:
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }
    response = requests.post(NTFY_URL, data=message.encode("utf-8"), headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    # Don't echo the URL or response body: this repo is public, so Actions
    # logs are public too, and both would reveal the ntfy topic string.
    print(f"ntfy POST -> {response.status_code}")
    response.raise_for_status()


def run(mode: str, dry_run: bool) -> None:
    state = State.load(STATE_FILE)
    now_utc = datetime.now(ZoneInfo("UTC"))
    now_london = now_utc.astimezone(LONDON)

    if mode == "summary":
        empty_docks, station_name = fetch_empty_docks()
        print(f"[{mode}] {station_name}: {empty_docks} empty docks")
        if not dry_run:
            log_history(mode, "empty_docks", empty_docks, station_name)

        title = "Tooley Street docks - morning check"
        forecast_text, forecast = forecast_sentence("morning", empty_docks, now_london)
        message = f"{empty_docks} empty docks now." + forecast_text + weather_caveat()
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,sunny")
            if forecast:
                record_prediction("morning", now_london.date(), forecast)

        # Clear stale morning alert state (e.g. yesterday's), leaving the
        # evening state untouched. The summary now runs INSIDE the check
        # window (08:10), so only reset if the last alert is old -- wiping a
        # minutes-old alert would cancel its cooldown and cause a duplicate
        # alert on the next check.
        stale = True
        if state.last_alert_time:
            last_alert = datetime.fromisoformat(state.last_alert_time)
            stale = (now_utc - last_alert) > timedelta(hours=2)
        if stale:
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
        empty_docks, station_name = fetch_empty_docks()
        print(f"[{mode}] {station_name}: {empty_docks} empty docks")
        if not dry_run:
            log_history(mode, "empty_docks", empty_docks, station_name)

        title = "Tooley Street docks - 08:25 check"
        message = f"{empty_docks} empty docks available right now."
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
        forecast_text, forecast = forecast_sentence("evening", bikes, now_london)
        message = f"{bikes} {bike_label} now." + forecast_text + weather_caveat()
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,sunny")
            if forecast:
                record_prediction("evening", now_london.date(), forecast)

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

    if mode == "gym_route":
        bikes, bike_station, docks, dock_station, backup_station, backup_docks = fetch_gym_route()
        bike_label = "standard bikes" if EXCLUDE_EBIKES else "bikes"
        print(
            f"[{mode}] {bike_station}: {bikes} {bike_label} | {dock_station}: {docks} empty docks"
            + (f" | backup {backup_station}: {backup_docks} empty docks" if backup_station else "")
        )
        # Not logged to history.csv -- that file/the dashboard are scoped to
        # the Tooley Street commute readings; mixing in a different route's
        # numbers would skew its "typical day" averages.

        # Which drop-off is actually usable: Empire Square if it has space,
        # otherwise Swan Street if that backup lookup found space instead.
        diverted = docks == 0 and backup_station is not None and backup_docks
        if docks > 0:
            usable_station, usable_docks = dock_station, docks
        elif diverted:
            usable_station, usable_docks = backup_station, backup_docks
        else:
            usable_station, usable_docks = dock_station, docks  # both full (or no backup data)

        good = bikes >= GYM_ROUTE_GOOD_BIKES_THRESHOLD and usable_docks >= GYM_ROUTE_GOOD_DOCKS_THRESHOLD
        headline = "Pump time" if good else "Low availability"

        bike_word = bike_label[:-1] if bikes == 1 else bike_label  # "1 standard bike" vs "6 standard bikes"
        parts = [f"{bikes} {bike_word} at {bike_station}."]
        if diverted:
            parts.append(
                f"{dock_station} is full -- head to {backup_station} instead "
                f"({backup_docks} empty dock{'s' if backup_docks != 1 else ''})."
            )
        elif docks == 0:
            parts.append(
                dock_station + " is full"
                + (f", and {backup_station} has none either." if backup_station else ", and no backup reading available.")
            )
        else:
            parts.append(f"{dock_station}: {docks} empty dock{'s' if docks != 1 else ''}.")

        title = "Route to the gym - status"
        message = headline + "\n" + " ".join(parts)

        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message.replace(chr(10), ' | ')}")
        else:
            tags = "bike,muscle" if good else "bike,warning"
            send_notification(title, message, priority="default", tags=tags)
        return

    if mode == "accuracy":
        # How well has the forecast actually been doing? Reconcile first so
        # the numbers include every window that has finished by now --
        # except under --dry-run, which must never write to disk (main()
        # skips its own reconcile pass for the same reason, so a dry run
        # simply reports on whatever has already been scored).
        if not dry_run:
            reconcile_predictions(now_london)
        summary = accuracy_summary()
        if not summary.get("n"):
            print("[accuracy] No scored predictions yet.")
            message = "No forecasts have been scored yet -- check back after a few more days."
        else:
            print(f"[accuracy] {summary}")
            lines = []
            for label, key in (("Mornings", "morning"), ("Evenings", "evening")):
                s = summary.get(key)
                if s:
                    direction = "over" if s["bias"] < 0 else "under"
                    lines.append(
                        f"{label}: typically {s['mae']} out ({s['within_2']}% within 2), "
                        f"{direction}-predicting by {abs(s['bias'])} on average, over {s['n']} days."
                    )
            message = " ".join(lines) or "Not enough scored forecasts yet."

        title = "Dock alerter - forecast accuracy"
        if dry_run:
            print(f"DRY RUN -- would send: {title} / {message}")
        else:
            send_notification(title, message, priority="default", tags="bike,bar_chart")
        return

    raise ValueError(f"Unknown mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force-mode",
        choices=["auto", "summary", "morning_bikes", "check", "evening_summary", "evening_second_summary", "evening_check", "status", "gym_route", "accuracy"],
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

    # Score any forecast whose window has now finished. Done before the
    # early returns below so it still self-heals on days that are muted or
    # outside the windows -- those runs already do nothing else, and this
    # is how a window missed at the time still gets scored later.
    if not args.dry_run:
        try:
            filled = reconcile_predictions(now_london)
            if filled:
                print(f"Scored {filled} finished forecast window(s).")
        except Exception as exc:  # never block a real check on bookkeeping
            print(f"WARNING: reconciling predictions failed ({exc.__class__.__name__}: {exc}).", file=sys.stderr)

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
