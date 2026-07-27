"""Fetch a snapshot of the official FPL API and write it into data/.

This is the bottom layer of the FPL agent: everything else (the expected
points model, the optimiser, the emails) reads what this produces.

It exists as a standalone step for a practical reason -- the Claude
development sandbox cannot reach fantasy.premierleague.com (the network
proxy blocks it), but GitHub Actions runners can. Running this on Actions
and committing the result back is what gives development sessions real
prices, ownership and underlying stats to work with, instead of guesses.

Outputs (all committed, all small enough to read in a session):
  data/players.csv    one row per player: price, ownership, xG/xA, minutes,
                      DefCon, injury news -- the model's input table
  data/teams.csv      team strength ratings, used for clean-sheet modelling
  data/fixtures.csv   upcoming fixtures with difficulty, for the horizon view
  data/events.csv     gameweek deadlines + data_checked, drives all scheduling
  data/meta.json      when the snapshot was taken, next deadline, API totals

Usage:
  python snapshot.py              fetch live and write data/
  python snapshot.py --summary    also print a readable digest to the log
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

API_ROOT = "https://fantasy.premierleague.com/api"
BOOTSTRAP_URL = f"{API_ROOT}/bootstrap-static/"
FIXTURES_URL = f"{API_ROOT}/fixtures/"

# The API rejects requests without a browser-ish User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FPLAgent/1.0)"}
TIMEOUT_SECONDS = 60

# Player fields worth keeping. bootstrap-static returns ~100 columns per
# player, most of them presentational (photo filenames, kit codes). These are
# the ones the model actually consumes.
PLAYER_FIELDS = [
    "id", "web_name", "first_name", "second_name", "team", "element_type",
    "now_cost", "cost_change_start", "cost_change_event",
    "total_points", "event_points", "points_per_game", "form",
    "minutes", "starts", "goals_scored", "assists", "clean_sheets",
    "goals_conceded", "yellow_cards", "red_cards", "saves", "bonus", "bps",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded",
    "defensive_contribution",
    "selected_by_percent", "transfers_in_event", "transfers_out_event",
    # Price-change inputs. transfers_in/out are cumulative (the event ones
    # reset each gameweek and read zero pre-season); price_change_percent is
    # FPL's own 2026/27 price-prediction figure, so it is worth capturing
    # rather than reverse-engineering what the official game already publishes.
    "transfers_in", "transfers_out", "price_change_percent",
    "cost_change_event_fall", "cost_change_start_fall",
    "status", "chance_of_playing_next_round", "news", "news_added",
    "ep_this", "ep_next",
    # team_join_date is how a club move is detected. A player who has just
    # transferred carries minutes and system-fit risk that last season's
    # stats cannot show, so the model discounts him rather than trusting
    # rates earned in a different side.
    "team_join_date", "birth_date",
]

TEAM_FIELDS = [
    "id", "name", "short_name", "strength",
    "strength_overall_home", "strength_overall_away",
    "strength_attack_home", "strength_attack_away",
    "strength_defence_home", "strength_defence_away",
]

EVENT_FIELDS = [
    "id", "name", "deadline_time", "finished", "data_checked",
    "is_previous", "is_current", "is_next",
    "average_entry_score", "highest_score", "most_captained",
]

FIXTURE_FIELDS = [
    "id", "event", "kickoff_time", "team_h", "team_a",
    "team_h_difficulty", "team_a_difficulty", "finished",
    "team_h_score", "team_a_score",
]

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def fetch(url: str) -> dict | list:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    """Missing keys are written blank rather than crashing -- the FPL API
    adds and removes columns between seasons (defensive_contribution only
    appeared in 2025/26), and a snapshot that still runs on a renamed field
    is far more useful than one that dies on it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})
    print(f"[snapshot] wrote {path.relative_to(BASE_DIR)} ({len(rows)} rows)")


def missing_fields(rows: list[dict], fields: list[str]) -> list[str]:
    if not rows:
        return fields
    return [f for f in fields if f not in rows[0]]


