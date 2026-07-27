"""Does the model actually work?

Builds a squad using only information that existed before a season started,
then scores it on what really happened. If the model can't beat a naive
benchmark here, it has no business sending transfer advice.

The test:
  1. Evidence: one season's per-player data (default 2024/25) and nothing else.
  2. Prices: the *starting* prices of the target season, which the FPL API
     keeps in history_past as start_cost -- so the squad is buildable exactly
     as it would have been on day one.
  3. Build the best legal 15 under £100m.
  4. Score it against what those players actually scored in the target season.
  5. Compare against benchmarks, because a big number means nothing on its own.

Benchmarks:
  random        average of many randomly-built legal squads -- the "no skill"
                floor
  by price      the most expensive legal squad the budget allows, i.e. what
                the market thought before a ball was kicked
  hindsight     the best squad that could possibly have been picked, knowing
                every result in advance -- the ceiling nobody reaches

Known limits, stated up front rather than buried:

  SURVIVORSHIP BIAS. The player pool is whoever is in FPL *today*. Anyone who
  left the Premier League after the target season is invisible, and leavers
  are worse on average than stayers. This flatters every strategy tested,
  including the model's, so the comparison between them stays meaningful even
  though the absolute totals are optimistic. The run reports how many players
  the pool is missing.

  POSITIONS AND CLUBS ARE CURRENT. A player reclassified since the target
  season is tested in today's position, and club moves are ignored entirely
  (the move discount would otherwise use next-season information, which is
  precisely the leak this test exists to avoid).

  SEASON TOTALS, NOT GAMEWEEKS. history_past holds only per-season figures,
  so this measures a whole season. The harder and more honest test -- the
  first five gameweeks, when the model has no current-season form at all --
  needs per-gameweek history. See --gameweeks.

Usage:
  python backtest.py                       2024/25 evidence -> 2025/26 result
  python backtest.py --evidence 2023/24 --target 2024/25
  python backtest.py --samples 2000        more random squads for the floor
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections import defaultdict

import pulp

from model import (
    ASSIST_POINTS, CLEAN_SHEET_POINTS, DEFCON_POINTS, DEFCON_THRESHOLD,
    GOAL_POINTS, POSITIONS, SEASON_MATCHES, SHRINKAGE_MINUTES,
    clean_sheet_probability, defcon_probability, num, read_csv, shrink,
)
from optimiser import BUDGET, MAX_PER_CLUB, SQUAD_SIZE, XI_LIMITS, XI_SIZE

# A neutral fixture: average difficulty, half the games at home. The point is
# to rank players fairly, not to model a specific schedule.
NEUTRAL_DIFFICULTY = 3


def season_rows(history: list[dict], season: str) -> dict[str, dict]:
    return {r["player_id"]: r for r in history if r["season_name"] == season}


def positional_means_for(players: list[dict], rows: dict[str, dict]) -> dict[int, dict]:
    means: dict[int, dict] = {}
    for position in POSITIONS:
        totals = defaultdict(float)
        minutes = 0.0
        for player in players:
            if int(player["element_type"]) != position:
                continue
            row = rows.get(player["id"])
            if not row or num(row["minutes"]) < SHRINKAGE_MINUTES:
                continue
            minutes += num(row["minutes"])
            for key in ("expected_goals", "expected_assists", "defensive_contribution", "bonus"):
                totals[key] += num(row[key])
        per90 = lambda total: (total / minutes * 90) if minutes else 0.0  # noqa: E731
        means[position] = {
            "xg90": per90(totals["expected_goals"]), "xa90": per90(totals["expected_assists"]),
            "defcon90": per90(totals["defensive_contribution"]), "bonus90": per90(totals["bonus"]),
        }
    return means


def project_season(player: dict, row: dict, means: dict) -> float:
    """Projected points for a full season, from evidence only.

    Deliberately does NOT apply the club-move discount or overrides.json:
    both describe today's world, and using them here would leak information
    the model could not have had at the time.
    """
    position = int(player["element_type"])
    minutes = num(row["minutes"])
    mean = means[position]
    if minutes <= 0:
        return 0.0

    xg90 = shrink(num(row["expected_goals"]) / minutes * 90, minutes, mean["xg90"])
    xa90 = shrink(num(row["expected_assists"]) / minutes * 90, minutes, mean["xa90"])
    defcon90 = shrink(num(row["defensive_contribution"]) / minutes * 90, minutes, mean["defcon90"])
    bonus90 = shrink(num(row["bonus"]) / minutes * 90, minutes, mean["bonus90"])
    start_prob = min(num(row["starts"]) / SEASON_MATCHES, 1.0)

    per_start = 2.0 + xg90 * GOAL_POINTS[position] + xa90 * ASSIST_POINTS + bonus90
    if CLEAN_SHEET_POINTS[position]:
        home = clean_sheet_probability(NEUTRAL_DIFFICULTY, True)
        away = clean_sheet_probability(NEUTRAL_DIFFICULTY, False)
        per_start += (home + away) / 2 * CLEAN_SHEET_POINTS[position]
    if position in DEFCON_THRESHOLD:
        per_start += defcon_probability(defcon90, DEFCON_THRESHOLD[position]) * DEFCON_POINTS

    return start_prob * per_start * SEASON_MATCHES


def optimise(pool: list[dict], value_key: str) -> list[dict]:
    """Best legal 15 maximising `value_key`, with a valid XI inside it."""
    problem = pulp.LpProblem("backtest_squad", pulp.LpMaximize)
    picked = {p["id"]: pulp.LpVariable(f"p_{p['id']}", cat="Binary") for p in pool}
    starting = {p["id"]: pulp.LpVariable(f"s_{p['id']}", cat="Binary") for p in pool}

    problem += pulp.lpSum(starting[p["id"]] * p[value_key] for p in pool)
    problem += pulp.lpSum(picked.values()) == 15
    problem += pulp.lpSum(starting.values()) == XI_SIZE
    problem += pulp.lpSum(picked[p["id"]] * p["price"] for p in pool) <= BUDGET

    for position, count in SQUAD_SIZE.items():
        members = [p for p in pool if p["position"] == position]
        problem += pulp.lpSum(picked[p["id"]] for p in members) == count
        low, high = XI_LIMITS[position]
        problem += pulp.lpSum(starting[p["id"]] for p in members) >= low
        problem += pulp.lpSum(starting[p["id"]] for p in members) <= high
    for team in {p["team"] for p in pool}:
        members = [p for p in pool if p["team"] == team]
        problem += pulp.lpSum(picked[p["id"]] for p in members) <= MAX_PER_CLUB
    for p in pool:
        problem += starting[p["id"]] <= picked[p["id"]]

    problem.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[problem.status] != "Optimal":
        raise SystemExit(f"Solver failed: {pulp.LpStatus[problem.status]}")
    return [p for p in pool if picked[p["id"]].value() > 0.5]


def best_xi_actual(squad: list[dict]) -> float:
    """Actual points of the best legal XI in hindsight.

    Every strategy is scored this way, so none is advantaged: it isolates
    squad quality from in-season team selection, which this test isn't
    measuring.
    """
    best = 0.0
    keepers = sorted((p for p in squad if p["position"] == "GKP"), key=lambda p: -p["actual"])
    for defenders in range(*(XI_LIMITS["DEF"][0], XI_LIMITS["DEF"][1] + 1)):
        for forwards in range(*(XI_LIMITS["FWD"][0], XI_LIMITS["FWD"][1] + 1)):
            midfielders = XI_SIZE - 1 - defenders - forwards
            if not (XI_LIMITS["MID"][0] <= midfielders <= XI_LIMITS["MID"][1]):
                continue
            total = keepers[0]["actual"] if keepers else 0.0
            ok = True
            for position, count in (("DEF", defenders), ("MID", midfielders), ("FWD", forwards)):
                members = sorted((p for p in squad if p["position"] == position),
                                 key=lambda p: -p["actual"])
                if len(members) < count:
                    ok = False
                    break
                total += sum(p["actual"] for p in members[:count])
            if ok:
                best = max(best, total)
    return best


def random_squad(pool: list[dict], rng: random.Random) -> list[dict] | None:
    """One randomly-assembled legal squad, or None if the draw doesn't fit."""
    squad, spend, clubs = [], 0.0, defaultdict(int)
    for position, count in SQUAD_SIZE.items():
        members = [p for p in pool if p["position"] == position]
        rng.shuffle(members)
        taken = 0
        for player in members:
            if taken == count:
                break
            if clubs[player["team"]] >= MAX_PER_CLUB:
                continue
            if spend + player["price"] > BUDGET:
                continue
            squad.append(player)
            spend += player["price"]
            clubs[player["team"]] += 1
            taken += 1
        if taken < count:
            return None
    return squad


