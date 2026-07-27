"""Tests for the scheduling decision.

The scheduler is the part hardest to observe in production -- a bug shows up
as an email that silently never arrives, which looks identical to "nothing to
report". These check the cases that matter: midweek compression, merging, the
volume cap, and the override that must always get through.

Run: python test_schedule.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fpl_agent import decide, lead_hours

NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def event(id_, deadline, data_checked=False, name=None):
    return {"id": str(id_), "deadline_time": deadline.isoformat(),
            "data_checked": str(data_checked), "finished": str(data_checked),
            "name": name or f"Gameweek {id_}", "average_entry_score": "50"}


def state(sent=None):
    return {"sent": sent or []}


results = []


def check(label, condition, detail=""):
    results.append((label, condition, detail))
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not condition else ""))


print("lead time scales with congestion")
check("normal week -> 36h", lead_hours(7.0) == 36.0, f"got {lead_hours(7.0)}")
check("midweek round -> 24h", lead_hours(4.0) == 24.0, f"got {lead_hours(4.0)}")
check("festive crush -> 18h", lead_hours(2.0) == 18.0, f"got {lead_hours(2.0)}")

print("\nmain email fires inside the lead window, not before")
events = [event(1, NOW - timedelta(days=7)), event(2, NOW + timedelta(hours=30))]
d = decide(events, state(), NOW, [], set())
check("30h out on a 7-day gap -> main", d["kind"] == "main", d["reason"])

events = [event(1, NOW - timedelta(days=7)), event(2, NOW + timedelta(hours=48))]
d = decide(events, state(), NOW, [], set())
check("48h out on a 7-day gap -> nothing yet", d["kind"] is None, d["reason"])

print("\nmidweek compression: a 30h-out email must NOT fire on a 4-day gap")
events = [event(1, NOW - timedelta(days=4)), event(2, NOW + timedelta(hours=30))]
d = decide(events, state(), NOW, [], set())
check("30h out on a 4-day gap -> nothing (lead is 24h)", d["kind"] is None, d["reason"])
events = [event(1, NOW - timedelta(days=4)), event(2, NOW + timedelta(hours=20))]
d = decide(events, state(), NOW, [], set())
check("20h out on a 4-day gap -> main", d["kind"] == "main", d["reason"])

print("\nreview fires on data_checked, not on a weekday")
events = [event(1, NOW - timedelta(days=3), data_checked=True), event(2, NOW + timedelta(days=4))]
d = decide(events, state(), NOW, [], set())
check("finalised gameweek -> review", d["kind"] == "review", d["reason"])

events = [event(1, NOW - timedelta(days=3), data_checked=False), event(2, NOW + timedelta(days=4))]
d = decide(events, state(), NOW, [], set())
check("not yet finalised -> no review", d["kind"] is None, d["reason"])

print("\ncolliding emails merge instead of double-sending")
# A normal 7-day gap, so the lead is 36h and a 30h-out main email IS due.
# (Set this up with a 2-day gap and the lead becomes 18h, the main email
# isn't due yet, and the review is held rather than merged -- correct
# behaviour, but a different case, tested separately below.)
events = [event(1, NOW - timedelta(days=5, hours=18), data_checked=True),
          event(2, NOW + timedelta(hours=30))]
d = decide(events, state(), NOW, [], set())
check("review due within the main window -> merged",
      d["kind"] == "main" and d.get("merge_review") == 1, str(d))

# Compressed round: main not yet due, review deliberately held back so the
# two arrive as one email rather than two hours apart.
events = [event(1, NOW - timedelta(days=2), data_checked=True), event(2, NOW + timedelta(hours=30))]
d = decide(events, state(), NOW, [], set())
check("compressed round -> review held for the merge",
      d["kind"] is None and "held to merge" in d["reason"], d["reason"])

# Review due, but the main email is still days away: send the review alone.
events = [event(1, NOW - timedelta(days=2), data_checked=True), event(2, NOW + timedelta(days=5))]
d = decide(events, state(), NOW, [], set())
check("review due, main far off -> review alone", d["kind"] == "review", d["reason"])

print("\nnothing is ever sent twice")
events = [event(1, NOW - timedelta(days=7)), event(2, NOW + timedelta(hours=30))]
sent = [{"kind": "main", "event": 2, "at": (NOW - timedelta(hours=2)).isoformat()}]
d = decide(events, state(sent), NOW, [], set())
check("main already sent -> not resent", d["kind"] != "main", d["reason"])

print("\nvolume cap holds")
sent = [{"kind": "x", "event": 9, "at": (NOW - timedelta(hours=h)).isoformat()} for h in (1, 5, 9)]
events = [event(1, NOW - timedelta(days=7)), event(2, NOW + timedelta(hours=30))]
d = decide(events, state(sent), NOW, [], set())
check("3 sent in 72h -> capped", d["kind"] is None, d["reason"])

print("\nbut a high-severity flag on your player always gets through")
flags = [{"player": "Haaland", "severity": "high", "concern": "dropped", "affects": "minutes"}]
d = decide(events, state(sent), NOW, flags, {"Haaland"})
check("urgent flag overrides the cap", d["kind"] == "alert-Haaland", d["reason"])

d = decide(events, state(sent), NOW, flags, {"Salah"})
check("same flag, player not mine -> no override", d["kind"] is None, d["reason"])

flags_low = [{"player": "Haaland", "severity": "low", "concern": "rumour", "affects": "other"}]
d = decide(events, state(sent), NOW, flags_low, {"Haaland"})
check("low-severity flag does not override", d["kind"] is None, d["reason"])

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    raise SystemExit("FAILED: " + "; ".join(r[0] for r in failed))
