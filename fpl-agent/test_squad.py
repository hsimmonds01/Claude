"""Offline tests for squad.py's transformation logic.

The dev sandbox can't reach fantasy.premierleague.com (see CLAUDE.md), so
this never calls the real API -- it feeds reconstruct() a synthetic payload
shaped like the real one and checks what comes out. What matters here is the
logic (captain/bench detection, chip mapping, and -- deliberately -- that
the manager's real name never appears anywhere), not the HTTP call itself.

Run: python test_squad.py
"""

from __future__ import annotations

import json
from unittest.mock import patch

import squad

results = []


def check(label, condition, detail=""):
    results.append((label, condition))
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"  -> {detail}" if detail and not condition else ""))


PLAYERS = {
    "411": {"id": "411", "web_name": "Haaland", "team": "15", "element_type": "4"},
    "109": {"id": "109", "web_name": "Verbruggen", "team": "5", "element_type": "1"},
    "496": {"id": "496", "web_name": "Kinsky", "team": "19", "element_type": "1"},
    "423": {"id": "423", "web_name": "Shaw", "team": "16", "element_type": "2"},
}
TEAMS = {"15": "MCI", "5": "BHA", "19": "TOT", "16": "MUN"}


def make_pick(element, position, captain=False, vice=False):
    return {"element": element, "position": position, "multiplier": 2 if captain else 1,
            "is_captain": captain, "is_vice_captain": vice}


print("a clean 15-pick response reconstructs correctly")
picks_data = {
    "active_chip": None,
    "entry_history": {"points": 68, "bank": 5, "value": 1000, "points_on_bench": 4},
    # 11 starters (reusing ids to pad to 15 -- fine, this test only checks
    # captain/vice/bench wiring, not real squad-legality).
    "picks": (
        [make_pick(411, 1, captain=True)]
        + [make_pick(423, i) for i in range(2, 11)]
        + [make_pick(109, 11, vice=True)]
        + [make_pick(496, 12), make_pick(423, 13), make_pick(411, 14), make_pick(109, 15)]
    ),
}
entry_data = {
    "name": "The Lazy Deploy", "summary_overall_points": 500, "summary_overall_rank": 12345,
    "player_first_name": "REAL_FIRST_NAME_MUST_NEVER_APPEAR",
    "player_last_name": "REAL_LAST_NAME_MUST_NEVER_APPEAR",
}
with patch("squad.fetch_json", side_effect=[picks_data, entry_data]):
    result = squad.reconstruct("999999", 1, PLAYERS, TEAMS)

check("reconstruct succeeds", result is not None, str(result))
check("captain identified", result and result["captain"] == "Haaland")
check("vice captain identified", result and result["vice_captain"] == "Verbruggen")
check("bench is exactly 4 names", result and len(result["bench"]) == 4, str(result and result["bench"]))
check("bank converted from tenths", result and result["bank"] == 0.5)
check("squad value converted from tenths", result and result["squad_value"] == 100.0)

serialised = json.dumps(result)
check("real first name never appears in the result",
      "REAL_FIRST_NAME_MUST_NEVER_APPEAR" not in serialised)
check("real last name never appears in the result",
      "REAL_LAST_NAME_MUST_NEVER_APPEAR" not in serialised)
check("player_first_name key itself never carried through", "player_first_name" not in result)

print("\na chip in play is mapped to this project's own naming")
picks_with_chip = dict(picks_data)
picks_with_chip["active_chip"] = "bboost"
with patch("squad.fetch_json", side_effect=[picks_with_chip, entry_data]):
    result_chip = squad.reconstruct("999999", 1, PLAYERS, TEAMS)
check("bboost maps to bench_boost", result_chip and result_chip["active_chip"] == "bboost")
check("chip name resolves via CHIP_NAMES", squad.CHIP_NAMES.get(result_chip["active_chip"]) == "bench_boost")

print("\na short or malformed picks list is refused rather than guessed at")
with patch("squad.fetch_json", side_effect=[{"active_chip": None, "entry_history": {},
                                              "picks": [make_pick(411, 1, captain=True)]}, entry_data]):
    short_result = squad.reconstruct("999999", 1, PLAYERS, TEAMS)
check("fewer than 15 resolved picks is refused, not truncated", short_result is None)

print("\na 404 (wrong id, or gameweek not yet past its deadline) is a clean no-op")
with patch("squad.fetch_json", return_value=None):
    none_result = squad.reconstruct("999999", 1, PLAYERS, TEAMS)
check("failed fetch returns None rather than raising", none_result is None)

print("\nupdate_my_squad_json writes only the fields it owns")
import tempfile, os
from pathlib import Path

original_path = squad.SQUAD_PATH
tmp_dir = tempfile.mkdtemp()
tmp_path = Path(tmp_dir) / "my_squad.json"
tmp_path.write_text(json.dumps({
    "_README": ["kept as-is"], "team_id": "999999", "team_id_note": "kept as-is",
    "bank": 99.9, "free_transfers": 2, "chips_used": [], "my_lineup": None,
    "squad": [], "history": ["existing entry"],
}), encoding="utf-8")
squad.SQUAD_PATH = tmp_path
try:
    squad.update_my_squad_json(result)
    written = json.loads(tmp_path.read_text(encoding="utf-8"))
finally:
    squad.SQUAD_PATH = original_path

check("_README preserved untouched", written["_README"] == ["kept as-is"])
check("team_id preserved untouched", written["team_id"] == "999999")
check("free_transfers left alone (not this script's job)", written["free_transfers"] == 2)
check("bank overwritten from the live result", written["bank"] == 0.5)
check("my_lineup overwritten from the live result", written["my_lineup"]["captain"] == "Haaland")
check("history appended to, not replaced", written["history"][0] == "existing entry" and len(written["history"]) == 2)
check("appended history line names no real name",
      "REAL_FIRST_NAME_MUST_NEVER_APPEAR" not in written["history"][-1])

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    raise SystemExit("FAILED: " + "; ".join(r[0] for r in failed))
