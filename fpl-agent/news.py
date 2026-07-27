"""Read the news, and turn it into flags the model can't derive itself.

The backtest's worst pick was Isak: projected 23.6 points over GW1-5, scored
0. Nothing in the statistics could have caught it. He was in the middle of a
months-long transfer saga, which is not an injury flag, not a minutes trend,
and not in any dataset -- it lived in news articles and nowhere else.

This is the layer that reads those. It takes the headlines knowledge.py
collects, narrows them to players that actually matter (your squad, plus
anyone heavily owned or heavily projected), and asks Gemini one question:
does this reporting suggest a player's minutes are at risk in a way the
numbers wouldn't show?

DESIGN RULE: the model owns the numbers, the language model owns the words.
Gemini never produces a projection, a price or a points figure. It reads
prose and returns structured concerns with a severity and a source link.
Every flag it raises is attributable to a headline you can click.

Flags are written to data/news_flags.json and consumed two ways:
  - the email quotes them alongside the projection they contradict
  - a `high` severity flag on one of your players forces the "something
    changed" email, even outside the normal schedule

Nothing here writes to overrides.json. A model reading a headline is not
grounds for silently altering a projection -- overrides stay human.

Usage:
  python news.py                 fetch, assess, write flags
  python news.py --dry-run       print what it would flag, write nothing
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
FEEDS_PATH = KNOWLEDGE_DIR / "feeds.json"
FLAGS_PATH = DATA_DIR / "news_flags.json"
SQUAD_PATH = BASE_DIR / "my_squad.json"

# Same models and fallback order as discovery-agent, which has been running
# on the free tier for months -- rolling aliases rather than pinned versions,
# because a hardcoded generation broke outright when Google retired 2.5 for
# new keys.
GEMINI_MODELS = ["gemini-pro-latest", "gemini-flash-latest"]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_SECONDS = 120

# Players worth asking about even if they aren't yours: the ones a
# recommendation might move you into.
RELEVANT_OWNERSHIP = 15.0
RELEVANT_TOP_N = 60

SEVERITIES = {"high", "medium", "low"}


def read_csv(name: str) -> list[dict]:
    path = DATA_DIR / name
    if not path.exists():
        raise SystemExit(f"{path} not found -- run snapshot.py and model.py first.")
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def relevant_players() -> dict[str, dict]:
    """Your squad, plus the players a transfer suggestion could realistically
    involve. Asking about all 558 would waste the free tier and bury the
    signal."""
    players = {p["id"]: p for p in read_csv("players.csv")}
    teams = {t["id"]: t["short_name"] for t in read_csv("teams.csv")}

    projections: dict[str, float] = {}
    projection_path = DATA_DIR / "projections.csv"
    if projection_path.exists():
        with projection_path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                projections[row["player_id"]] = projections.get(row["player_id"], 0.0) + num(row["xp"])

    wanted: dict[str, dict] = {}

    if SQUAD_PATH.exists():
        squad = json.loads(SQUAD_PATH.read_text(encoding="utf-8")).get("squad", [])
        names = {entry["name"] for entry in squad}
        for player in players.values():
            if player["web_name"] in names:
                wanted[player["id"]] = player

    for player in players.values():
        if num(player.get("selected_by_percent")) >= RELEVANT_OWNERSHIP:
            wanted[player["id"]] = player

    for player_id, _ in sorted(projections.items(), key=lambda kv: -kv[1])[:RELEVANT_TOP_N]:
        if player_id in players:
            wanted[player_id] = players[player_id]

    for player in wanted.values():
        player["team_name"] = teams.get(player["team"], "?")
        player["full_name"] = f"{player.get('first_name', '')} {player.get('second_name', '')}".strip()
    return wanted


def load_headlines() -> list[dict]:
    if not FEEDS_PATH.exists():
        return []
    return json.loads(FEEDS_PATH.read_text(encoding="utf-8")).get("items", [])


def prefilter(headlines: list[dict], players: dict[str, dict]) -> list[dict]:
    """Keep headlines that name a relevant player or club.

    Cuts the prompt down to what's plausibly about our players, so the model
    spends its attention on those rather than on general football news.
    """
    surnames = {}
    for player in players.values():
        for token in (player["web_name"], player.get("second_name", "")):
            token = (token or "").strip()
            if len(token) >= 4:
                surnames.setdefault(token.lower(), player)
    clubs = {p["team_name"].lower() for p in players.values()}

    kept = []
    for item in headlines:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        if any(re.search(rf"\b{re.escape(name)}\b", text) for name in surnames):
            kept.append(item)
        elif any(club in text for club in clubs):
            kept.append(item)
    return kept


def build_prompt(headlines: list[dict], players: dict[str, dict], squad_names: set[str]) -> str:
    lines = []
    for player in sorted(players.values(), key=lambda p: -num(p.get("selected_by_percent")))[:80]:
        mark = " [IN MY SQUAD]" if player["web_name"] in squad_names else ""
        flag = f" [FPL flag: {player['news']}]" if player.get("news") else ""
        lines.append(f"- {player['web_name']} ({player['team_name']}){mark}{flag}")
    roster = "\n".join(lines)

    stories = "\n".join(
        f"- {item.get('title', '')} [{item.get('source', '')}] {item.get('link', '')}"
        for item in headlines[:120]
    ) or "(no headlines matched these players)"

    return f"""You are a Fantasy Premier League research assistant. Today is \
{datetime.now(timezone.utc).strftime('%A %d %B %Y')}.

