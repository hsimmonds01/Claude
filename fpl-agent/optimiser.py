"""Turn projections into decisions.

model.py says what each player is worth. This says what to actually do about
it, under the rules that make FPL hard: £100m budget, 2/5/5/3 by position, at
most 3 players from any one club, a valid formation, and a limited number of
free transfers.

Three jobs:

  --build      the best legal 15 from scratch. This is what a Wildcard or an
               opening-day squad needs. Solved exactly with integer
               programming rather than picked greedily, because greedy
               selection reliably strands money in the wrong places.

  --squad      given the squad in my_squad.json, the transfers worth making.
               Every option is compared against DOING NOTHING, which is the
               benchmark most FPL advice quietly skips -- a transfer has to
               beat rolling the free transfer, not merely be an upgrade.

  --team       the best starting XI, bench order and captain from a squad.

A hit (-4 points) is only recommended when the projected gain over the
horizon clears it with room to spare, because a projection is not precise
enough to justify a 4-point certainty for a 4.1-point estimate.

Usage:
  python optimiser.py --build
  python optimiser.py --squad
  python optimiser.py --team
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import pulp

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SQUAD_PATH = BASE_DIR / "my_squad.json"
OVERRIDES_PATH = BASE_DIR / "overrides.json"
STRATEGY_PATH = BASE_DIR / "strategy.md"

BUDGET = 100.0
SQUAD_SIZE = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
# Valid formations: exactly 1 keeper, then 3-5 defenders, 2-5 midfielders,
# 1-3 forwards, eleven players in total.
XI_LIMITS = {"GKP": (1, 1), "DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}
XI_SIZE = 11

HIT_COST = 4.0
# A hit needs to clear its cost by this much before it's worth recommending.
# Projections aren't precise enough to trade a certain -4 for a marginal gain.
HIT_MARGIN = 2.0
# Bench players only score when someone ahead of them doesn't play, so they're
# worth a fraction of their projection when valuing a squad.
BENCH_WEIGHT = 0.12

# Ownership above this counts as "template": the players whose absence from
# your squad is a rank bet rather than a neutral choice.
TEMPLATE_OWNERSHIP = 20.0


def load_strategy() -> dict:
    """Read the `key: value` lines out of strategy.md.

    Settings live in prose rather than JSON because the same file is the
    email writer's brief -- keeping them together means the numbers and the
    reasoning behind them can't drift apart.
    """
    settings = {"risk": 0.5, "max_hit": 4.0, "horizon": 5.0}
    if not STRATEGY_PATH.exists():
        return settings
    for line in STRATEGY_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if ":" not in stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        if key in settings:
            try:
                settings[key] = float(value.strip())
            except ValueError:
                continue
    return settings


def template_value(player: dict, risk: float) -> float:
    """A player's worth once rank risk is priced in.

    Raw expected points answer "how many points will this score". FPL asks a
    different question: "how will this move me against everyone else". A
    75%-owned player who hauls gains you nothing if you own him -- but costs
    you dearly if you don't. So his effective value carries a premium in
    proportion to how many rivals hold him.

    This is a risk preference, not an accuracy improvement, and it will lower
    projected points on purpose. risk=0 disables it entirely.
    """
    return player["total"] * (1 + risk * player["selected_by"] / 100)


def load_projections() -> tuple[dict, list[int]]:
    """player_id -> aggregate projection over the horizon, plus per-gameweek."""
    path = DATA_DIR / "projections.csv"
    if not path.exists():
        raise SystemExit("data/projections.csv not found -- run `python model.py` first.")
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    players: dict[str, dict] = {}
    events = sorted({int(r["event"]) for r in rows})
    for row in rows:
        entry = players.setdefault(row["player_id"], {
            "player_id": row["player_id"], "name": row["web_name"], "team": row["team"],
            "position": row["position"], "price": float(row["price"]),
            "selected_by": float(row["selected_by"]), "by_event": defaultdict(float),
            "total": 0.0, "start_prob": float(row["start_prob"]),
            "moved_club": bool(row["moved_club"]), "minutes_reason": row["minutes_reason"],
        })
        # Doubles: a team playing twice in a gameweek accumulates both.
        entry["by_event"][int(row["event"])] += float(row["xp"])
        entry["total"] += float(row["xp"])
    return players, events


def load_overrides() -> dict:
    if not OVERRIDES_PATH.exists():
        return {}
    return {k: v for k, v in json.loads(OVERRIDES_PATH.read_text(encoding="utf-8")).items()
            if not k.startswith("_")}


def resolve_squad(players: dict) -> tuple[list[dict], dict]:
    """Match my_squad.json's names to real players, failing loudly on any that
    can't be resolved -- a silently dropped player would corrupt every number
    downstream."""
    config = json.loads(SQUAD_PATH.read_text(encoding="utf-8"))
    by_name = defaultdict(list)
    for player in players.values():
        by_name[player["name"]].append(player)

    squad, missing = [], []
    for entry in config["squad"]:
        candidates = by_name.get(entry["name"], [])
        if entry.get("team"):
            candidates = [c for c in candidates if c["team"] == entry["team"]] or candidates
        if len(candidates) != 1:
            missing.append(f"{entry['name']} ({entry.get('team', '?')}) -> {len(candidates)} matches")
            continue
        squad.append(candidates[0])
    if missing:
        raise SystemExit("Could not resolve these squad entries:\n  " + "\n  ".join(missing))
    return squad, config


# ── Squad construction ─────────────────────────────────────────────────


def build_squad(players: dict, budget: float = BUDGET, banned: set[str] | None = None,
                locked: set[str] | None = None, risk: float = 0.0) -> list[dict]:
    """Best legal 15 by integer programming.

    Bench players are weighted down rather than ignored: a squad valued purely
    on its best eleven will spend nothing on the bench and then bleed points
    the first time someone is injured.
    """
    banned, locked = banned or set(), locked or set()
    pool = [p for p in players.values() if p["player_id"] not in banned]

    problem = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    picked = {p["player_id"]: pulp.LpVariable(f"pick_{p['player_id']}", cat="Binary") for p in pool}
    starting = {p["player_id"]: pulp.LpVariable(f"start_{p['player_id']}", cat="Binary") for p in pool}

    # The captain's score is doubled, so a squad's value isn't just the sum of
    # its projections -- it includes the extra copy of whoever wears the
    # armband. Leaving this out systematically undervalues premium players:
    # without it the solver drops Haaland and spreads the money, because it
    # never sees the second helping his projection earns every week.
    captain = {p["player_id"]: pulp.LpVariable(f"capt_{p['player_id']}", cat="Binary") for p in pool}

    value = {p["player_id"]: template_value(p, risk) for p in pool}
    problem += pulp.lpSum(
        starting[p["player_id"]] * value[p["player_id"]]
        + captain[p["player_id"]] * value[p["player_id"]]
        + (picked[p["player_id"]] - starting[p["player_id"]]) * value[p["player_id"]] * BENCH_WEIGHT
        for p in pool
    )

    problem += pulp.lpSum(captain.values()) == 1
    for p in pool:
        problem += captain[p["player_id"]] <= starting[p["player_id"]]

    problem += pulp.lpSum(picked.values()) == 15
    problem += pulp.lpSum(starting.values()) == XI_SIZE
    problem += pulp.lpSum(picked[p["player_id"]] * p["price"] for p in pool) <= budget

    for position, count in SQUAD_SIZE.items():
        members = [p for p in pool if p["position"] == position]
        problem += pulp.lpSum(picked[p["player_id"]] for p in members) == count
        low, high = XI_LIMITS[position]
        problem += pulp.lpSum(starting[p["player_id"]] for p in members) >= low
        problem += pulp.lpSum(starting[p["player_id"]] for p in members) <= high

    for team in {p["team"] for p in pool}:
        members = [p for p in pool if p["team"] == team]
        problem += pulp.lpSum(picked[p["player_id"]] for p in members) <= MAX_PER_CLUB

    for p in pool:
        problem += starting[p["player_id"]] <= picked[p["player_id"]]
    for player_id in locked:
        if player_id in picked:
            problem += picked[player_id] == 1

    problem.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[problem.status] != "Optimal":
        raise SystemExit(f"No optimal squad found (solver said: {pulp.LpStatus[problem.status]})")
    return [p for p in pool if picked[p["player_id"]].value() > 0.5]


def pick_team(squad: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Best XI, bench in the order they should come on, and the captain."""
    best, best_score = None, -1.0
    for keeper in [p for p in squad if p["position"] == "GKP"]:
        outfield = sorted((p for p in squad if p["position"] != "GKP"),
                          key=lambda p: -p["total"])
        for defenders in range(XI_LIMITS["DEF"][0], XI_LIMITS["DEF"][1] + 1):
            for forwards in range(XI_LIMITS["FWD"][0], XI_LIMITS["FWD"][1] + 1):
                midfielders = XI_SIZE - 1 - defenders - forwards
                if not (XI_LIMITS["MID"][0] <= midfielders <= XI_LIMITS["MID"][1]):
                    continue
                pick = []
                for position, count in (("DEF", defenders), ("MID", midfielders), ("FWD", forwards)):
                    available = [p for p in outfield if p["position"] == position]
                    if len(available) < count:
                        break
                    pick += available[:count]
                else:
                    score = keeper["total"] + sum(p["total"] for p in pick)
                    if score > best_score:
                        best, best_score = [keeper] + pick, score

    starters = sorted(best, key=lambda p: (p["position"] != "GKP", -p["total"]))
    bench = sorted((p for p in squad if p not in best), key=lambda p: -p["total"])
    # The reserve keeper can only ever replace the keeper, so he sits last
    # regardless of projection.
    bench = [p for p in bench if p["position"] != "GKP"] + [p for p in bench if p["position"] == "GKP"]
    captain = max(starters, key=lambda p: p["total"])
    return starters, bench, captain


