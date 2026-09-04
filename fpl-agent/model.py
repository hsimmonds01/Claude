"""Expected points model.

Turns the raw snapshot into a projected points figure per player, per
gameweek, over a multi-gameweek horizon. Everything here is deliberately
explainable: each projection decomposes into named parts the email can quote,
because a recommendation you can't interrogate is one you can't trust.

    xP = P(play) x [ appearance
                   + goals   (xG90  x position value)
                   + assists (xA90  x 3)
                   + clean sheet probability x position value
                   + P(hitting the DefCon threshold) x 2
                   + expected bonus ]

Three things this model takes seriously that a naive one doesn't:

1. MINUTES ARE THE WHOLE GAME. A brilliant player who doesn't start scores
   nothing, and backtesting found 80% of this model's error came from
   minutes rather than from misjudging players. Start probability is a
   recency-weighted start rate (see minutes.py), then injury status, then
   explicit human judgement in overrides.json.

2. SMALL SAMPLES LIE. A player with 300 minutes of hot xG is not better than
   one with 3,000 minutes of good xG. Rates are shrunk toward the positional
   mean in proportion to how little evidence there is behind them.

3. UNTESTED INTUITIONS STAY OUT. An earlier version discounted players who
   had changed clubs, on the reasonable theory that new signings are rotation
   risks. Tested across two seasons it did not replicate -- movers were
   over-predicted one year and under-predicted the next -- so it was removed.
   Club moves are still shown as a flag for a human to weigh; they no longer
   silently move the number. minutes.py records what was tested and rejected.

Pre-season caveat, honestly stated: FPL publishes team attack/defence
strength as zeros until the season starts, so clean sheets are modelled from
fixture difficulty for now. Once results exist the ratings should be computed
from them instead -- see team_strength().

Outputs data/projections.csv, one row per player per gameweek in the horizon,
plus the component breakdown behind every number.

Usage:
  python model.py                  project the next 5 gameweeks
  python model.py --horizon 8      project further ahead
  python model.py --explain NAME   print the full breakdown for one player
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from minutes import base_start_probability, start_rates

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OVERRIDES_PATH = BASE_DIR / "overrides.json"

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# Scoring, taken from the official rules captured in knowledge/official/fpl-rules.md.
GOAL_POINTS = {1: 10, 2: 6, 3: 5, 4: 4}
CLEAN_SHEET_POINTS = {1: 4, 2: 4, 3: 1, 4: 0}
ASSIST_POINTS = 3
DEFCON_POINTS = 2
# Defenders need 10+ CBIT; midfielders and forwards need 12+ CBIT *plus
# recoveries*. Goalkeepers cannot earn it. The points do not stack.
DEFCON_THRESHOLD = {2: 10, 3: 12, 4: 12}

FULL_APPEARANCE_MINUTES = 60
SEASON_MATCHES = 38

# How much evidence counts as "enough". A player with this many minutes keeps
# most of his own rate; one with far fewer is pulled toward the positional
# average. Roughly a third of a season -- the point at which a rate stops
# being noise.
SHRINKAGE_MINUTES = 1000.0

# A player who has just joined a club is a genuine unknown regardless of
# reputation or price, so his rates are pulled toward average and his start
# probability toward a neutral prior rather than assumed.
# A club move is still surfaced as a flag on the projection -- it's real
# context for a human reading the email -- but it no longer changes the
# number. Tested on two season pairs and it did not replicate; see minutes.py.
RECENT_MOVE_DAYS = 120

# Injury status codes: a=available, d=doubtful, i=injured, s=suspended,
# u=unavailable, n=on loan/not in squad.
STATUS_MULTIPLIER = {"a": 1.0, "d": 0.5, "i": 0.0, "s": 0.0, "u": 0.0, "n": 0.0}

# Expected goals conceded by fixture difficulty, used only until real results
# exist. Calibrated so the implied clean-sheet rates (37%/29%/22%/17%/12%)
# sit in the range the Premier League actually produces.
XGC_BY_DIFFICULTY = {1: 0.85, 2: 1.00, 3: 1.25, 4: 1.50, 5: 2.10}


# Season whose gameweek-by-gameweek data feeds the minutes model.
ARCHIVE_SEASON = "2025-26"

CALIBRATION_PATH = DATA_DIR / "calibration.json"
# Minutes a player needs before his actual points-per-gameweek is treated as
# a fair benchmark to calibrate against.
CALIBRATION_MIN_MINUTES = 1800


def load_calibration() -> dict[str, float]:
    """Per-position multipliers correcting systematic level bias.

    Measured, not guessed: --calibrate compares projections against what the
    same players actually scored and writes the ratios here. The first run
    found defenders projected 14% high (clean sheets and DefCon compounding)
    while other positions sat within 5%. Left uncorrected that bias doesn't
    just misprice defenders, it makes the optimiser prefer them over
    equivalent midfielders -- a structural error in every squad it builds.
    """
    if not CALIBRATION_PATH.exists():
        return {}
    return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8")).get("position_multipliers", {})


def read_csv(name: str) -> list[dict]:
    path = DATA_DIR / name
    if not path.exists():
        raise SystemExit(f"{path} not found -- run snapshot.py (and history.py) first.")
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_overrides() -> dict:
    if not OVERRIDES_PATH.exists():
        return {}
    raw = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# ── Evidence from last season ──────────────────────────────────────────


def latest_season_rows(history: list[dict]) -> tuple[dict, str]:
    """Most recent season present, keyed by player id. Derived from the data
    rather than hardcoded so this keeps working next season."""
    seasons = sorted({r["season_name"] for r in history if r.get("season_name")})
    if not seasons:
        return {}, ""
    latest = seasons[-1]
    return {r["player_id"]: r for r in history if r["season_name"] == latest}, latest


def positional_means(players: list[dict], last: dict) -> dict[int, dict]:
    """Average per-90 rates by position, over players with enough minutes to
    be meaningful. These are what thin samples get shrunk toward."""
    means: dict[int, dict] = {}
    for pos in POSITIONS:
        xg = xa = defcon = bonus = mins = 0.0
        for player in players:
            if int(player["element_type"]) != pos:
                continue
            row = last.get(player["id"])
            if not row or num(row["minutes"]) < SHRINKAGE_MINUTES:
                continue
            mins += num(row["minutes"])
            xg += num(row["expected_goals"])
            xa += num(row["expected_assists"])
            defcon += num(row["defensive_contribution"])
            bonus += num(row["bonus"])
        per90 = lambda total: (total / mins * 90) if mins else 0.0  # noqa: E731
        means[pos] = {
            "xg90": per90(xg), "xa90": per90(xa),
            "defcon90": per90(defcon), "bonus90": per90(bonus),
        }
    return means


def shrink(rate: float, minutes: float, prior: float) -> float:
    """Empirical-Bayes style blend: trust the player's own rate in proportion
    to the minutes behind it, otherwise fall back on his position's average."""
    weight = minutes / (minutes + SHRINKAGE_MINUTES)
    return weight * rate + (1 - weight) * prior


