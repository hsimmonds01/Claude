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

  In-season updating. Everything above blends TWO PAST seasons -- last
  year's recent form against last year's whole season -- but says nothing
  about what happens once the new season itself is under way. Before this
  was added, a returning player's start probability was frozen on last
  season's archive for the entire new season, with no way to notice that
  he's started every game so far. Found 3 Sep 2026 when a manager pushed
  back hard on the model wanting to sell Calafiori, who started 56% of his
  last 10 games in 2025/26 (hence the low probability) but had started 2/2
  so far in 2026/27 -- real, current evidence the model had access to
  (`players.csv`'s own live `starts` field, refreshed every run) and simply
  never looked at.

  Tested by walking forward inside a season rather than across seasons: use
  each player's actual starts in that season's first few gameweeks as
  additional evidence on top of the prior-season archive, and see whether
  it predicts starts in the gameweeks after that better than the archive
  alone. It does, sharply, with only 2 games of in-season evidence already
  available (our actual situation the day this was found) and gets better
  with more:

      evidence used              pair                  corr           MAE
      archive only                2023-24 -> 2024-25    0.677         0.197
      + first 2 GWs this season   2023-24 -> 2024-25    0.766         0.169
      archive only                2024-25 -> 2025-26    0.655         0.195
      + first 2 GWs this season   2024-25 -> 2025-26    0.778         0.156
      archive only                2023-24 -> 2024-25    0.664         0.202
      + first 5 GWs this season   2023-24 -> 2024-25    0.826         0.146
      archive only                2024-25 -> 2025-26    0.636         0.204
      + first 5 GWs this season   2024-25 -> 2025-26    0.818         0.138

  The blend weight (how many games the archive prior is worth against real
  in-season starts) was swept from 1 to 30 at three different amounts of
  in-season evidence (2, 3 and 5 games); 2 games' worth of prior weight won
  or tied for best in every single case, consistently across both season
  pairs. `PRIOR_GAMES = 2` in `base_start_probability` is that constant --
  in effect, two of a player's OWN actual starts this season outweigh a
  whole season of someone else's archived pattern. Re-run with
  `minutes.py --evaluate-in-season`.

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


# How many games the prior (whichever tier produced it) is worth against a
# player's OWN actual starts in the season under way. Swept from 1 to 30
# against real forward-walking data -- see the module docstring. 2 games'
# worth of prior weight won or tied for best at every amount of in-season
# evidence tested, meaning two of a player's own starts this season already
# outweigh a whole season of someone else's archived pattern.
PRIOR_GAMES = 2.0


def base_start_probability(player: dict, archive: dict[str, dict],
                           fallback_starts: float | None,
                           current_starts: float | None = None,
                           games_played: int | None = None) -> tuple[float, str]:
    """Start probability before injuries and human judgement are applied.

    Prefers the recency-weighted archive rate; falls back to the FPL API's
    season-total starts when a player isn't in the archive (a newcomer or a
    promoted-club player); finally falls back to price, which is the only
    signal left for someone with no Premier League record at all.

    Once games_played is given (the season under way has actually kicked
    off), that prior is then blended with the player's own starts THIS
    season via PRIOR_GAMES -- see the module docstring for why archived
    history alone, however recent, stops being enough the moment real
    current-season evidence exists to check it against.
    """
    entry = archive.get(full_name(player))
    if entry:
        rate, reason = entry["blended"], (
            f"{entry['recent_rate']:.0%} of last 10 GWs started, "
            f"{entry['season_rate']:.0%} across the season"
        )
    elif fallback_starts is not None and fallback_starts > 0:
        rate = min(fallback_starts / TOTAL_GAMEWEEKS, 1.0)
        reason = f"{fallback_starts:.0f} starts last season (no gameweek detail)"
    else:
        cost = num(player.get("now_cost")) / 10
        rate = 0.75 if cost >= 6.0 else 0.55 if cost >= 5.0 else 0.35
        reason = f"no Premier League record; inferred from £{cost:.1f}m price"

    if games_played and current_starts is not None:
        blended = (rate * PRIOR_GAMES + current_starts) / (PRIOR_GAMES + games_played)
        return blended, (
            f"{current_starts:.0f}/{games_played:.0f} starts this season, "
            f"blended with last year's evidence ({reason})"
        )
    return rate, reason


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


def _correlation(observed: list[float], predicted: list[float]) -> float:
    mean_o, mean_p = statistics.mean(observed), statistics.mean(predicted)
    covariance = sum((o - mean_o) * (p - mean_p) for o, p in zip(observed, predicted)) / len(observed)
    sd_o, sd_p = statistics.pstdev(observed), statistics.pstdev(predicted)
    return covariance / (sd_o * sd_p) if sd_o and sd_p else float("nan")


def evaluate_in_season(pairs: list[tuple[str, str]], early_gws: int = 5,
                       later_gws: tuple[int, int] = (6, 15),
                       prior_games_options=(1, 2, 3, 5, 8, 12, 20)) -> None:
    """Walking forward INSIDE a season, rather than across two of them.

    evaluate() tests the archive alone: does last season's pattern predict
    this season's opening gameweeks. This tests the thing PRIOR_GAMES
    actually claims -- once a handful of this season's OWN games exist, does
    blending them into the archive prior predict the games after that better
    than the archive alone. See the module docstring for the numbers this
    produced and why PRIOR_GAMES=2.
    """
    print(f"blending in the target season's own first {early_gws} GWs, "
          f"predicting GW{later_gws[0]}-{later_gws[1]}\n")
    for evidence_season, target_season in pairs:
        archive = start_rates(evidence_season)
        target = load_archive(target_season)
        if not archive or not target:
            print(f"{evidence_season} -> {target_season}: missing data, run gwdata.py")
            continue

        by_player: dict[str, dict[int, float]] = defaultdict(dict)
        for row in target:
            by_player[row["name"]][int(num(row["round"]))] = num(row["starts"])

        samples = []
        for name, rounds in by_player.items():
            entry = archive.get(name)
            if not entry:
                continue
            later_vals = [rounds[r] for r in rounds if later_gws[0] <= r <= later_gws[1]]
            early_vals = [rounds[r] for r in rounds if 1 <= r <= early_gws]
            if not later_vals:
                continue
            samples.append((statistics.mean(later_vals), entry["blended"], sum(early_vals), len(early_vals)))

        observed = [s[0] for s in samples]
        print(f"{evidence_season} -> {target_season} ({len(samples)} players)")
        archive_only = [s[1] for s in samples]
        print(f"  {'archive only':<28}corr={_correlation(observed, archive_only):.3f}  "
              f"MAE={statistics.mean(abs(o - p) for o, p in zip(observed, archive_only)):.3f}")
        for prior_games in prior_games_options:
            blended = [(s[1] * prior_games + s[2]) / (prior_games + s[3]) if s[3] else s[1] for s in samples]
            print(f"  {'blend, prior_games=' + str(prior_games):<28}"
                  f"corr={_correlation(observed, blended):.3f}  "
                  f"MAE={statistics.mean(abs(o - p) for o, p in zip(observed, blended)):.3f}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluate", action="store_true", help="re-run the predictor comparison")
    parser.add_argument("--evaluate-in-season", action="store_true",
                        help="test blending in the target season's own early gameweeks")
    parser.add_argument("--player", help="show how one player's start rate is derived")
    parser.add_argument("--season", default="2025-26", help="evidence season for --player")
    args = parser.parse_args()

    if args.evaluate:
        evaluate([("2023-24", "2024-25"), ("2024-25", "2025-26")])
    if args.evaluate_in_season:
        evaluate_in_season([("2023-24", "2024-25"), ("2024-25", "2025-26")])
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

            current = next(
                (p for p in csv.DictReader((DATA_DIR / "players.csv").open(encoding="utf-8"))
                 if full_name(p) == name), None) if (DATA_DIR / "players.csv").exists() else None
            events_path = DATA_DIR / "events.csv"
            games_played = (sum(1 for e in csv.DictReader(events_path.open(encoding="utf-8"))
                                 if e["finished"] == "True") if events_path.exists() else 0)
            if current and games_played:
                live_rate, live_reason = base_start_probability(
                    current, {name: entry}, None,
                    current_starts=num(current["starts"]), games_played=games_played)
                print(f"  live blended (used now) {live_rate:.3f}  -- {live_reason}")


if __name__ == "__main__":
    main()