# ── Transfers ──────────────────────────────────────────────────────────


def squad_value(squad: list[dict]) -> float:
    """What a squad is actually worth: its starting XI, plus a discounted
    bench.

    Valuing a transfer by the two players' raw projections is wrong, and
    wrong in a way that produces confidently bad advice -- it rates
    "upgrade your reserve goalkeeper" as a large gain, when a reserve
    keeper's points almost never reach your total. Only the effect on the
    eleven that actually play counts.
    """
    starters, bench, _ = pick_team(squad)
    return sum(p["total"] for p in starters) + BENCH_WEIGHT * sum(p["total"] for p in bench)


def suggest_transfers(squad: list[dict], players: dict, bank: float,
                      free_transfers: int, overrides: dict, limit: int = 5) -> list[dict]:
    """Every single-transfer option, ranked against doing nothing.

    Each option is scored by how much it moves the whole squad's value (XI
    plus discounted bench), not by the difference between the two players.

    Selling price is treated as the current price. That's right pre-season and
    a small simplification later, when a risen player sells for the purchase
    price plus half the gain -- squad.py will supply real selling prices once
    it reconstructs the team from the API.
    """
    baseline = squad_value(squad)
    squad_ids = {p["player_id"] for p in squad}
    club_counts = defaultdict(int)
    for player in squad:
        club_counts[player["team"]] += 1

    avoid = {name for name, rule in overrides.items() if rule.get("avoid")}
    options = []

    for out_player in squad:
        budget = bank + out_player["price"]
        for candidate in players.values():
            if candidate["player_id"] in squad_ids:
                continue
            if candidate["position"] != out_player["position"]:
                continue
            if candidate["price"] > budget + 1e-9:
                continue
            if candidate["name"] in avoid:
                continue
            # The 3-per-club limit, accounting for the outgoing player.
            projected_count = club_counts[candidate["team"]] - (1 if candidate["team"] == out_player["team"] else 0)
            if projected_count >= MAX_PER_CLUB:
                continue
            trial = [candidate if p is out_player else p for p in squad]
            gain = squad_value(trial) - baseline
            cost = 0.0 if free_transfers >= 1 else HIT_COST
            options.append({
                "out": out_player, "in": candidate,
                "gain": gain, "net": gain - cost, "hit": cost > 0,
                "raw_gain": candidate["total"] - out_player["total"],
                "bank_after": budget - candidate["price"],
            })

    options.sort(key=lambda o: -o["net"])
    return options[:limit]


