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

Rather than start the season guessing the threshold and spend two months
learning it, the probabilities are FITTED FROM PAST SEASONS -- 73,341
player-gameweek transitions across 2023-24, 2024-25 and 2025-26. The
relationship is strong and monotonic, and notably asymmetric:

    net transfers per owner      P(rise)   P(fall)
    -0.60 to -0.30                  0.4%     46.8%
    -0.30 to -0.15                  0.2%     28.2%
    -0.05 to +0.05                  0.6%      2.8%
    +0.15 to +0.30                 16.6%      2.2%
    +0.60 and above                24.1%      2.1%

Falls trigger far more easily than rises: a player being sold at 0.30 net
per owner is twice as likely to drop as one being bought at 0.60 is to
rise. That asymmetry is in the data, not an artefact, and it means "sell
before he drops" alerts fire earlier and more confidently than "buy before
he rises" ones.

Granularity caveat, stated plainly: the archive is per gameweek while real
prices move nightly, so this predicts "will his price change before the next
deadline" rather than "will it change tonight". Live readings are scaled to
a seven-day equivalent before lookup, otherwise a daily observation gets
compared against a weekly threshold and nothing ever looks close to moving.

Outputs:
  data/price_history.csv       append-only: one row per player per snapshot
  data/price_calibration.json  fitted probabilities, re-derivable
  data/price_watch.json        current risers/fallers, with confidence

Usage:
  python price.py                 record a snapshot and report movers
  python price.py --calibrate     refit probabilities from past seasons
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
CALIBRATION_PATH = DATA_DIR / "price_calibration.json"
GW_DIR = DATA_DIR / "history_gw"

# Bands of net-transfers-per-owner used to fit the change probabilities.
RATIO_BANDS = [
    (-99.0, -0.60), (-0.60, -0.30), (-0.30, -0.15), (-0.15, -0.05),
    (-0.05, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 0.60), (0.60, 99.0),
]
MIN_BAND_SAMPLES = 50
# Owners below this are too small a base for the ratio to mean anything.
MIN_OWNERS = 1000
# Calibration is fitted per GAMEWEEK, but snapshots are taken more often than
# that. Observed ratios are scaled to a seven-day equivalent before lookup,
# otherwise a daily reading is compared against a weekly threshold and
# nothing ever looks close to changing.
CALIBRATION_WINDOW_DAYS = 7.0

HISTORY_FIELDS = [
    "recorded_at", "player_id", "web_name", "now_cost",
    "transfers_in", "transfers_out", "selected_by_percent", "price_change_percent",
]

# Report a player once his probability of changing price passes this.
ALERT_THRESHOLD = 0.20
# Below this many observations the momentum estimate isn't worth much, and
# the report says so rather than implying precision it doesn't have.
MIN_OBSERVATIONS = 3


