"""Fetch per-player history from the FPL API.

snapshot.py captures the *current* state of the game (prices, ownership,
fixtures). This captures the *past*: how every player actually performed in
previous seasons, which is the only real evidence available before a ball is
kicked. At GW1 there is no form, no minutes and no xG for the season ahead --
so last season's per-90 rates, adjusted for the move to a new club or a new
role, are what the model has to reason from.

Source: /api/element-summary/{id}/, which returns
  history_past  one row per previous season the player has PL data for
  history       one row per gameweek of the current season (empty until GW1)
  fixtures      that player's upcoming fixtures

Outputs:
  data/player_history_past.csv     season totals per player, all seasons
  data/player_history_current.csv  per-gameweek rows for the live season

Runs ~560 requests with a polite delay, so expect a few minutes. Designed to
be re-runnable: it always rewrites both files from scratch.

Usage:
  python history.py               fetch everything
  python history.py --limit 20    fetch only the first 20 players (smoke test)
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PLAYERS_CSV = DATA_DIR / "players.csv"

ELEMENT_SUMMARY_URL = "https://fantasy.premierleague.com/api/element-summary/{id}/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FPLAgent/1.0)"}
TIMEOUT_SECONDS = 30

# Be a good citizen: this is someone else's free API and we only need it a
# couple of times a season.
DELAY_SECONDS = 0.25
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

PAST_FIELDS = [
    "season_name", "start_cost", "end_cost", "total_points", "minutes",
    "starts", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
    "red_cards", "saves", "bonus", "bps",
    "influence", "creativity", "threat", "ict_index",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "defensive_contribution",
]

CURRENT_FIELDS = [
    "round", "fixture", "opponent_team", "was_home", "kickoff_time",
    "total_points", "minutes", "starts", "goals_scored", "assists",
    "clean_sheets", "goals_conceded", "yellow_cards", "red_cards", "saves",
    "bonus", "bps", "influence", "creativity", "threat", "ict_index",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "defensive_contribution",
    "value", "selected", "transfers_in", "transfers_out",
]

# Identity columns prefixed onto every row so the CSVs are usable on their
# own, without needing a join back to players.csv.
IDENTITY = ["player_id", "web_name", "team", "element_type"]


def load_players(limit: int | None) -> list[dict]:
    if not PLAYERS_CSV.exists():
        raise SystemExit(
            f"{PLAYERS_CSV} not found -- run snapshot.py first; history.py builds on its player list."
        )
    with PLAYERS_CSV.open(encoding="utf-8") as handle:
        players = list(csv.DictReader(handle))
    return players[:limit] if limit else players


def fetch_summary(player_id: str) -> dict | None:
    """Returns None rather than raising if a single player can't be fetched --
    one bad id shouldn't throw away the other 557 rows."""
    url = ELEMENT_SUMMARY_URL.format(id=player_id)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            if response.status_code == 429:
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(f"[history] rate limited on {player_id}, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 -- retry, then give up on this player
            if attempt == MAX_RETRIES:
                print(f"[history] giving up on player {player_id}: {exc}", file=sys.stderr)
                return None
            time.sleep(RETRY_BACKOFF_SECONDS)
    return None


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})
    print(f"[history] wrote {path.relative_to(BASE_DIR)} ({len(rows)} rows)")


def run(limit: int | None) -> None:
    players = load_players(limit)
    print(f"[history] fetching history for {len(players)} players...")

    past_rows: list[dict] = []
    current_rows: list[dict] = []
    failures = 0

    for index, player in enumerate(players, start=1):
        summary = fetch_summary(player["id"])
        if summary is None:
            failures += 1
            continue

        identity = {
            "player_id": player["id"],
            "web_name": player["web_name"],
            "team": player["team"],
            "element_type": player["element_type"],
        }
        for season in summary.get("history_past", []):
            past_rows.append({**identity, **season})
        for gameweek in summary.get("history", []):
            current_rows.append({**identity, **gameweek})

        if index % 50 == 0:
            print(f"[history]   {index}/{len(players)} players, {len(past_rows)} past-season rows so far")
        time.sleep(DELAY_SECONDS)

    write_csv(DATA_DIR / "player_history_past.csv", past_rows, IDENTITY + PAST_FIELDS)
    write_csv(DATA_DIR / "player_history_current.csv", current_rows, IDENTITY + CURRENT_FIELDS)

    seasons = sorted({r.get("season_name", "") for r in past_rows if r.get("season_name")})
    print(f"[history] done. {len(past_rows)} past-season rows covering {seasons or 'no seasons'}")
    print(f"[history] {len(current_rows)} current-season gameweek rows")
    if failures:
        print(f"[history] WARNING: {failures} players could not be fetched", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="only fetch the first N players")
    args = parser.parse_args()
    run(limit=args.limit)


if __name__ == "__main__":
    main()
