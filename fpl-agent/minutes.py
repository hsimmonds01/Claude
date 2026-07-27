"""Minutes model -- the part that actually decides whether a projection is right.

Backtesting showed 80% of the model's absolute error came from minutes
changing, not from mis-estimating how good players are. Its worst picks were
never players who underperformed; they were players who stopped playing. So
this is where the effort belongs, and everything in here has been tested
against what really happened rather than reasoned about and assumed.

WHAT THE EVIDENCE SUPPORTED

  Recency weighting. A player's start rate over the LAST TEN gameweeks of the
  previous season predicts his opening-five start rate better than his rate
  across the whole season -- because it reflects the pecking order he ended
  on, not the one he began with. A 60/40 blend of recent to full-season
  tested best, and improved on both season pairs:

      2023-24 -> 2024-25   corr 0.648 -> 0.676   MAE 0.224 -> 0.208
      2024-25 -> 2025-26   corr 0.628 -> 0.653   MAE 0.213 -> 0.204

  End-to-end -- actual points scored by the squad this builds -- it wins
  overall, but only on the aggregate. On a single season it can lose badly:

      pair                GWs   season avg   recency blend
      2023-24 -> 2024-25    5          190             278
      2023-24 -> 2024-25   10          386             516
      2024-25 -> 2025-26    5          171             149
      2024-25 -> 2025-26   10          314             286
      TOTAL                 5          361             427
      TOTAL                10          700             802

  That 2025/26 row is the reason this file exists in its current form. Tested
  against that season alone, the change looks like a clear regression and the
  obvious move is to revert it. It was one noisy sample: an eleven-player XI
  over five gameweeks swings on two or three picks, and here it swung on
  Isak, who the blend liked and who then barely played. The predictor-level
  test -- 465 players, both season pairs, same direction both times -- is far
  better powered than the outcome test, and both agree once you stop looking
  at a single season.

  Excluding the final three gameweeks was also tried, on the theory that
  teams with nothing to play for rest their starters. The rotation is real
  (regular starters drop from 0.796 in GW29-34 to 0.728 in GW36-38) but
  cutting those weeks made prediction WORSE (5-GW total 389 vs 427), so the
  full GW29-38 window is kept. A true premise does not guarantee a useful
  adjustment.

WHAT THE EVIDENCE DID NOT SUPPORT, DESPITE BEING PLAUSIBLE

  A blanket club-move discount. This seemed obviously right -- a new signing
  is a rotation risk -- and an earlier version of the model applied one. The
  data disagrees, and disagrees in opposite directions across two seasons:

      2023-24 -> 2024-25   movers over-predicted by 0.018
      2024-25 -> 2025-26   movers UNDER-predicted by 0.032

  With ~40 movers per season that is noise, not signal. The intuition is
  right about specific cases (Isak, Wissa and Wood all collapsed after
  moves) but wrong as a class-wide rule: most new signings walk straight
  into the team, because that is why the club bought them. A blanket
  discount would mis-rank the many to catch the few, so this model does not
  apply one. Individual cases belong in overrides.json, where a human can
  name the specific reason.

  A penalty on expensive players. £7m+ players were over-predicted by 0.141
  in 2025/26 but only 0.011 in 2024/25 -- one season's busts, not a pattern.

Both were left out deliberately. Fitting them would have improved the
backtest and made the live model worse.

Usage:
  python minutes.py --evaluate      re-run the tests above against the archive
  python minutes.py --player NAME   show how one player's start rate is derived
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
GW_DIR = DATA_DIR / "history_gw"

# The last ten gameweeks of a season carry most of the signal about where a
# player stands going into the next one.
RECENT_FROM_GW = 29
RECENT_WEIGHT = 0.6
SEASON_WEIGHT = 1.0 - RECENT_WEIGHT

TOTAL_GAMEWEEKS = 38


def num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_archive(season: str) -> list[dict]:
    path = GW_DIR / f"{season}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def start_rates(season: str) -> dict[str, dict]:
    """Full-name -> start rates for one season, from the gameweek archive.

    Returns both the whole-season rate and the last-ten-gameweek rate, plus
    the blend, so callers can show the working rather than just the answer.
    """
    rows = load_archive(season)
    if not rows:
        return {}

    by_player: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        by_player[row["name"]][int(num(row["round"]))] = row

    rates = {}
    for name, gameweeks in by_player.items():
        all_rounds = sorted(gameweeks)
        recent_rounds = [r for r in all_rounds if r >= RECENT_FROM_GW]
        if not all_rounds:
            continue
        season_rate = sum(num(gameweeks[r]["starts"]) for r in all_rounds) / len(all_rounds)
        recent_rate = (sum(num(gameweeks[r]["starts"]) for r in recent_rounds) / len(recent_rounds)
                       if recent_rounds else season_rate)
        rates[name] = {
            "season_rate": season_rate,
            "recent_rate": recent_rate,
            "blended": RECENT_WEIGHT * recent_rate + SEASON_WEIGHT * season_rate,
            "team": gameweeks[all_rounds[-1]]["team"],
            "minutes": sum(num(gameweeks[r]["minutes"]) for r in all_rounds),
        }
    return rates


def full_name(player: dict) -> str:
    return f"{player.get('first_name', '')} {player.get('second_name', '')}".strip()


def base_start_probability(player: dict, archive: dict[str, dict],
                           fallback_starts: float | None) -> tuple[float, str]:
    """Start probability before injuries and human judgement are applied.

    Prefers the recency-weighted archive rate; falls back to the FPL API's
    season-total starts when a player isn't in the archive (a newcomer or a
    promoted-club player); finally falls back to price, which is the only
    signal left for someone with no Premier League record at all.
    """
    entry = archive.get(full_name(player))
    if entry:
        return entry["blended"], (
            f"{entry['recent_rate']:.0%} of last 10 GWs started, "
            f"{entry['season_rate']:.0%} across the season"
        )

    if fallback_starts is not None and fallback_starts > 0:
        rate = min(fallback_starts / TOTAL_GAMEWEEKS, 1.0)
        return rate, f"{fallback_starts:.0f} starts last season (no gameweek detail)"

    cost = num(player.get("now_cost")) / 10
    rate = 0.75 if cost >= 6.0 else 0.55 if cost >= 5.0 else 0.35
    return rate, f"no Premier League record; inferred from £{cost:.1f}m price"


# ── Evaluation ─────────────────────────────────────────────────────────


def evaluate(pairs: list[tuple[str, str]], gameweeks: int = 5) -> None:
    """Re-run the comparison that justified this module's design.

    Kept in the code rather than written up in a commit message so the claims
    above can be checked, and re-checked when a new season lands.
    """
    print(f"{'season pair':<22}{'predictor':<24}{'corr':>7}{'MAE':>8}")
    print("-" * 61)
    for evidence_season, target_season in pairs:
        archive = start_rates(evidence_season)
        target = load_archive(target_season)
        if not archive or not target:
            print(f"{evidence_season} -> {target_season}: missing data, run gwdata.py")
            continue

        actual: dict[str, list[float]] = defaultdict(list)
        for row in target:
            if int(num(row["round"])) <= gameweeks:
                actual[row["name"]].append(num(row["starts"]))

        samples = []
        for name, starts in actual.items():
            entry = archive.get(name)
            if not entry or not starts:
                continue
            samples.append((statistics.mean(starts), entry["season_rate"], entry["blended"]))

        for label, index in (("season rate", 1), (f"{RECENT_WEIGHT:.0%} recency blend", 2)):
            observed = [s[0] for s in samples]
            predicted = [s[index] for s in samples]
            mean_o, mean_p = statistics.mean(observed), statistics.mean(predicted)
            covariance = sum((o - mean_o) * (p - mean_p) for o, p in zip(observed, predicted)) / len(samples)
            correlation = covariance / (statistics.pstdev(observed) * statistics.pstdev(predicted))
            error = statistics.mean(abs(o - p) for o, p in zip(observed, predicted))
            print(f"{evidence_season + ' -> ' + target_season:<22}{label:<24}{correlation:>7.3f}{error:>8.3f}")
        print(f"{'':<22}{f'({len(samples)} players)':<24}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluate", action="store_true", help="re-run the predictor comparison")
    parser.add_argument("--player", help="show how one player's start rate is derived")
    parser.add_argument("--season", default="2025-26", help="evidence season for --player")
    args = parser.parse_args()

    if args.evaluate:
        evaluate([("2023-24", "2024-25"), ("2024-25", "2025-26")])
    if args.player:
        rates = start_rates(args.season)
        matches = {n: r for n, r in rates.items() if args.player.lower() in n.lower()}
        if not matches:
            print(f"No player matching '{args.player}' in {args.season}")
        for name, entry in matches.items():
            print(f"\n{name} ({entry['team']}, {args.season})")
            print(f"  season start rate      {entry['season_rate']:.3f}")
            print(f"  last 10 GWs start rate {entry['recent_rate']:.3f}")
            print(f"  blended (used)         {entry['blended']:.3f}")
            print(f"  minutes                {entry['minutes']:.0f}")


if __name__ == "__main__":
    main()