# ── Minutes ────────────────────────────────────────────────────────────


def moved_recently(player: dict, now: datetime) -> bool:
    raw = (player.get("team_join_date") or "").strip()
    if not raw:
        return False
    try:
        joined = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - joined).days <= RECENT_MOVE_DAYS


def start_probability(player: dict, row: dict | None, moved: bool, override: dict,
                      archive: dict, games_played: int = 0) -> tuple[float, str]:
    """Returns (probability, why). The reason travels with the number so the
    email can explain a projection instead of just asserting one."""
    if "start_prob" in override:
        return float(override["start_prob"]), override.get("minutes_note", "manual override")

    status = (player.get("status") or "a").lower()
    if STATUS_MULTIPLIER.get(status, 1.0) == 0.0:
        return 0.0, f"unavailable (status '{status}'): {player.get('news') or 'no detail'}"

    # Recency-weighted start rate from the gameweek archive -- tested to
    # predict opening-five minutes better than a season average on both
    # season pairs available. See minutes.py for the numbers.
    fallback = num(row["starts"]) if row and num(row["minutes"]) > 0 else None
    # THIS season's own starts, once there are any -- see minutes.py's
    # in-season-updating evidence. player["starts"] is players.csv's live
    # season-to-date count, refreshed every snapshot.py run.
    base, reason = base_start_probability(
        player, archive, fallback,
        current_starts=num(player.get("starts")), games_played=games_played)

    # NOTE: no club-move discount. It was tested and did not replicate --
    # movers were over-predicted one season and under-predicted the next.
    # Specific risky moves belong in overrides.json with a named reason,
    # not in a rule that mis-ranks every new signing. See minutes.py.

    chance = player.get("chance_of_playing_next_round")
    if chance not in (None, "", "None"):
        base *= num(chance) / 100
        reason += f"; {num(chance):.0f}% chance of playing"
    elif status == "d":
        base *= STATUS_MULTIPLIER["d"]
        reason += "; flagged doubtful"

    return max(0.0, min(base, 1.0)), reason