Your ONLY job is to spot things in the news that a statistical model cannot
see. The model already knows: injuries FPL has officially flagged, minutes
history, expected goals, fixtures and prices. Do not repeat those.

What matters and is invisible to statistics:
- transfer sagas and moves in progress (a player may stop being selected)
- managers publicly questioning or dropping a player
- a new manager changing who starts
- a role change (moved position, lost penalties, lost set pieces)
- a return from injury that FPL hasn't flagged yet
- rotation warnings ahead of European or cup fixtures

PLAYERS I CARE ABOUT:
{roster}

HEADLINES:
{stories}

Return ONLY a JSON array inside a ```json code fence. One entry per player
you have a genuine concern or update about -- if the headlines say nothing
meaningful, return an empty array. Do not invent stories, and do not include
a player whose only issue is an injury FPL has already flagged.

Each entry:
{{
  "player": "exact name from the list above",
  "concern": "one sentence, factual, on what the reporting says",
  "severity": "high | medium | low",
  "affects": "minutes | role | availability | other",
  "source": "the headline URL you used"
}}

severity high = likely to miss or lose his place; medium = real doubt;
low = worth knowing. Never output a points projection or a price.
No prose outside the JSON."""


def call_gemini(api_key: str, prompt: str) -> tuple[str, str]:
    last_error: Exception | None = None
    for model in GEMINI_MODELS:
        try:
            response = requests.post(
                GEMINI_URL.format(model=model), params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.2}},
                timeout=TIMEOUT_SECONDS,
            )
            if response.status_code in (404, 429, 500, 503):
                raise RuntimeError(f"{model} returned HTTP {response.status_code}")
            response.raise_for_status()
            parts = response.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
            if text.strip():
                return text, model
            raise RuntimeError(f"{model} returned nothing")
        except Exception as exc:  # noqa: BLE001 -- try the next model
            last_error = exc
            print(f"[news] {model} failed: {exc}", file=sys.stderr)
    raise RuntimeError(f"all Gemini models failed; last error: {last_error}")


def parse_flags(text: str, players: dict[str, dict]) -> list[dict]:
    """Parse the reply and keep only flags naming a real player.

    A hallucinated name is dropped rather than guessed at -- a flag that
    can't be tied to an actual player is worse than no flag, because it
    would appear in the email looking authoritative.
    """
    match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL) or re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        raw = json.loads(match.group(1) if match.lastindex else match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    by_name = {p["web_name"].lower(): p for p in players.values()}
    flags = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("player", "")).strip()
        # The prompt lists players as "Name (TEAM)", so the model reasonably
        # echoes that format back. Matching only the bare name silently
        # discarded real flags -- a live run threw away concerns about
        # Calvert-Lewin and Rogers this way. Strip a trailing parenthetical
        # and use it to disambiguate instead of rejecting the flag.
        team_hint = ""
        bracket = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", name)
        if bracket:
            name, team_hint = bracket.group(1).strip(), bracket.group(2).strip()
        player = by_name.get(name.lower())
        if player and team_hint and player.get("team_name", "").lower() != team_hint.lower():
            # Same surname, different club -- prefer the one the model meant.
            better = next((p for p in players.values()
                           if p["web_name"].lower() == name.lower()
                           and p.get("team_name", "").lower() == team_hint.lower()), None)
            player = better or player
        if not player or not entry.get("concern"):
            if name:
                print(f"[news] dropping flag for unrecognised player '{name}'", file=sys.stderr)
            continue
        severity = str(entry.get("severity", "low")).lower()
        source = str(entry.get("source", "")).strip()
        flags.append({
            "player_id": player["id"],
            "player": player["web_name"],
            "team": player["team_name"],
            "concern": str(entry["concern"]).strip()[:300],
            "severity": severity if severity in SEVERITIES else "low",
            "affects": str(entry.get("affects", "other")).strip()[:20],
            "source": source if source.startswith(("http://", "https://")) else "",
        })
    return flags


def run(dry_run: bool) -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    players = relevant_players()
    squad_names = set()
    if SQUAD_PATH.exists():
        squad_names = {e["name"] for e in json.loads(SQUAD_PATH.read_text(encoding="utf-8")).get("squad", [])}

    headlines = prefilter(load_headlines(), players)
    print(f"[news] {len(players)} players of interest, {len(headlines)} relevant headlines")

    if not api_key:
        print("[news] GEMINI_API_KEY not set -- writing an empty flag set", file=sys.stderr)
        flags, model_used = [], "none (no API key)"
    elif not headlines:
        print("[news] nothing to assess")
        flags, model_used = [], "none (no headlines)"
    else:
        text, model_used = call_gemini(api_key, build_prompt(headlines, players, squad_names))
        flags = parse_flags(text, players)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_used,
        "headlines_considered": len(headlines),
        "flags": flags,
    }

    if dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    FLAGS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[news] wrote data/news_flags.json ({len(flags)} flags, via {model_used})")
    for flag in flags:
        print(f"  [{flag['severity']}] {flag['player']} ({flag['team']}): {flag['concern']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print flags, write nothing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