# ── Reporting ──────────────────────────────────────────────────────────


def show_squad(squad: list[dict], events: list[int], label: str) -> None:
    starters, bench, captain = pick_team(squad)
    total = sum(p["price"] for p in squad)
    horizon = f"GW{events[0]}-{events[-1]}" if len(events) > 1 else f"GW{events[0]}"

    print(f"\n=== {label} ===")
    print(f"cost £{total:.1f}m of £{BUDGET:.1f}m | projected {sum(p['total'] for p in starters):.1f} pts "
          f"from the XI over {horizon}")
    print(f"\n{'':<3}{'player':<16}{'tm':<5}{'pos':<5}{'£':>6}{'own%':>7}{'xP':>7}{'start':>7}")
    for player in starters:
        mark = "C" if player is captain else " "
        print(f"{mark:<3}{player['name']:<16}{player['team']:<5}{player['position']:<5}"
              f"{player['price']:>6.1f}{player['selected_by']:>7.1f}{player['total']:>7.1f}{player['start_prob']:>7.2f}")
    print("  -- bench --")
    for index, player in enumerate(bench, start=1):
        print(f"{index:<3}{player['name']:<16}{player['team']:<5}{player['position']:<5}"
              f"{player['price']:>6.1f}{player['selected_by']:>7.1f}{player['total']:>7.1f}{player['start_prob']:>7.2f}")

    flagged = [p for p in squad if p["moved_club"]]
    if flagged:
        # Context, not a penalty. Testing found no reliable class-wide effect
        # from a club move (see minutes.py), so the projection is unchanged --
        # this is here for a human to weigh, and for the email to mention.
        print("\n  recently joined a new club (context only, projection unchanged):")
        for player in flagged:
            print(f"    {player['name']} ({player['team']})")