# ── Fixtures ───────────────────────────────────────────────────────────


def team_strength(fixtures: list[dict]) -> None:
    """Placeholder for the real thing.

    FPL publishes strength_attack_* and strength_defence_* as zeros until the
    season is under way, so there is nothing to read pre-season. Once results
    exist these should be computed from actual goals scored and conceded
    (an Elo-style rating updated weekly), which responds far faster than
    FPL's own difficulty ratings -- those are set pre-season and barely move.
    Until then, clean sheets are modelled from fixture difficulty alone.
    """


def upcoming_fixtures(fixtures: list[dict], next_event: int, horizon: int) -> dict[int, list[dict]]:
    """team id -> its fixtures in the horizon. A team can appear twice in one
    gameweek (a double) or not at all (a blank); both fall out naturally
    because this keeps a list per gameweek rather than assuming one match."""
    by_team: dict[int, list[dict]] = {}
    for fixture in fixtures:
        if not fixture.get("event"):
            continue
        event = int(num(fixture["event"]))
        if not (next_event <= event < next_event + horizon):
            continue
        home, away = int(fixture["team_h"]), int(fixture["team_a"])
        by_team.setdefault(home, []).append({
            "event": event, "opponent": away, "home": True,
            "difficulty": int(num(fixture["team_h_difficulty"], 3)),
        })
        by_team.setdefault(away, []).append({
            "event": event, "opponent": home, "home": False,
            "difficulty": int(num(fixture["team_a_difficulty"], 3)),
        })
    return by_team


def clean_sheet_probability(difficulty: int, home: bool) -> float:
    """Poisson: P(0 conceded) = exp(-expected goals conceded)."""
    xgc = XGC_BY_DIFFICULTY.get(difficulty, 1.35)
    xgc *= 0.90 if home else 1.10
    return math.exp(-xgc)


def defcon_probability(rate90: float, threshold: int) -> float:
    """P(at least `threshold` defensive actions in 90 minutes), Poisson.

    Poisson understates a player whose action count is consistent rather than
    random, so this is a floor rather than an exact figure -- worth
    recalibrating against real per-match counts once the season provides them.
    """
    if rate90 <= 0:
        return 0.0
    cumulative = sum(math.exp(-rate90) * rate90 ** k / math.factorial(k) for k in range(threshold))
    return max(0.0, 1.0 - cumulative)


# ── Projection ─────────────────────────────────────────────────────────


def project_player(player, row, fixture, means, moved, override, attack_multiplier, archive, games_played=0):
    pos = int(player["element_type"])
    minutes = num(row["minutes"]) if row else 0.0
    mean = means[pos]

    if row and minutes > 0:
        xg90 = shrink(num(row["expected_goals"]) / minutes * 90, minutes, mean["xg90"])
        xa90 = shrink(num(row["expected_assists"]) / minutes * 90, minutes, mean["xa90"])
        defcon90 = shrink(num(row["defensive_contribution"]) / minutes * 90, minutes, mean["defcon90"])
        bonus90 = shrink(num(row["bonus"]) / minutes * 90, minutes, mean["bonus90"])
    else:
        xg90, xa90 = mean["xg90"], mean["xa90"]
        defcon90, bonus90 = mean["defcon90"], mean["bonus90"]

    # An explicit system-fit concern (a human judgement in overrides.json)
    # still softens the attacking rates. A bare club move no longer does --
    # that adjustment failed to replicate across seasons.
    if override.get("system_fit_risk"):
        blend = lambda own, avg: 0.55 * own + 0.45 * avg  # noqa: E731
        xg90, xa90 = blend(xg90, mean["xg90"]), blend(xa90, mean["xa90"])

    start_prob, minutes_reason = start_probability(player, row, moved, override, archive, games_played)

    goals = xg90 * attack_multiplier * GOAL_POINTS[pos]
    assists = xa90 * attack_multiplier * ASSIST_POINTS
    clean_sheet = 0.0
    if CLEAN_SHEET_POINTS[pos]:
        clean_sheet = clean_sheet_probability(fixture["difficulty"], fixture["home"]) * CLEAN_SHEET_POINTS[pos]
    defcon = 0.0
    if pos in DEFCON_THRESHOLD:
        defcon = defcon_probability(defcon90, DEFCON_THRESHOLD[pos]) * DEFCON_POINTS

    # 2 for a full appearance; a start that ends early still usually clears
    # 60 minutes, so this is close enough without a substitution model.
    appearance = 2.0
    per_start = appearance + goals + assists + clean_sheet + defcon + bonus90
    return {
        "xp": start_prob * per_start,
        "start_prob": start_prob,
        "minutes_reason": minutes_reason,
        "appearance": appearance, "goals": goals, "assists": assists,
        "clean_sheet": clean_sheet, "defcon": defcon, "bonus": bonus90,
        "moved_club": moved,
    }


