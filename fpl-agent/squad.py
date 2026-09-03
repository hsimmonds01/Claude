"""Reconstruct the manager's real squad from the FPL API.

Until a gameweek's deadline passes, FPL keeps a team private -- there is no
public endpoint that returns a squad in progress. That is why my_squad.json
was hand-maintained from screenshots up to now. Once a deadline has passed,
though, /api/entry/{id}/event/{event}/picks/ is a genuinely public endpoint:
anyone who knows the team ID can read it, no login required. This script is
what "team_id becomes the only field that matters" (see my_squad.json's
README) was always pointing at.

Deliberately never reads or writes the manager's real name. The entry
endpoint returns player_first_name/player_last_name alongside the team
data, and this repo is public -- so those two fields are the one thing this
script drops on the floor, every time, on purpose.

Rewrites my_squad.json's squad, my_lineup and bank from the live API,
preserving team_id, chips_used (merging in anything newly detected) and the
history log (appending one line). Leaves the file untouched on any failure,
so a flaky API response degrades to "yesterday's squad" rather than a
broken dashboard.

free_transfers is deliberately NOT touched here -- the API has no direct
"free transfers available" field, and deriving it correctly means replaying
every chip and transfer since the start of the season. Left manual; a wrong
automated number would be worse than an honest gap.

Usage:
  python squad.py                 update my_squad.json from FPL_TEAM_ID
  python squad.py --team-id 123   override the env var for a one-off check
  python squad.py --event 4       reconstruct a specific gameweek's picks
                                   instead of the latest finished one
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SQUAD_PATH = BASE_DIR / "my_squad.json"

API_ROOT = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FPLAgent/1.0)"}
TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

POSITION_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# FPL's own names for the chips this project's overrides.json/my_squad.json
# already talk about in plain English.
CHIP_NAMES = {
    "bboost": "bench_boost", "3xc": "triple_captain",
    "wildcard": "wildcard", "freehit": "free_hit",
}


def fetch_json(url: str) -> dict | None:
    """Returns None rather than raising -- a flaky fetch should leave
    my_squad.json untouched, not crash the whole pipeline."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            if response.status_code == 429:
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(f"[squad] rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if response.status_code == 404:
                print(f"[squad] 404 from {url} -- wrong team_id, or this gameweek "
                      f"hasn't had its deadline yet", file=sys.stderr)
                return None
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 -- retry, then give up
            if attempt == MAX_RETRIES:
                print(f"[squad] giving up on {url}: {exc}", file=sys.stderr)
                return None
            time.sleep(RETRY_BACKOFF_SECONDS)
    return None


def load_teams() -> dict[str, str]:
    with (DATA_DIR / "teams.csv").open(encoding="utf-8") as handle:
        return {row["id"]: row["short_name"] for row in csv.DictReader(handle)}


def load_players() -> dict[str, dict]:
    with (DATA_DIR / "players.csv").open(encoding="utf-8") as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def latest_reconstructable_event() -> int | None:
    """The most recent gameweek whose deadline has passed -- is_current
    covers the ongoing one, is_previous the one just finished, and either is
    fair game since the picks endpoint opens up right at the deadline, not
    at "finished"."""
    with (DATA_DIR / "events.csv").open(encoding="utf-8") as handle:
        events = list(csv.DictReader(handle))
    passed = [int(e["id"]) for e in events
              if e["is_current"] == "True" or e["is_previous"] == "True" or e["finished"] == "True"]
    return max(passed) if passed else None


def build_squad_entry(pick: dict, players: dict, teams: dict) -> dict | None:
    player = players.get(str(pick["element"]))
    if not player:
        return None
    return {
        "name": player["web_name"],
        "team": teams.get(player["team"], "?"),
        "position": POSITION_NAMES.get(int(player["element_type"]), "?"),
        "is_starter": pick["position"] <= 11,
        "is_captain": pick["is_captain"],
        "is_vice_captain": pick["is_vice_captain"],
    }


def reconstruct(team_id: str, event: int, players: dict, teams: dict) -> dict | None:
    """Fetches one gameweek's picks and turns them into my_squad.json's
    shape. Returns None on any failure -- see fetch_json's docstring."""
    picks_data = fetch_json(f"{API_ROOT}/entry/{team_id}/event/{event}/picks/")
    if picks_data is None:
        return None
    entry_data = fetch_json(f"{API_ROOT}/entry/{team_id}/")
    if entry_data is None:
        return None

    entries = [build_squad_entry(p, players, teams) for p in picks_data["picks"]]
    if any(e is None for e in entries) or len(entries) != 15:
        print(f"[squad] picks didn't resolve cleanly against players.csv "
              f"({len(entries)}/15) -- leaving my_squad.json untouched", file=sys.stderr)
        return None

    captain = next((e["name"] for e in entries if e["is_captain"]), None)
    vice = next((e["name"] for e in entries if e["is_vice_captain"]), None)
    bench = [e["name"] for e in entries if not e["is_starter"]]
    if captain is None or len(bench) != 4:
        print("[squad] no captain, or bench isn't 4 -- leaving my_squad.json untouched",
              file=sys.stderr)
        return None

    history = picks_data.get("entry_history", {})
    return {
        "team_name": entry_data.get("name", ""),
        "overall_points": entry_data.get("summary_overall_points"),
        "overall_rank": entry_data.get("summary_overall_rank"),
        "event": event,
        "event_points": history.get("points"),
        "bank": round(history.get("bank", 0) / 10, 1),
        "squad_value": round(history.get("value", 0) / 10, 1),
        "points_on_bench": history.get("points_on_bench"),
        "active_chip": picks_data.get("active_chip"),
        "squad": [{"name": e["name"], "team": e["team"], "position": e["position"]}
                  for e in entries],
        "captain": captain,
        "vice_captain": vice,
        "bench": bench,
    }


def update_my_squad_json(result: dict) -> None:
    config = json.loads(SQUAD_PATH.read_text(encoding="utf-8"))
    config["bank"] = result["bank"]
    config["squad"] = result["squad"]
    config["my_lineup"] = {
        "captain": result["captain"],
        "vice_captain": result["vice_captain"] or "",
        "bench": result["bench"],
    }

    chip = CHIP_NAMES.get(result["active_chip"])
    if chip and not any(c.get("chip") == chip and c.get("event") == result["event"]
                         for c in config.get("chips_used", [])):
        config.setdefault("chips_used", []).append({"chip": chip, "event": result["event"]})

    # Runs every few hours, and most runs find nothing new -- same gameweek,
    # same points, same captain. Appending unconditionally turned this into
    # a duplicate every 3 hours, forever. Only actually append when the
    # content differs from the last entry, so a re-run that finds nothing
    # new stays a no-op and the log only grows when something real changed
    # (bonus points settling, a captain change, a newly detected chip).
    line_body = (
        f"squad.py reconstructed GW{result['event']} from the live FPL API -- "
        f"{result['event_points']} pts this gameweek, £{result['squad_value']}m squad value, "
        f"£{result['bank']}m banked, captain {result['captain']}."
        + (f" Chip active: {chip}." if chip else "")
    )
    history = config.setdefault("history", [])
    last_body = history[-1].split(": ", 1)[1] if history and ": " in history[-1] else None
    if line_body != last_body:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        history.append(f"{stamp}: {line_body}")

    SQUAD_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[squad] my_squad.json updated from GW{result['event']} "
          f"({result['event_points']} pts, captain {result['captain']})")
    # Never printed, never stored: entry_data's player_first_name/player_last_name.
    print(f"[squad] overall rank {result['overall_rank']:,}" if result["overall_rank"] else
          "[squad] no overall rank yet")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-id", default=None, help="overrides FPL_TEAM_ID")
    parser.add_argument("--event", type=int, default=None,
                        help="reconstruct this gameweek instead of the latest passed one")
    args = parser.parse_args()

    team_id = args.team_id or os.environ.get("FPL_TEAM_ID", "").strip()
    if not team_id:
        print("[squad] FPL_TEAM_ID not set -- nothing to do (this is normal before "
              "the first deadline; my_squad.json stays hand-maintained until then)")
        return

    event = args.event or latest_reconstructable_event()
    if event is None:
        print("[squad] no gameweek has passed its deadline yet -- nothing to reconstruct")
        return

    players = load_players()
    teams = load_teams()
    result = reconstruct(team_id, event, players, teams)
    if result is None:
        return
    update_my_squad_json(result)


if __name__ == "__main__":
    main()