def build_snapshot(summary: bool) -> None:
    print("[snapshot] fetching bootstrap-static...")
    bootstrap = fetch(BOOTSTRAP_URL)
    print("[snapshot] fetching fixtures...")
    fixtures = fetch(FIXTURES_URL)

    players = bootstrap["elements"]
    teams = bootstrap["teams"]
    events = bootstrap["events"]

    # Surface schema drift loudly rather than silently writing empty columns.
    for label, rows, fields in (
        ("players", players, PLAYER_FIELDS),
        ("teams", teams, TEAM_FIELDS),
        ("events", events, EVENT_FIELDS),
        ("fixtures", fixtures, FIXTURE_FIELDS),
    ):
        absent = missing_fields(rows, fields)
        if absent:
            print(f"[snapshot] WARNING: {label} missing expected fields: {absent}", file=sys.stderr)

    write_csv(DATA_DIR / "players.csv", players, PLAYER_FIELDS)
    write_csv(DATA_DIR / "teams.csv", teams, TEAM_FIELDS)
    write_csv(DATA_DIR / "events.csv", events, EVENT_FIELDS)
    write_csv(DATA_DIR / "fixtures.csv", fixtures, FIXTURE_FIELDS)

    now = datetime.now(timezone.utc)
    next_event = next((e for e in events if e.get("is_next")), None)
    if next_event is None:
        # Pre-season, before any gameweek is flagged current/next: fall back
        # to the first deadline still in the future.
        upcoming = [e for e in events if e.get("deadline_time", "") > now.isoformat()]
        next_event = upcoming[0] if upcoming else None

    # Record the API's full field list, not just the columns we keep. The
    # model needs to know what's *available* (e.g. whether a transfer date
    # exists to detect a club move) without another round-trip to find out,
    # and it makes schema drift between seasons visible in the diff.
    field_map = {
        "elements": sorted(players[0].keys()) if players else [],
        "teams": sorted(teams[0].keys()) if teams else [],
        "events": sorted(events[0].keys()) if events else [],
        "fixtures": sorted(fixtures[0].keys()) if fixtures else [],
    }
    (DATA_DIR / "api_fields.json").write_text(
        json.dumps(field_map, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[snapshot] api_fields.json: {len(field_map['elements'])} element fields available")

    meta = {
        "fetched_at": now.isoformat(),
        "total_players": len(players),
        "total_teams": len(teams),
        "total_fixtures": len(fixtures),
        "next_event": next_event["id"] if next_event else None,
        "next_deadline": next_event["deadline_time"] if next_event else None,
        "season_started": any(e.get("finished") for e in events),
    }
    (DATA_DIR / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"[snapshot] meta: {json.dumps(meta)}")

    if summary:
        print_summary(players, teams)


def print_summary(players: list[dict], teams: list[dict]) -> None:
    """A readable digest in the Actions log, so a run is useful even before
    anything pulls the CSVs."""
    by_id = {t["id"]: t["short_name"] for t in teams}

    def cost(p: dict) -> float:
        return p["now_cost"] / 10

    print("\n=== Most-owned player by position ===")
    for pos_id, pos_name in POSITIONS.items():
        pool = [p for p in players if p["element_type"] == pos_id]
        pool.sort(key=lambda p: float(p.get("selected_by_percent") or 0), reverse=True)
        print(f"\n{pos_name}:")
        for p in pool[:8]:
            print(
                f"  {p['web_name']:<18} {by_id.get(p['team'], '?'):<4} "
                f"£{cost(p):>4.1f}m  {p.get('selected_by_percent', '?'):>5}% owned  "
                f"news={p.get('news') or '-'}"
            )

    flagged = [p for p in players if (p.get("status") or "a") != "a"]
    print(f"\n=== {len(flagged)} flagged players ===")
    for p in sorted(flagged, key=lambda p: float(p.get("selected_by_percent") or 0), reverse=True)[:15]:
        print(f"  {p['web_name']:<18} {by_id.get(p['team'], '?'):<4} status={p.get('status')} — {p.get('news')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="store_true", help="print a readable digest to the log")
    args = parser.parse_args()
    build_snapshot(summary=args.summary)


if __name__ == "__main__":
    main()