def build_projections(horizon: int) -> list[dict]:
    players = read_csv("players.csv")
    fixtures = read_csv("fixtures.csv")
    history = read_csv("player_history_past.csv")
    meta = json.loads((DATA_DIR / "meta.json").read_text(encoding="utf-8"))
    teams = {t["id"]: t["short_name"] for t in read_csv("teams.csv")}
    overrides = load_overrides()

    last, season = latest_season_rows(history)
    means = positional_means(players, last)
    calibration = load_calibration()
    # Gameweek-level archive for the season just gone -- the source of the
    # recency-weighted start rates. Absent, minutes.py falls back to season
    # totals, so this is an upgrade rather than a hard dependency.
    archive = start_rates(ARCHIVE_SEASON)
    print(f"[model] minutes evidence: {len(archive)} players from the {ARCHIVE_SEASON} gameweek archive")
    next_event = int(meta.get("next_event") or 1)
    # Gameweeks actually completed so far this season -- what turns
    # PRIOR_GAMES from a permanent discount into a shrinking one. 0 before
    # a ball's kicked, so nothing changes pre-season; see minutes.py.
    games_played = max(next_event - 1, 0)
    by_team = upcoming_fixtures(fixtures, next_event, horizon)
    now = datetime.now(timezone.utc)

    print(f"[model] projecting GW{next_event}-{next_event + horizon - 1}, "
          f"evidence from {season}, {len(players)} players")

    rows = []
    for player in players:
        override = overrides.get(player["web_name"], {})
        if override.get("team") and teams.get(player["team"]) != override["team"]:
            override = {}
        row = last.get(player["id"])
        moved = moved_recently(player, now)

        position = POSITIONS[int(player["element_type"])]
        factor = calibration.get(position, 1.0)
        for fixture in by_team.get(int(player["team"]), []):
            projection = project_player(player, row, fixture, means, moved, override, 1.0, archive, games_played)
            projection["xp"] *= factor
            rows.append({
                "player_id": player["id"], "web_name": player["web_name"],
                "team": teams.get(player["team"], "?"), "position": POSITIONS[int(player["element_type"])],
                "price": num(player["now_cost"]) / 10,
                "selected_by": num(player["selected_by_percent"]),
                "event": fixture["event"],
                "opponent": teams.get(str(fixture["opponent"]), "?"),
                "home": "H" if fixture["home"] else "A",
                "difficulty": fixture["difficulty"],
                "xp": round(projection["xp"], 3),
                "start_prob": round(projection["start_prob"], 3),
                "goals_pts": round(projection["goals"], 3),
                "assists_pts": round(projection["assists"], 3),
                "cs_pts": round(projection["clean_sheet"], 3),
                "defcon_pts": round(projection["defcon"], 3),
                "bonus_pts": round(projection["bonus"], 3),
                "moved_club": "yes" if projection["moved_club"] else "",
                "override": "yes" if override else "",
                "minutes_reason": projection["minutes_reason"],
            })
    return rows


def write_projections(rows: list[dict]) -> None:
    path = DATA_DIR / "projections.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[model] wrote data/projections.csv ({len(rows)} rows)")