def ownership_gap(squad: list[dict], players: dict) -> list[dict]:
    """Template players you don't own, with the rank exposure quantified.

    For a template-led strategy this is the primary risk report: the danger
    isn't owning someone mediocre, it's being absent when a player two-thirds
    of the field owns returns.
    """
    owned = {p["player_id"] for p in squad}
    gap = [p for p in players.values()
           if p["selected_by"] >= TEMPLATE_OWNERSHIP and p["player_id"] not in owned]
    for player in gap:
        # Expected points you concede to the average rival by not owning him.
        player["exposure"] = player["total"] * player["selected_by"] / 100
    return sorted(gap, key=lambda p: -p["exposure"])


def risk_curve(players: dict, overrides: dict) -> None:
    """What each notch of risk aversion costs in projected points.

    Shown rather than asserted: the trade between raw score and template
    safety is the whole point of the setting, so it should be visible.
    """
    banned = {p["player_id"] for p in players.values()
              if overrides.get(p["name"], {}).get("avoid")}
    print(f"\n{'risk':>6}{'expected pts (inc C)':>22}{'template covered':>18}  squad character")
    print("-" * 74)
    for risk in (0.0, 0.25, 0.5, 0.75, 1.0):
        squad = build_squad(players, banned=banned, risk=risk)
        starters, bench, captain = pick_team(squad)
        # Must match what the solver actually maximises at risk=0 -- XI, plus
        # the captain's second helping, plus the discounted bench. Reporting
        # any subset of that made risk=0 look worse than risk=0.25, which is
        # impossible when risk=0 *is* the points-maximising setting. The
        # mismatch was in the yardstick, not the optimiser.
        points = (sum(p["total"] for p in starters) + captain["total"]
                  + BENCH_WEIGHT * sum(p["total"] for p in bench))
        template = [p for p in players.values() if p["selected_by"] >= TEMPLATE_OWNERSHIP]
        owned = {p["player_id"] for p in squad}
        covered = sum(1 for p in template if p["player_id"] in owned)
        top = max(squad, key=lambda p: p["selected_by"])
        print(f"{risk:>6.2f}{points:>22.1f}{f'{covered}/{len(template)}':>18}  "
              f"most-owned pick: {top['name']} ({top['selected_by']:.0f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="optimal 15 from scratch")
    parser.add_argument("--risk", type=float, default=None,
                        help="0 = pure points, 1 = full template (default: strategy.md)")
    parser.add_argument("--risk-curve", action="store_true",
                        help="show what each risk level costs in projected points")
    parser.add_argument("--gap", action="store_true", help="template players you don't own")
    parser.add_argument("--squad", action="store_true", help="transfer suggestions for my_squad.json")
    parser.add_argument("--team", action="store_true", help="best XI, bench and captain for my_squad.json")
    parser.add_argument("--budget", type=float, default=BUDGET)
    args = parser.parse_args()

    players, events = load_projections()
    overrides = load_overrides()
    strategy = load_strategy()
    risk = strategy["risk"] if args.risk is None else args.risk

    if args.risk_curve:
        risk_curve(players, overrides)

    if args.build:
        avoid_ids = {p["player_id"] for p in players.values()
                     if overrides.get(p["name"], {}).get("avoid")}
        squad = build_squad(players, budget=args.budget, banned=avoid_ids, risk=risk)
        show_squad(squad, events, f"Optimal squad, £{args.budget:.1f}m (risk {risk:.2f})")
        if avoid_ids:
            names = sorted(players[i]["name"] for i in avoid_ids)
            print(f"\n  excluded by overrides.json: {', '.join(names)}")

    if args.team or args.squad or args.gap:
        squad, config = resolve_squad(players)
        show_squad(squad, events, "Your squad")

        gap = ownership_gap(squad, players)
        if gap:
            print(f"\n=== template gap: {TEMPLATE_OWNERSHIP:.0f}%+ owned players you don't have ===")
            print("  exposure = points you concede to the average rival by not owning him\n")
            for player in gap[:6]:
                print(f"  {player['name']:<16}{player['team']:<5}{player['position']:<5}"
                      f"£{player['price']:>5.1f}m{player['selected_by']:>7.1f}% owned  "
                      f"xP {player['total']:>5.1f}  exposure {player['exposure']:>5.1f}")

        if args.squad:
            bank = float(config.get("bank") or 0.0)
            free_transfers = config.get("free_transfers")
            free_transfers = 1 if free_transfers is None else int(free_transfers)
            print(f"\n=== transfer options (bank £{bank:.1f}m, {free_transfers} free transfer(s)) ===")
            print("  doing nothing is the benchmark: a move must beat 0.0\n")
            for option in suggest_transfers(squad, players, bank, free_transfers, overrides):
                verdict = "WORTH IT" if option["net"] > (HIT_MARGIN if option["hit"] else 0.5) else "not worth it"
                print(f"  {option['out']['name']:<15} -> {option['in']['name']:<15} "
                      f"{option['gain']:+6.2f} xP  bank after £{option['bank_after']:.1f}m  {verdict}")


if __name__ == "__main__":
    main()