def calibrate() -> None:
    """Fit price-change probabilities from past seasons.

    Answers the question the live watcher needs and cannot yet observe: how
    much net transfer traffic actually precedes a price change? Fitted from
    every player-gameweek transition in the archive rather than assumed, so
    the watcher is useful from the first week of the season instead of
    spending two months learning.

    Granularity caveat, stated because it matters: the archive is per
    gameweek and real prices change nightly. This therefore predicts "will
    his price move before the next deadline", not "will it move tonight" --
    which is the question a weekly email actually asks anyway.
    """
    seasons = sorted(p.stem for p in GW_DIR.glob("*.csv"))
    if not seasons:
        raise SystemExit("No archive found -- run `python gwdata.py` first.")

    by_player: dict[tuple, dict[int, dict]] = {}
    for season in seasons:
        with (GW_DIR / f"{season}.csv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                by_player.setdefault((season, row["name"]), {})[int(num(row["round"]))] = row

    observations = []
    for gameweeks in by_player.values():
        for round_number in sorted(gameweeks):
            following = gameweeks.get(round_number + 1)
            if not following:
                continue
            owners = num(gameweeks[round_number]["selected"])
            if owners < MIN_OWNERS:
                continue
            net = num(following["transfers_in"]) - num(following["transfers_out"])
            observations.append({
                "delta": num(following["value"]) - num(gameweeks[round_number]["value"]),
                "ratio": net / owners,
            })

    bands = []
    for low, high in RATIO_BANDS:
        group = [o for o in observations if low <= o["ratio"] < high]
        if len(group) < MIN_BAND_SAMPLES:
            continue
        bands.append({
            "low": low, "high": high, "samples": len(group),
            "p_rise": round(sum(1 for o in group if o["delta"] > 0) / len(group), 4),
            "p_fall": round(sum(1 for o in group if o["delta"] < 0) / len(group), 4),
        })

    CALIBRATION_PATH.write_text(json.dumps({
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "seasons": seasons,
        "observations": len(observations),
        "window_days": CALIBRATION_WINDOW_DAYS,
        "bands": bands,
        "_note": "P(price change) by net-transfers-per-owner over one gameweek. "
                 "Fitted by `python price.py --calibrate` from the gameweek archive. "
                 "Falls trigger more easily than rises -- that asymmetry is in the data, "
                 "not an artefact.",
    }, indent=2) + "\n", encoding="utf-8")

    print(f"[price] fitted {len(bands)} bands from {len(observations)} observations "
          f"across {', '.join(seasons)}")
    print(f"\n{'band':<20}{'n':>8}{'P(rise)':>10}{'P(fall)':>10}")
    for band in bands:
        label = f"{band['low']:+.2f} to {band['high']:+.2f}"
        print(f"{label:<20}{band['samples']:>8}{band['p_rise']:>10.1%}{band['p_fall']:>10.1%}")


def load_calibration() -> dict:
    if not CALIBRATION_PATH.exists():
        return {}
    return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))


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
        elapsed_days = 1.0
        try:
            then = datetime.fromisoformat(previous["recorded_at"])
            elapsed_days = max((datetime.now(timezone.utc) - then).total_seconds() / 86400, 0.25)
        except (ValueError, KeyError):
            pass
        result[player["id"]] = {
            "net": delta, "rate": delta / owners, "observations": observations,
            "days_elapsed": elapsed_days,
        }
    return result


def band_for(ratio: float, calibration: dict) -> dict | None:
    for band in calibration.get("bands", []):
        if band["low"] <= ratio < band["high"]:
            return band
    return None


def assess(players: list[dict], history: list[dict]) -> list[dict]:
    """Current risers and fallers, with a probability rather than a hunch."""
    moves = momentum(players, history)
    calibration = load_calibration()
    watch = []

    for player in players:
        official = player.get("price_change_percent", "")
        signal = moves.get(player["id"], {})
        probability, direction, source = None, "", ""

        if num(official) != 0:
            # FPL's own progress figure: negative means heading down.
            probability = min(abs(num(official)) / 100, 1.0)
            direction = "rise" if num(official) > 0 else "fall"
            source = "FPL's own price_change_percent"

        elif signal.get("observations", 0) >= MIN_OBSERVATIONS and calibration:
            # Scale the observed ratio to the gameweek-equivalent the
            # calibration was fitted on. Skipping this compares a one-day
            # reading against a seven-day threshold, and nothing ever looks
            # close to moving.
            days = max(signal.get("days_elapsed", 1.0), 0.25)
            weekly_ratio = signal["rate"] * (CALIBRATION_WINDOW_DAYS / days)
            band = band_for(weekly_ratio, calibration)
            if band:
                rise, fall = band["p_rise"], band["p_fall"]
                probability = max(rise, fall)
                direction = "rise" if rise >= fall else "fall"
                source = (f"fitted from {calibration['observations']:,} past observations "
                          f"({signal['observations']} snapshots)")

        if probability is None or probability < ALERT_THRESHOLD:
            continue

        watch.append({
            "player_id": player["id"], "name": player["web_name"],
            "price": num(player["now_cost"]) / 10,
            "owned": num(player.get("selected_by_percent")),
            "direction": direction,
            "probability": round(probability, 3),
            "net_transfers": int(signal.get("net", 0)),
            "source": source,
            "confidence": "official" if "price_change_percent" in source else "estimated",
        })
    return sorted(watch, key=lambda w: -w["probability"])


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

    print(f"\n{'player':<16}{'£':>6}{'owned%':>8}{'move':>7}{'chance':>8}  basis")
    for entry in watch[:15]:
        print(f"{entry['name']:<16}{entry['price']:>6.1f}{entry['owned']:>8.1f}"
              f"{entry['direction']:>7}{entry['probability']:>8.0%}  {entry['source']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="report only, don't append history")
    parser.add_argument("--calibrate", action="store_true",
                        help="fit change probabilities from past seasons, then exit")
    args = parser.parse_args()

    if args.calibrate:
        calibrate()
        return

    players = read_players()
    history = read_history()
    if not args.report:
        append_history(players)
    report(players, history)


if __name__ == "__main__":
    main()