def explain(rows: list[dict], name: str) -> None:
    matches = [r for r in rows if name.lower() in r["web_name"].lower()]
    if not matches:
        print(f"No player matching '{name}'")
        return
    for row in matches:
        print(f"\n{row['web_name']} ({row['team']} {row['position']}, £{row['price']}m) "
              f"GW{row['event']} vs {row['opponent']}({row['home']}) FDR {row['difficulty']}")
        print(f"  projected {row['xp']} pts")
        print(f"  start probability {row['start_prob']} -- {row['minutes_reason']}")
        print(f"  goals {row['goals_pts']} | assists {row['assists_pts']} | "
              f"clean sheet {row['cs_pts']} | defcon {row['defcon_pts']} | bonus {row['bonus_pts']}")
        if row["moved_club"]:
            print("  NOTE: recent club move -- rates pulled toward positional average")
        if row["override"]:
            print("  NOTE: manual override applied from overrides.json")


def calibrate(rows: list[dict]) -> None:
    """Compare uncalibrated projections against what the same players actually
    scored, and write the per-position correction factors.

    Only players with real minutes count, and injured players (projected zero)
    are excluded -- a correctly-zeroed injury isn't a modelling error and
    would drag the ratios down if counted as one.
    """
    import statistics

    history = read_csv("player_history_past.csv")
    last, season = latest_season_rows(history)
    existing = load_calibration()

    projected: dict[str, list[float]] = {}
    actual: dict[str, list[float]] = {}
    per_player: dict[str, list[float]] = {}
    position_of: dict[str, str] = {}
    for row in rows:
        per_player.setdefault(row["player_id"], []).append(float(row["xp"]))
        position_of[row["player_id"]] = row["position"]

    for player_id, values in per_player.items():
        history_row = last.get(player_id)
        if not history_row or num(history_row["minutes"]) < CALIBRATION_MIN_MINUTES:
            continue
        mean_xp = statistics.mean(values)
        if mean_xp <= 0:
            continue
        position = position_of[player_id]
        # Undo any calibration already applied, so factors are always measured
        # against the raw model rather than compounding run on run.
        projected.setdefault(position, []).append(mean_xp / existing.get(position, 1.0))
        actual.setdefault(position, []).append(num(history_row["total_points"]) / SEASON_MATCHES)

    multipliers = {}
    print(f"\n[calibrate] benchmark: {season}, players with {CALIBRATION_MIN_MINUTES}+ minutes")
    print(f"{'pos':<5}{'n':>4}{'model':>8}{'actual':>8}{'factor':>8}")
    for position in ("GKP", "DEF", "MID", "FWD"):
        if not projected.get(position):
            continue
        model_mean = statistics.mean(projected[position])
        actual_mean = statistics.mean(actual[position])
        factor = actual_mean / model_mean if model_mean else 1.0
        multipliers[position] = round(factor, 4)
        print(f"{position:<5}{len(projected[position]):>4}{model_mean:>8.2f}{actual_mean:>8.2f}{factor:>8.3f}")

    CALIBRATION_PATH.write_text(json.dumps({
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_season": season,
        "min_minutes": CALIBRATION_MIN_MINUTES,
        "position_multipliers": multipliers,
        "_note": "Measured by `python model.py --calibrate`, not hand-tuned. "
                 "Corrects systematic level bias per position so the optimiser "
                 "compares positions fairly. Re-run when a season's data lands.",
    }, indent=2) + "\n", encoding="utf-8")
    print(f"[calibrate] wrote data/calibration.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=5, help="how many gameweeks to project")
    parser.add_argument("--explain", metavar="NAME", help="print the full breakdown for one player")
    parser.add_argument("--calibrate", action="store_true",
                        help="measure per-position bias against actual points and write calibration.json")
    args = parser.parse_args()

    rows = build_projections(args.horizon)
    if not rows:
        raise SystemExit("No projections produced -- check that fixtures cover the upcoming gameweeks.")
    if args.calibrate:
        calibrate(rows)
        # Rebuild so the written projections use the factors just measured.
        rows = build_projections(args.horizon)
    write_projections(rows)
    if args.explain:
        explain(rows, args.explain)


if __name__ == "__main__":
    main()
