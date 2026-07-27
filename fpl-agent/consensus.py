"""What the published drafts agree on, and where your squad differs.

Before a ball is kicked there is no ownership figure worth trusting: FPL's
percentages swing enormously in the final week before the deadline, so the
risk setting in strategy.md is working from numbers that will not survive
contact with the deadline. What experienced analysts have actually picked is
the best early read available on where the template is forming.

Consensus across independent drafts is the signal. A player three of three
have picked is close to essential; a player one of three has picked is that
analyst's differential, not a template pick. This deliberately reports the
count rather than treating any single draft as authoritative -- these are
opinions, and the point is to see where they converge.

Reads knowledge/sample_teams.json (captured by hand from public posts) and
my_squad.json, and writes data/consensus.json for the email and dashboard.

Usage:
  python consensus.py            print the comparison and write the json
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SAMPLES_PATH = BASE_DIR / "knowledge" / "sample_teams.json"
SQUAD_PATH = BASE_DIR / "my_squad.json"
OUT_PATH = DATA_DIR / "consensus.json"


def load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def build() -> dict:
    samples = load(SAMPLES_PATH, {})
    teams = samples.get("teams", [])
    if not teams:
        raise SystemExit(f"No sample teams in {SAMPLES_PATH}")

    picked_by: dict[tuple, list[str]] = defaultdict(list)
    for entry in teams:
        source = entry["source"]
        # Bench and starters both count as "picked" -- squad membership is the
        # template signal, where in the XI they sit is a weekly decision.
        for player in entry.get("starting", []) + entry.get("bench", []):
            picked_by[(player["name"], player["team"])].append(source)

    squad = load(SQUAD_PATH, {}).get("squad", [])
    mine = {(p["name"], p.get("team", "")) for p in squad}
    mine_names = {p["name"] for p in squad}

    total = len(teams)
    rows = []
    for (name, team), sources in picked_by.items():
        owned = (name, team) in mine or name in mine_names
        rows.append({
            "name": name, "team": team, "picked_by": len(sources),
            "of": total, "sources": sources, "in_my_squad": owned,
        })
    rows.sort(key=lambda r: (-r["picked_by"], r["name"]))

    consensus = [r for r in rows if r["picked_by"] >= 2]
    missing = [r for r in consensus if not r["in_my_squad"]]
    unique_to_me = sorted(
        n for n in mine_names
        if not any(r["name"] == n for r in rows)
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "drafts_compared": total,
        "sources": [t["source"] for t in teams],
        "all_picks": rows,
        "consensus_picks": consensus,
        "consensus_missing_from_my_squad": missing,
        "my_players_no_draft_picked": unique_to_me,
    }


def main() -> None:
    payload = build()
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    total = payload["drafts_compared"]
    print(f"Comparing {total} published drafts: {', '.join(payload['sources'])}\n")

    print(f"{'player':<16}{'team':<6}{'drafts':>7}  yours?")
    print("-" * 42)
    for row in payload["consensus_picks"]:
        mark = "yes" if row["in_my_squad"] else "NO"
        print(f"{row['name']:<16}{row['team']:<6}{row['picked_by']}/{row['of']:<5}  {mark}")

    if payload["consensus_missing_from_my_squad"]:
        print("\nPicked by 2+ drafts but NOT in your squad:")
        for row in payload["consensus_missing_from_my_squad"]:
            print(f"  {row['name']} ({row['team']}) — {', '.join(row['sources'])}")

    if payload["my_players_no_draft_picked"]:
        print("\nYour players no published draft picked (your differentials):")
        print("  " + ", ".join(payload["my_players_no_draft_picked"]))

    print(f"\n[consensus] wrote data/consensus.json")


if __name__ == "__main__":
    main()
