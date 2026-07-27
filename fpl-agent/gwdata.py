"""Fetch per-gameweek history for past seasons.

The FPL API only exposes season *totals* for previous campaigns, which is
enough to compare whole seasons but not enough to answer the question that
actually matters: how good are the model's picks over the opening five
gameweeks, when it has no current-season form to work from?

This pulls the community archive at github.com/vaastav/Fantasy-Premier-League,
which mirrors the FPL API gameweek by gameweek and keeps seasons after the
official API has moved on. One row per player per gameweek, including the
price he was that week and the underlying stats behind his return.

It also fixes a real weakness in the season-totals backtest: that test could
only see players still in FPL today, so anyone who left the league was
invisible and the results flattered every strategy. These files contain the
whole player pool as it was at the time.

Outputs data/history_gw/{season}.csv.

Usage:
  python gwdata.py                          fetch the default seasons
  python gwdata.py --seasons 2023-24 2024-25 2025-26
"""

from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "data" / "history_gw"

SOURCE = ("https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/"
          "master/data/{season}/gws/merged_gw.csv")
DEFAULT_SEASONS = ["2024-25", "2025-26"]
TIMEOUT_SECONDS = 120

# The archive carries ~40 columns; these are the ones the model and the
# backtest use. Anything absent in an older season is written blank rather
# than breaking the fetch -- the schema genuinely changes between seasons
# (defensive_contribution only exists from 2025/26, when DefCon was introduced).
KEEP = [
    "name", "position", "team", "element", "round", "fixture", "opponent_team",
    "was_home", "kickoff_time", "value", "selected",
    # Price-change calibration: how many managers moved a player in or out
    # over a gameweek, against how his price moved. Lets the threshold be
    # fitted from thousands of past observations instead of guessed.
    "transfers_in", "transfers_out", "transfers_balance",
    "minutes", "starts", "total_points", "goals_scored", "assists",
    "clean_sheets", "goals_conceded", "saves", "bonus", "bps",
    "yellow_cards", "red_cards", "own_goals",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded",
    "defensive_contribution", "clearances_blocks_interceptions", "recoveries", "tackles",
    "influence", "creativity", "threat", "ict_index", "xP",
]


def fetch_season(season: str) -> int:
    url = SOURCE.format(season=season)
    print(f"[gwdata] fetching {season}...")
    response = requests.get(url, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    if not rows:
        raise RuntimeError(f"{season}: no rows returned")

    absent = [c for c in KEEP if c not in rows[0]]
    if absent:
        print(f"[gwdata]   note: {season} has no {absent} (expected for older seasons)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{season}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=KEEP, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in KEEP})

    gameweeks = len({r.get("round") for r in rows})
    players = len({r.get("name") for r in rows})
    print(f"[gwdata]   {len(rows)} rows, {players} players, {gameweeks} gameweeks -> "
          f"data/history_gw/{season}.csv")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    args = parser.parse_args()
    for season in args.seasons:
        fetch_season(season)


if __name__ == "__main__":
    main()
