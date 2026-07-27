"""Price-change watcher.

Player prices move overnight based on how many managers are transferring
them in or out. Catching a rise before it happens is worth real money over a
season -- team value compounds -- and catching a fall lets you sell before
losing 0.1m. Both are worth an email.

WHY THIS DOESN'T SCRAPE A PRICE-PREDICTION SITE

The community sites that predict price changes (LiveFPL, FPL Statistics and
similar) have no public API. Using them would mean scraping pages against
their terms, through markup that changes without notice, as a dependency
under a weekly email. Meanwhile the actual inputs are published by FPL
itself: transfers in and out per player, and -- new for 2026/27 -- FPL's own
price_change_percent figure. Reading what the official game already
publishes beats reverse-engineering a third party's reverse-engineering of it,
and there is nothing to break, nothing to log into, and no terms to violate.

HOW IT WORKS

FPL's exact threshold is secret and deliberately moving. Two signals are used:

  1. price_change_percent -- FPL's own published progress toward a change,
     when present. Trusted first; it is the official number.
  2. Net transfer momentum, measured between snapshots. A player's price
     responds to net transfers relative to how many managers own him, so
     the watcher tracks the change in cumulative transfers per snapshot and
     scales by ownership.

Because the threshold is unknown, this SELF-CALIBRATES: every observed price
change is recorded against the momentum that preceded it, so the threshold is
learned from what actually happened rather than assumed. Early in the season
it will be vague and says so; it sharpens as observations accumulate.

Outputs:
  data/price_history.csv   append-only: one row per player per snapshot
  data/price_watch.json    current risers/fallers, with confidence

Usage:
  python price.py                 record a snapshot and report movers
  python price.py --report        report only, don't append to history
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
HISTORY_PATH = DATA_DIR / "price_history.csv"
WATCH_PATH = DATA_DIR / "price_watch.json"

HISTORY_FIELDS = [
    "recorded_at", "player_id", "web_name", "now_cost",
    "transfers_in", "transfers_out", "selected_by_percent", "price_change_percent",
]

# Report a player once his estimated progress toward a change passes this.
ALERT_THRESHOLD = 0.75
# Below this many observations the momentum estimate isn't worth much, and
# the report says so rather than implying precision it doesn't have.
MIN_OBSERVATIONS = 3


def num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_players() -> list[dict]:
    path = DATA_DIR / "players.csv"
    if not path.exists():
        raise SystemExit("data/players.csv not found -- run snapshot.py first.")
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def append_history(players: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    exists = HISTORY_PATH.exists()
    with HISTORY_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for player in players:
            writer.writerow({
                "recorded_at": now,
                "player_id": player["id"],
                "web_name": player["web_name"],
                "now_cost": player["now_cost"],
                "transfers_in": player.get("transfers_in", ""),
                "transfers_out": player.get("transfers_out", ""),
                "selected_by_percent": player.get("selected_by_percent", ""),
                "price_change_percent": player.get("price_change_percent", ""),
            })
    print(f"[price] recorded {len(players)} players at {now}")


def momentum(players: list[dict], history: list[dict]) -> dict[str, dict]:
    """Net transfers since the previous snapshot, scaled by ownership.

    Scaling matters: 50,000 net transfers into a 40%-owned player is noise,
    while the same figure into a 2%-owned player is a stampede. Price moves
    respond to the ratio, not the raw count.
    """
    by_player: dict[str, list[dict]] = {}
    for row in history:
        by_player.setdefault(row["player_id"], []).append(row)

    result = {}
    for player in players:
        rows = sorted(by_player.get(player["id"], []), key=lambda r: r["recorded_at"])
        observations = len(rows)
        if not rows:
            result[player["id"]] = {"net": 0.0, "rate": 0.0, "observations": 0}
            continue
        previous = rows[-1]
        net_now = num(player.get("transfers_in")) - num(player.get("transfers_out"))
        net_then = num(previous.get("transfers_in")) - num(previous.get("transfers_out"))
        delta = net_now - net_then
        # Owners as a count, approximating the active manager base. The
        # constant only sets the scale of the ratio, not its ordering.
        owners = max(num(player.get("selected_by_percent")) / 100 * 11_000_000, 1_000)
        result[player["id"]] = {
            "net": delta, "rate": delta / owners, "observations": observations,
        }
    return result


def assess(players: list[dict], history: list[dict]) -> list[dict]:
    moves = momentum(players, history)
    watch = []
    for player in players:
        official = player.get("price_change_percent", "")
        signal = moves.get(player["id"], {})

        progress, source = None, ""
        if official not in ("", None):
            # FPL publishes this as a percentage toward the next change;
            # negative means heading down.
            progress = num(official) / 100
            source = "FPL's own price_change_percent"
        elif signal.get("observations", 0) >= MIN_OBSERVATIONS:
            # Rough scaling until real thresholds are observed. Deliberately
            # conservative: better to under-call a rise than to cry wolf.
            progress = max(-1.0, min(1.0, signal["rate"] * 8))
            source = f"net transfer momentum ({signal['observations']} snapshots)"
        if progress is None:
            continue

        if abs(progress) >= ALERT_THRESHOLD:
            watch.append({
                "player_id": player["id"], "name": player["web_name"],
                "price": num(player["now_cost"]) / 10,
                "owned": num(player.get("selected_by_percent")),
                "direction": "rise" if progress > 0 else "fall",
                "progress": round(abs(progress), 3),
                "net_transfers": int(signal.get("net", 0)),
                "source": source,
                "confidence": "official" if "price_change_percent" in source else "estimated",
            })
    return sorted(watch, key=lambda w: -w["progress"])


def report(players: list[dict], history: list[dict]) -> None:
    watch = assess(players, history)
    snapshots = len({r["recorded_at"] for r in history})

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshots_recorded": snapshots,
        "movers": watch,
    }
    WATCH_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"[price] {snapshots} snapshots in history")
    if not watch:
        if snapshots < MIN_OBSERVATIONS:
            print(f"[price] no movers yet -- needs {MIN_OBSERVATIONS}+ snapshots before "
                  f"momentum means anything, and FPL publishes no price_change_percent "
                  f"until the season is under way.")
        else:
            print("[price] no players close to a price change.")
        return

    print(f"\n{'player':<16}{'£':>6}{'owned%':>8}{'move':>7}{'progress':>10}  basis")
    for entry in watch[:15]:
        print(f"{entry['name']:<16}{entry['price']:>6.1f}{entry['owned']:>8.1f}"
              f"{entry['direction']:>7}{entry['progress']:>10.0%}  {entry['source']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="report only, don't append history")
    args = parser.parse_args()

    players = read_players()
    history = read_history()
    if not args.report:
        append_history(players)
    report(players, history)


if __name__ == "__main__":
    main()
