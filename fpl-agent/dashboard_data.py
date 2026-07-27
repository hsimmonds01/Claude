"""Export everything the dashboard needs as one JSON file.

The dashboard is a static page -- no server, no build step, same pattern as
the other projects in this repo. That means all the thinking has to happen
here, ahead of time, and the page just draws what it's given.

Includes, for every player, the affordable alternatives at his position.
Precomputing that is what makes "click a player, see who I could swap him
for" instant and offline, rather than needing an API call per click.

Output: data/dashboard.json

Usage:
  python dashboard_data.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from optimiser import (
    BUDGET, MAX_PER_CLUB, TEMPLATE_OWNERSHIP, load_overrides, load_projections,
    load_strategy, ownership_gap, pick_team, resolve_squad, squad_value,
    suggest_transfers,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUT_PATH = DATA_DIR / "dashboard.json"

# How many replacement options to precompute per player.
ALTERNATIVES_PER_PLAYER = 12


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def slim(player: dict, extra: dict | None = None) -> dict:
    out = {
        "id": player["player_id"], "name": player["name"], "team": player["team"],
        "position": player["position"], "price": round(player["price"], 1),
        "owned": round(player["selected_by"], 1), "xp": round(player["total"], 1),
        "start_prob": round(player["start_prob"], 2),
        "moved_club": bool(player.get("moved_club")),
        "minutes_reason": player.get("minutes_reason", ""),
    }
    if extra:
        out.update(extra)
    return out


def alternatives_for(player: dict, squad: list[dict], players: dict,
                     bank: float, baseline: float) -> list[dict]:
    """Who you could swap this player for, ranked by effect on your XI.

    Uses the same rules the optimiser applies -- budget, position, the
    3-per-club limit -- so what the dashboard offers and what the email
    recommends can never disagree.
    """
    squad_ids = {p["player_id"] for p in squad}
    club_counts: dict[str, int] = {}
    for member in squad:
        club_counts[member["team"]] = club_counts.get(member["team"], 0) + 1

    budget = bank + player["price"]
    options = []
    for candidate in players.values():
        if candidate["player_id"] in squad_ids or candidate["position"] != player["position"]:
            continue
        if candidate["price"] > budget + 1e-9:
            continue
        projected = club_counts.get(candidate["team"], 0) - (1 if candidate["team"] == player["team"] else 0)
        if projected >= MAX_PER_CLUB:
            continue
        trial = [candidate if p is player else p for p in squad]
        options.append(slim(candidate, {
            "gain": round(squad_value(trial) - baseline, 2),
            "bank_after": round(budget - candidate["price"], 1),
        }))
    options.sort(key=lambda o: -o["gain"])
    return options[:ALTERNATIVES_PER_PLAYER]


def build() -> dict:
    players, events = load_projections()
    squad, config = resolve_squad(players)
    strategy = load_strategy()
    overrides = load_overrides()
    starters, bench, captain = pick_team(squad)
    bank = float(config.get("bank") or 0.0)
    free_transfers = int(config.get("free_transfers") or 1)
    baseline = squad_value(squad)

    meta = load_json(DATA_DIR / "meta.json", {})
    news = load_json(DATA_DIR / "news_flags.json", {}).get("flags", [])
    prices = load_json(DATA_DIR / "price_watch.json", {}).get("movers", [])
    squad_names = {p["name"] for p in squad}

    transfers = [
        {"out": slim(option["out"]), "in": slim(option["in"]),
         "gain": round(option["gain"], 2), "bank_after": round(option["bank_after"], 1)}
        for option in suggest_transfers(squad, players, bank, free_transfers, overrides, limit=8)
    ]

    squad_out = []
    for player in squad:
        entry = slim(player, {
            "role": "captain" if player is captain else ("starter" if player in starters else "bench"),
            "bench_order": bench.index(player) + 1 if player in bench else None,
            "alternatives": alternatives_for(player, squad, players, bank, baseline),
            "override": overrides.get(player["name"], {}).get("note", ""),
        })
        squad_out.append(entry)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "next_event": meta.get("next_event"),
        "next_deadline": meta.get("next_deadline"),
        "horizon": {"from": events[0], "to": events[-1]} if events else None,
        "budget": BUDGET,
        "bank": bank,
        "free_transfers": free_transfers,
        "risk": strategy["risk"],
        "squad_cost": round(sum(p["price"] for p in squad), 1),
        "projected_xi": round(sum(p["total"] for p in starters), 1),
        "captain": captain["name"],
        "squad": squad_out,
        "transfers": transfers,
        "template_gap": [
            slim(p, {"exposure": round(p["exposure"], 1)}) for p in ownership_gap(squad, players)[:8]
        ],
        "news": [n for n in news if n["player"] in squad_names],
        "prices": [p for p in prices if p["name"] in squad_names],
        "template_threshold": TEMPLATE_OWNERSHIP,
    }


def main() -> None:
    payload = build()
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total_alternatives = sum(len(p["alternatives"]) for p in payload["squad"])
    print(f"[dashboard] wrote data/dashboard.json — {len(payload['squad'])} players, "
          f"{total_alternatives} precomputed alternatives, "
          f"{len(payload['transfers'])} transfer options")


if __name__ == "__main__":
    main()
