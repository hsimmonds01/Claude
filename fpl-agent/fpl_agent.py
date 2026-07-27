"""The agent: decides what to send, and sends it.

Runs every few hours and asks one question -- is an email due right now?
Nothing is scheduled by day of the week, because gameweeks aren't. A Saturday
round, a Tuesday round and a festive pile-up all fall out of the same logic:
read the real deadlines from the API, read the `data_checked` flag that says
a gameweek's points are final, and compare against what's already been sent.

FOUR KINDS OF EMAIL

  main      the deadline briefing. Lead time scales with congestion (below).
  final     a short "something changed" note ~3h before the deadline, only if
            it actually changed.
  review    how the last gameweek went. Fires when its points go final, not
            on a fixed weekday.
  alert     a high-severity news flag on one of your players, any time.

LEAD TIME SCALES WITH CONGESTION

A fixed 36 hours breaks in midweek rounds: for a Tuesday 18:30 deadline it
lands Monday breakfast, before the Monday press conference -- missing the
news the timing exists to capture.

    gap since last deadline    main email
    >= 6 days (normal)         36h before
    3-5 days (midweek)         24h before
    < 3 days (festive)         18h before

EMAILS MERGE WHEN THEY COLLIDE

If a review comes due within 24h of the main email, they're sent as one. In a
congested run that's the normal case, and it's the right outcome: you want
one good email, not three fragments.

Capped at 3 emails per rolling 72 hours, with one override that always sends:
a high-severity flag on a player in your team.

CONFIGURATION -- all via environment, nothing personal in the repo:
  FPL_TEAM_ID      your team id (optional until GW1 is played)
  FPL_EMAIL_TO     where to send
  RESEND_API_KEY   sending
  GEMINI_API_KEY   the news layer (news.py)

Usage:
  python fpl_agent.py                  decide and send if due
  python fpl_agent.py --dry-run        decide and print, send nothing
  python fpl_agent.py --force main     send a given email regardless
  python fpl_agent.py --test-email     prove the Resend path end to end
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import email_render as render
from optimiser import (
    HIT_MARGIN, load_overrides, load_projections, load_strategy,
    ownership_gap, pick_team, resolve_squad, suggest_transfers,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = BASE_DIR / "state.json"
HISTORY_PATH = BASE_DIR / "history.json"

RESEND_URL = "https://api.resend.com/emails"
EMAIL_FROM = "FPL Agent <onboarding@resend.dev>"

# Lead time in hours, by the gap since the previous deadline.
LEAD_TIMES = ((6.0, 36.0), (3.0, 24.0), (0.0, 18.0))
FINAL_CHECK_HOURS = 3.0
# Don't send a "final check" that lands before the main email.
FINAL_CHECK_MIN_GAP_HOURS = 6.0
MERGE_WINDOW_HOURS = 24.0
MAX_EMAILS_PER_72H = 3


def num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_csv(name: str) -> list[dict]:
    path = DATA_DIR / name
    if not path.exists():
        raise SystemExit(f"{path} not found -- run snapshot.py first.")
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_state() -> dict:
    state = load_json(STATE_PATH, {})
    state.setdefault("sent", [])
    return state


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ── Scheduling ─────────────────────────────────────────────────────────


def lead_hours(gap_days: float) -> float:
    for threshold, hours in LEAD_TIMES:
        if gap_days >= threshold:
            return hours
    return LEAD_TIMES[-1][1]


def already_sent(state: dict, kind: str, event: int) -> bool:
    return any(s["kind"] == kind and s["event"] == event for s in state["sent"])


def recent_send_count(state: dict, now: datetime) -> int:
    cutoff = now - timedelta(hours=72)
    count = 0
    for entry in state["sent"]:
        when = parse_time(entry.get("at", ""))
        if when and when >= cutoff:
            count += 1
    return count


def decide(events: list[dict], state: dict, now: datetime, flags: list[dict],
           squad_names: set[str]) -> dict:
    """What, if anything, is due right now -- and why.

    Always returns a reason, including when the answer is "nothing", so a
    quiet run is explainable from the logs rather than a mystery.
    """
    upcoming = [e for e in events if parse_time(e["deadline_time"]) and parse_time(e["deadline_time"]) > now]
    upcoming.sort(key=lambda e: e["deadline_time"])
    next_event = upcoming[0] if upcoming else None

    # The most recent finished-and-final gameweek that hasn't been reviewed.
    finalised = [e for e in events
                 if str(e.get("data_checked", "")).lower() in ("true", "1")
                 and not already_sent(state, "review", int(e["id"]))]
    finalised.sort(key=lambda e: int(e["id"]))
    review_due = finalised[-1] if finalised else None

    urgent = [f for f in flags if f.get("severity") == "high" and f["player"] in squad_names]

    if not next_event:
        if review_due:
            return {"kind": "review", "event": int(review_due["id"]),
                    "reason": "season over; last gameweek's points are final"}
        return {"kind": None, "reason": "no upcoming deadline and nothing to review"}

    deadline = parse_time(next_event["deadline_time"])
    event_id = int(next_event["id"])
    hours_to_deadline = (deadline - now).total_seconds() / 3600

    previous = [e for e in events if parse_time(e["deadline_time"]) and parse_time(e["deadline_time"]) < now]
    previous.sort(key=lambda e: e["deadline_time"])
    if previous:
        gap_days = (deadline - parse_time(previous[-1]["deadline_time"])).total_seconds() / 86400
    else:
        gap_days = 7.0
    lead = lead_hours(gap_days)

    # A high-severity flag on one of your players always sends, even over cap.
    if urgent and not already_sent(state, f"alert-{urgent[0]['player']}", event_id):
        return {"kind": f"alert-{urgent[0]['player']}", "event": event_id, "urgent": True,
                "flags": urgent, "deadline": deadline, "gap_days": gap_days,
                "reason": f"high-severity news on {urgent[0]['player']}, who is in your squad"}

    if recent_send_count(state, now) >= MAX_EMAILS_PER_72H:
        return {"kind": None, "reason": f"{MAX_EMAILS_PER_72H}-email cap reached in the last 72h"}

    main_due = hours_to_deadline <= lead and not already_sent(state, "main", event_id)
    if main_due:
        merged = bool(review_due)
        return {"kind": "main", "event": event_id, "deadline": deadline, "gap_days": gap_days,
                "lead": lead, "merge_review": int(review_due["id"]) if merged else None,
                "reason": (f"{hours_to_deadline:.1f}h to the GW{event_id} deadline "
                           f"(lead {lead:.0f}h for a {gap_days:.1f}-day gap)"
                           + (f"; merging the GW{review_due['id']} review" if merged else ""))}

    if review_due:
        # Hold a review that would land just before the main email; it'll be
        # merged into it instead.
        hours_until_main = hours_to_deadline - lead
        if hours_until_main <= MERGE_WINDOW_HOURS:
            return {"kind": None,
                    "reason": (f"GW{review_due['id']} review held to merge into the main email "
                               f"due in {max(hours_until_main, 0):.1f}h")}
        return {"kind": "review", "event": int(review_due["id"]),
                "reason": f"GW{review_due['id']} points are final"}

    final_due = (already_sent(state, "main", event_id)
                 and hours_to_deadline <= FINAL_CHECK_HOURS
                 and lead - FINAL_CHECK_HOURS >= FINAL_CHECK_MIN_GAP_HOURS
                 and not already_sent(state, "final", event_id))
    if final_due:
        changed = [f for f in flags if f["player"] in squad_names]
        if changed:
            return {"kind": "final", "event": event_id, "deadline": deadline, "flags": changed,
                    "reason": f"{len(changed)} news flag(s) on your squad {hours_to_deadline:.1f}h out"}
        return {"kind": None, "reason": "final-check window, but nothing changed"}

    return {"kind": None,
            "reason": f"{hours_to_deadline:.1f}h to the GW{event_id} deadline; next email at {lead:.0f}h"}


# ── Content ────────────────────────────────────────────────────────────


def build_email(decision: dict, players: dict, events_by_id: dict) -> tuple[str, str]:
    """Returns (subject, html). Numbers come from the optimiser only."""
    strategy = load_strategy()
    overrides = load_overrides()
    squad, config = resolve_squad(players)
    starters, bench, captain = pick_team(squad)
    bank = num(config.get("bank"))
    free_transfers = int(config.get("free_transfers") or 1)

    flags_payload = load_json(DATA_DIR / "news_flags.json", {})
    flags = flags_payload.get("flags", [])
    squad_names = {p["name"] for p in squad}
    squad_flags = [f for f in flags if f["player"] in squad_names]

    price_payload = load_json(DATA_DIR / "price_watch.json", {})
    owned_movers = [m for m in price_payload.get("movers", []) if m["name"] in squad_names]

    kind = decision["kind"]
    event = decision.get("event")

    if kind.startswith("alert-"):
        flag = decision["flags"][0]
        subject = f"⚠️ FPL: {flag['player']} — {flag['affects']} concern"
        sections = [("what changed", render.render_news(decision["flags"])),
                    ("your XI as it stands", render.render_team(starters, bench, captain))]
        return subject, render.render_email(
            verdict=f"{flag['player']}: {flag['concern'][:110]}",
            subtitle="Breaking news on a player in your squad",
            sections=sections,
            footer_note="Sent outside the normal schedule because this affects your team.")

    if kind == "review":
        name = events_by_id.get(event, {}).get("name", f"Gameweek {event}")
        average = events_by_id.get(event, {}).get("average_entry_score", "?")
        subject = f"FPL review — {name}"
        sections = [
            ("how it went", render.card(
                f'<div style="font-size:14px;color:#374151;">{render.esc(name)} is final. '
                f'The average manager scored {render.esc(average)}.</div>'
                f'<div style="font-size:12px;color:#6b7280;margin-top:8px;">'
                f'Once your team id is configured, this section reports your score, rank movement '
                f'and points left on the bench.</div>')),
            ("your squad now", render.render_team(starters, bench, captain)),
            ("news on your players", render.render_news(squad_flags)),
        ]
        return subject, render.render_email(
            verdict=f"{name} is done.",
            subtitle="Review of the gameweek just finished",
            sections=sections, footer_note="Sent when the gameweek's points went final.")

    # main, and final-check
    transfers = suggest_transfers(squad, players, bank, free_transfers, overrides)
    gap = ownership_gap(squad, players)
    shortlist = sorted(starters, key=lambda p: -p["total"])

    if kind == "final":
        subject = f"FPL final check — GW{event} deadline in ~3h"
        verdict = f"{len(decision['flags'])} late update(s) on your squad."
        sections = [("what changed", render.render_news(decision["flags"])),
                    ("your XI", render.render_team(starters, bench, captain)),
                    ("captain", render.render_captain(shortlist))]
        return subject, render.render_email(
            verdict=verdict, subtitle="Last look before the deadline",
            sections=sections, footer_note="Only sent because something changed after the main email.")

    best = transfers[0] if transfers else None
    if best and best["gain"] > (HIT_MARGIN if best["hit"] else 0.5):
        verdict = f"{best['out']['name']} → {best['in']['name']}, captain {captain['name']}."
    else:
        verdict = f"Roll the transfer. Captain {captain['name']}."

    deadline = decision.get("deadline")
    when = deadline.strftime("%a %d %b, %H:%M UTC") if deadline else "the deadline"
    subject = f"FPL GW{event} — {verdict[:60]}"
    sections = [
        ("transfers", render.render_transfers(transfers, free_transfers, bank)),
        ("captain", render.render_captain(shortlist)),
        ("starting XI", render.render_team(starters, bench, captain)),
        ("news the numbers can't see", render.render_news(squad_flags)),
        ("price watch", render.render_prices(owned_movers)),
        ("template gap", render.render_gap(gap)),
    ]
    if decision.get("merge_review"):
        sections.insert(0, ("last gameweek", render.card(
            f'<div style="font-size:14px;color:#374151;">GW{decision["merge_review"]} is final. '
            f'Merged into this email because the two would otherwise have landed together.</div>')))

    return subject, render.render_email(
        verdict=verdict,
        subtitle=f"GW{event} deadline {when} · risk setting {strategy['risk']:.2f}",
        sections=sections,
        footer_note=f"Lead time {decision.get('lead', 36):.0f}h, scaled to a "
                    f"{decision.get('gap_days', 7):.1f}-day gap between deadlines.")


# ── Sending ────────────────────────────────────────────────────────────


def send_email(subject: str, html_body: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    recipient = os.environ.get("FPL_EMAIL_TO", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not set")
    if not recipient:
        raise RuntimeError("FPL_EMAIL_TO is not set (kept in GitHub Secrets, never in the repo)")
    response = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": EMAIL_FROM, "to": [recipient], "subject": subject, "html": html_body},
        timeout=60,
    )
    if response.status_code >= 400:
        # raise_for_status() discards the body, which is where Resend puts the
        # actual reason -- and its 403s are usually a specific, fixable rule
        # (most often: the free shared sender can only deliver to the address
        # that owns the Resend account). Losing that message turns a
        # two-minute fix into a guessing exercise.
        detail = response.text[:500]
        raise RuntimeError(f"Resend returned HTTP {response.status_code}: {detail}")
    print(f"[agent] sent '{subject}' (id {response.json().get('id', '?')})")


def record(state: dict, decision: dict, subject: str, now: datetime) -> None:
    state["sent"].append({
        "kind": decision["kind"], "event": decision.get("event"),
        "at": now.isoformat(), "subject": subject,
    })
    if decision.get("merge_review"):
        state["sent"].append({
            "kind": "review", "event": decision["merge_review"],
            "at": now.isoformat(), "subject": subject + " (merged)",
        })
    state["sent"] = state["sent"][-200:]
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    history = load_json(HISTORY_PATH, {"emails": []})
    history["emails"].append({"at": now.isoformat(), "kind": decision["kind"],
                              "event": decision.get("event"), "subject": subject,
                              "reason": decision.get("reason", "")})
    history["emails"] = history["emails"][-500:]
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def run(dry_run: bool, force: str | None) -> None:
    events = read_csv("events.csv")
    events_by_id = {int(e["id"]): e for e in events}
    players, _ = load_projections()
    state = load_state()
    now = datetime.now(timezone.utc)

    flags = load_json(DATA_DIR / "news_flags.json", {}).get("flags", [])
    squad, _ = resolve_squad(players)
    squad_names = {p["name"] for p in squad}

    decision = decide(events, state, now, flags, squad_names)
    if force:
        upcoming = [e for e in events if parse_time(e["deadline_time"]) and parse_time(e["deadline_time"]) > now]
        event_id = int(upcoming[0]["id"]) if upcoming else 1
        decision = {"kind": force, "event": event_id, "reason": f"forced ({force})",
                    "deadline": parse_time(upcoming[0]["deadline_time"]) if upcoming else None,
                    "gap_days": 7.0, "lead": 36.0,
                    "flags": [f for f in flags if f["player"] in squad_names] or
                             [{"player": "(none)", "concern": "forced run, no live flags",
                               "severity": "low", "affects": "other", "source": ""}]}

    print(f"[agent] decision: {decision['kind'] or 'nothing to send'} — {decision['reason']}")
    if not decision["kind"]:
        return

    subject, html_body = build_email(decision, players, events_by_id)
    if dry_run:
        out = DATA_DIR / "last_email_preview.html"
        out.write_text(html_body, encoding="utf-8")
        print(f"[agent] DRY RUN — subject: {subject}")
        print(f"[agent] preview written to {out.relative_to(BASE_DIR)}")
        return

    send_email(subject, html_body)
    record(state, decision, subject, now)


def send_failure_email(reason: str) -> None:
    """A silent failure before a deadline is the worst outcome -- no email
    must always mean 'check the logs', never 'nothing to say'."""
    try:
        send_email("⚠️ FPL agent failed", f"<pre>{render.esc(reason)}</pre>")
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] failure email also failed: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="decide and preview, send nothing")
    parser.add_argument("--force", choices=["main", "review", "final"], help="send this regardless")
    parser.add_argument("--test-email", action="store_true", help="prove the Resend path")
    args = parser.parse_args()

    try:
        if args.test_email:
            send_email("FPL agent — test", render.render_email(
                verdict="The FPL agent can reach your inbox.",
                subtitle="Test message",
                sections=[("", render.card('<div style="font-size:14px;color:#374151;">'
                                           'Real emails will carry transfers, captain, XI, news and '
                                           'price warnings.</div>'))],
                footer_note="Sent by --test-email."))
            return
        run(dry_run=args.dry_run, force=args.force)
    except Exception:
        reason = traceback.format_exc()
        print(reason, file=sys.stderr)
        if not args.dry_run:
            send_failure_email(reason)
        sys.exit(1)


if __name__ == "__main__":
    main()