def run(evidence: str, target: str, samples: int) -> None:
    players = read_csv("players.csv")
    history = read_csv("player_history_past.csv")
    teams = {t["id"]: t["short_name"] for t in read_csv("teams.csv")}

    evidence_rows = season_rows(history, evidence)
    target_rows = season_rows(history, target)
    if not evidence_rows or not target_rows:
        raise SystemExit(f"Need data for both {evidence} and {target}. "
                         f"Seasons available: {sorted({r['season_name'] for r in history})}")

    means = positional_means_for(players, evidence_rows)

    pool = []
    for player in players:
        evidence_row = evidence_rows.get(player["id"])
        target_row = target_rows.get(player["id"])
        # Must have played in the evidence season (something to learn from)
        # and in the target season (something to be scored against).
        if not evidence_row or not target_row:
            continue
        price = num(target_row["start_cost"]) / 10
        if price <= 0:
            continue
        pool.append({
            "id": player["id"], "name": player["web_name"],
            "team": teams.get(player["team"], "?"),
            "position": POSITIONS[int(player["element_type"])],
            "price": price,
            "projected": project_season(player, evidence_row, means),
            "actual": num(target_row["total_points"]),
        })

    print(f"=== Backtest: evidence {evidence} -> scored on {target} ===")
    print(f"pool: {len(pool)} players with data in both seasons "
          f"(of {len(target_rows)} who played in {target}, and {len(players)} in FPL today)")
    missing = len(target_rows) - len(pool)
    print(f"survivorship gap: {missing} players who played in {target} are not testable "
          f"(left the league, or no {evidence} data) -- absolute scores are therefore optimistic\n")

    results = []

    model_squad = optimise(pool, "projected")
    results.append(("MODEL", best_xi_actual(model_squad), model_squad))

    hindsight_squad = optimise(pool, "actual")
    results.append(("hindsight ceiling", best_xi_actual(hindsight_squad), hindsight_squad))

    price_pool = [{**p, "price_value": p["price"]} for p in pool]
    price_squad = optimise(price_pool, "price_value")
    results.append(("by price (market view)", best_xi_actual(price_squad), price_squad))

    rng = random.Random(20260727)
    random_scores = []
    for _ in range(samples):
        squad = random_squad(pool, rng)
        if squad:
            random_scores.append(best_xi_actual(squad))
    random_mean = statistics.mean(random_scores) if random_scores else 0.0

    print(f"{'strategy':<24}{'XI actual pts':>14}")
    print("-" * 38)
    for label, score, _ in sorted(results, key=lambda r: -r[1]):
        print(f"{label:<24}{score:>14.0f}")
    print(f"{'random (avg of ' + str(len(random_scores)) + ')':<24}{random_mean:>14.0f}")

    model_score = next(s for label, s, _ in results if label == "MODEL")
    ceiling = next(s for label, s, _ in results if label == "hindsight ceiling")
    market = next(s for label, s, _ in results if label == "by price (market view)")

    print(f"\nmodel vs random          {model_score - random_mean:+.0f} pts "
          f"({(model_score / random_mean - 1) * 100:+.1f}%)" if random_mean else "")
    print(f"model vs market          {model_score - market:+.0f} pts")
    print(f"share of hindsight ceiling  {model_score / ceiling * 100:.1f}%")
    if random_scores:
        beaten = sum(1 for s in random_scores if s < model_score) / len(random_scores)
        print(f"percentile vs random squads {beaten * 100:.1f}%")

    print(f"\n--- the squad the model picked (prices as at start of {target}) ---")
    for player in sorted(model_squad, key=lambda p: (p["position"], -p["actual"])):
        print(f"  {player['name']:<16}{player['team']:<5}{player['position']:<5}"
              f"£{player['price']:>5.1f}m  projected {player['projected']:>6.0f}  actual {player['actual']:>4.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default="2024/25", help="season the model may learn from")
    parser.add_argument("--target", default="2025/26", help="season it is scored against")
    parser.add_argument("--samples", type=int, default=1000, help="random squads for the no-skill floor")
    args = parser.parse_args()
    run(args.evidence, args.target, args.samples)


if __name__ == "__main__":
    main()
