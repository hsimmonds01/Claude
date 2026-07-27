"""The honest test: how would the model's squad have done in GW1-5?

This is the situation the agent actually faces every August. No current-season
form exists, nothing has been played, and it has to commit £100m on the
strength of last season alone. Getting that right matters more than any
in-season decision, because the opening squad is carried for weeks.

Difference from backtest.py, which tests a whole season from FPL API totals:
  - the player pool is everyone who was in the game at the time (from the
    gameweek archive), not just players who are still in FPL today, so
    survivorship bias is gone
  - prices are the real GW1 prices from that season
  - the score is actual points in GW1-5, gameweek by gameweek

The squad and XI are chosen from projections only -- no hindsight -- and every
benchmark is chosen the same way, so nothing gets an unfair look at the future.

One caveat that cuts against the model: defensive contribution points did not
exist before 2025/26, so a test using 2024/25 as evidence is blind to DefCon
entirely. The live model has a full DefCon season to learn from and should do
better than this test suggests, particularly on defenders.

Usage:
  python backtest_gw.py                               2024-25 -> 2025-26 GW1-5
  python backtest_gw.py --gameweeks 10                first ten instead
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
from collections import defaultdict
from pathlib import Path

import pulp

from model import (
    ASSIST_POINTS, CLEAN_SHEET_POINTS, DEFCON_POINTS, DEFCON_THRESHOLD,
    GOAL_POINTS, SEASON_MATCHES, SHRINKAGE_MINUTES,
    clean_sheet_probability, defcon_probability, num, shrink,
)
from minutes import RECENT_WEIGHT, SEASON_WEIGHT, start_rates
from optimiser import BUDGET, MAX_PER_CLUB, SQUAD_SIZE, XI_LIMITS, XI_SIZE

BASE_DIR = Path(__file__).resolve().parent
GW_DIR = BASE_DIR / "data" / "history_gw"
NEUTRAL_DIFFICULTY = 3
POSITION_ID = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}

# The archive labels positions differently from the FPL API, and the labels
# aren't even stable between seasons: goalkeepers are "GK" here, and 2024/25
# carries an "AM" (attacking midfielder) class the API doesn't use. Left
# untranslated, "GK" simply vanishes from the pool and squad selection becomes
# infeasible with no obvious cause -- so normalise explicitly and loudly.
POSITION_ALIASES = {"GK": "GKP", "GKP": "GKP", "DEF": "DEF", "MID": "MID",
                    "FWD": "FWD", "AM": "MID"}


def normalise_position(raw: str) -> str | None:
    return POSITION_ALIASES.get((raw or "").strip().upper())


def load_season(season: str) -> list[dict]:
    path = GW_DIR / f"{season}.csv"
    if not path.exists():
        raise SystemExit(f"{path} not found -- run `python gwdata.py` first.")
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def aggregate(rows: list[dict]) -> dict[str, dict]:
    """Season totals per player, keyed by name (element ids are reassigned
    between seasons, so they can't be joined on)."""
    totals: dict[str, dict] = {}
    for row in rows:
        position = normalise_position(row["position"])
        entry = totals.setdefault(row["name"], {
            "name": row["name"], "position": position, "team": row["team"],
            "minutes": 0.0, "starts": 0.0, "total_points": 0.0, "bonus": 0.0,
            "expected_goals": 0.0, "expected_assists": 0.0, "defensive_contribution": 0.0,
        })
        for key in ("minutes", "starts", "total_points", "bonus",
                    "expected_goals", "expected_assists", "defensive_contribution"):
            entry[key] += num(row.get(key))
        entry["position"] = position or entry["position"]
    return totals


def positional_means(evidence: dict[str, dict]) -> dict[str, dict]:
    means: dict[str, dict] = {}
    for position in POSITION_ID:
        minutes = 0.0
        totals = defaultdict(float)
        for entry in evidence.values():
            if entry["position"] != position or entry["minutes"] < SHRINKAGE_MINUTES:
                continue
            minutes += entry["minutes"]
            for key in ("expected_goals", "expected_assists", "defensive_contribution", "bonus"):
                totals[key] += entry[key]
        per90 = lambda total: (total / minutes * 90) if minutes else 0.0  # noqa: E731
        means[position] = {
            "xg90": per90(totals["expected_goals"]), "xa90": per90(totals["expected_assists"]),
            "defcon90": per90(totals["defensive_contribution"]), "bonus90": per90(totals["bonus"]),
        }
    return means


def project_per_game(entry: dict, means: dict, position: str,
                     start_prob_override: float | None = None) -> float:
    """Projected points per gameweek from evidence-season data only."""
    minutes = entry["minutes"]
    if minutes <= 0:
        return 0.0
    mean = means[position]
    pos_id = POSITION_ID[position]

    xg90 = shrink(entry["expected_goals"] / minutes * 90, minutes, mean["xg90"])
    xa90 = shrink(entry["expected_assists"] / minutes * 90, minutes, mean["xa90"])
    defcon90 = shrink(entry["defensive_contribution"] / minutes * 90, minutes, mean["defcon90"])
    bonus90 = shrink(entry["bonus"] / minutes * 90, minutes, mean["bonus90"])
    start_prob = (start_prob_override if start_prob_override is not None
                  else min(entry["starts"] / SEASON_MATCHES, 1.0))

    per_start = 2.0 + xg90 * GOAL_POINTS[pos_id] + xa90 * ASSIST_POINTS + bonus90
    if CLEAN_SHEET_POINTS[pos_id]:
        home = clean_sheet_probability(NEUTRAL_DIFFICULTY, True)
        away = clean_sheet_probability(NEUTRAL_DIFFICULTY, False)
        per_start += (home + away) / 2 * CLEAN_SHEET_POINTS[pos_id]
    if pos_id in DEFCON_THRESHOLD:
        per_start += defcon_probability(defcon90, DEFCON_THRESHOLD[pos_id]) * DEFCON_POINTS
    return start_prob * per_start


def build_pool(target_rows: list[dict], evidence: dict, means: dict, gameweeks: int,
               recency: dict | None = None) -> list[dict]:
    """Everyone available at GW1 of the target season, with their real GW1
    price, an evidence-only projection, and what they actually scored."""
    first_gw = {r["name"]: r for r in target_rows if num(r["round"]) == 1}
    actual = defaultdict(float)
    for row in target_rows:
        if num(row["round"]) <= gameweeks:
            actual[row["name"]] += num(row["total_points"])

    pool = []
    for name, row in first_gw.items():
        position = normalise_position(row["position"])
        if position is None:
            continue
        price = num(row["value"]) / 10
        if price <= 0:
            continue
        entry = evidence.get(name)
        override = recency.get(name, {}).get("blended") if recency else None
        projected = (project_per_game(entry, means, position, override) * gameweeks
                     if entry else 0.0)
        pool.append({
            "id": name, "name": name, "team": row["team"], "position": position,
            "price": price, "projected": projected, "actual": actual.get(name, 0.0),
            "had_evidence": entry is not None,
        })
    return pool


def optimise(pool: list[dict], key: str) -> tuple[list[dict], list[dict]]:
    """Best legal 15 and the XI inside it, maximising `key`."""
    problem = pulp.LpProblem("gw_backtest", pulp.LpMaximize)
    picked = {p["id"]: pulp.LpVariable(f"p_{i}", cat="Binary") for i, p in enumerate(pool)}
    starting = {p["id"]: pulp.LpVariable(f"s_{i}", cat="Binary") for i, p in enumerate(pool)}

    problem += pulp.lpSum(starting[p["id"]] * p[key] for p in pool)
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
    squad = [p for p in pool if picked[p["id"]].value() > 0.5]
    xi = [p for p in pool if starting[p["id"]].value() > 0.5]
    return squad, xi


def random_xi_score(pool: list[dict], rng: random.Random) -> float | None:
    squad, spend, clubs = [], 0.0, defaultdict(int)
    for position, count in SQUAD_SIZE.items():
        members = [p for p in pool if p["position"] == position]
        rng.shuffle(members)
        taken = 0
        for player in members:
            if taken == count:
                break
            if clubs[player["team"]] >= MAX_PER_CLUB or spend + player["price"] > BUDGET:
                continue
            squad.append(player)
            spend += player["price"]
            clubs[player["team"]] += 1
            taken += 1
        if taken < count:
            return None
    # A random manager has no projections, so the XI is drawn at random too --
    # in a legal formation.
    keeper = next(p for p in squad if p["position"] == "GKP")
    outfield = [p for p in squad if p["position"] != "GKP"]
    rng.shuffle(outfield)
    for defenders in (3, 4, 5):
        for forwards in (1, 2, 3):
            midfielders = XI_SIZE - 1 - defenders - forwards
            if not (XI_LIMITS["MID"][0] <= midfielders <= XI_LIMITS["MID"][1]):
                continue
            xi = [keeper]
            ok = True
            for position, count in (("DEF", defenders), ("MID", midfielders), ("FWD", forwards)):
                members = [p for p in outfield if p["position"] == position]
                if len(members) < count:
                    ok = False
                    break
                xi += members[:count]
            if ok:
                return sum(p["actual"] for p in xi)
    return None


def run(evidence_season: str, target_season: str, gameweeks: int, samples: int,
        use_recency: bool = True) -> None:
    evidence = aggregate(load_season(evidence_season))
    target_rows = load_season(target_season)
    means = positional_means(evidence)
    recency = start_rates(evidence_season) if use_recency else None
    pool = build_pool(target_rows, evidence, means, gameweeks, recency)

    with_evidence = sum(1 for p in pool if p["had_evidence"])
    print(f"=== GW1-{gameweeks} backtest: {evidence_season} evidence -> {target_season} results ===")
    print(f"pool: {len(pool)} players available at GW1 "
          f"({with_evidence} with {evidence_season} data, "
          f"{len(pool) - with_evidence} newcomers with none)")
    defcon_seen = sum(e["defensive_contribution"] for e in evidence.values())
    if defcon_seen == 0:
        print(f"note: {evidence_season} predates defensive contribution points, so the model "
              f"is blind to DefCon here -- it should do better with a DefCon season to learn from.\n")

    model_squad, model_xi = optimise(pool, "projected")
    _, hindsight_xi = optimise(pool, "actual")
    price_pool = [{**p, "price_key": p["price"]} for p in pool]
    _, price_xi = optimise(price_pool, "price_key")

    model_score = sum(p["actual"] for p in model_xi)
    hindsight_score = sum(p["actual"] for p in hindsight_xi)
    price_score = sum(p["actual"] for p in price_xi)

    rng = random.Random(20260727)
    random_scores = [s for s in (random_xi_score(pool, rng) for _ in range(samples)) if s is not None]
    random_mean = statistics.mean(random_scores) if random_scores else 0.0

    print(f"{'strategy':<26}{'GW1-' + str(gameweeks) + ' XI pts':>16}")
    print("-" * 42)
    for label, score in sorted(
        [("MODEL", model_score), ("hindsight ceiling", hindsight_score),
         ("by price (market view)", price_score),
         (f"random (avg of {len(random_scores)})", random_mean)],
        key=lambda r: -r[1],
    ):
        print(f"{label:<26}{score:>16.0f}")

    if random_mean:
        print(f"\nmodel vs random             {model_score - random_mean:+.0f} pts "
              f"({(model_score / random_mean - 1) * 100:+.1f}%)")
        beaten = sum(1 for s in random_scores if s < model_score) / len(random_scores)
        print(f"percentile vs random squads {beaten * 100:.1f}%")
    print(f"model vs market             {model_score - price_score:+.0f} pts")
    print(f"share of hindsight ceiling  {model_score / hindsight_score * 100:.1f}%")

    print(f"\n--- the XI the model picked (prices as at GW1 {target_season}) ---")
    for player in sorted(model_xi, key=lambda p: (p["position"], -p["actual"])):
        gap = player["actual"] - player["projected"]
        print(f"  {player['name'][:22]:<23}{player['team'][:11]:<12}{player['position']:<5}"
              f"£{player['price']:>5.1f}m  projected {player['projected']:>5.1f}  "
              f"actual {player['actual']:>4.0f}  ({gap:+.0f})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default="2024-25")
    parser.add_argument("--target", default="2025-26")
    parser.add_argument("--gameweeks", type=int, default=5)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--no-recency", action="store_true",
                        help="use the old season-average minutes model, for comparison")
    args = parser.parse_args()
    run(args.evidence, args.target, args.gameweeks, args.samples, use_recency=not args.no_recency)


if __name__ == "__main__":
    main()
