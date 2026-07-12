#!/usr/bin/env python3
"""Offline tests for check_tickets.py using fixture HTML, since the sandbox
(and possibly some CI environments) can't reach the real site. Stubs the
network layer and walks the full lifecycle: seed -> nothing -> event appears
-> tickets appear -> sold out -> site unreachable -> recovery.

Run: python test_check_tickets.py
"""

import sys
import tempfile
from pathlib import Path

import check_tickets as ct

HUB_BEFORE = """
<html><body>
<a href="/events-btb/world-cup-round-of-16-7-july"><h3>World Cup 2026: Round of 16 - Argentina V Egypt</h3></a>
<a href="/events-btb/england-norway-11-july"><h3>World Cup 2026: England V Norway</h3></a>
<a href="/events-btb/world-cup-england-panama-27-jun"><h3>World Cup 2026: England Vs Panama</h3></a>
</body></html>
"""

SEMI_LINK = '<a href="/events-btb/world-cup-semi-final-england-argentina-15-july"><h3>World Cup 2026: Semi Final - England V Argentina</h3></a>'
HUB_WITH_SEMI = HUB_BEFORE.replace("</body>", SEMI_LINK + "</body>")

OTHER_NEW_LINK = '<a href="/events-btb/mystery-event"><h3>Big Screen Special</h3></a>'
HUB_WITH_OTHER = HUB_BEFORE.replace("</body>", OTHER_NEW_LINK + "</body>")

EVENT_NO_TICKETS = "<html><body><h1>England V Argentina</h1><p>Tickets coming soon</p></body></html>"
EVENT_ON_SALE = '<html><body><h1>England V Argentina</h1><a href="https://dice.fm/event/abc123-england-argentina">Book now</a></body></html>'
EVENT_SOLD_OUT = "<html><body><h1>England V Argentina</h1><p>SOLD OUT</p></body></html>"

sent: list[tuple[str, str, str]] = []
site: dict[str, str | None] = {}


def fake_fetch(url: str) -> str | None:
    return site.get(url)


def fake_send(title: str, message: str, priority: str = "default", tags: str = "") -> None:
    sent.append((priority, title, message))
    print(f"  [notify/{priority}] {title}")


ct.fetch_html = fake_fetch
ct.send_notification = fake_send

failures = 0


def check(label: str, cond: bool) -> None:
    global failures
    print(f"{'PASS' if cond else 'FAIL'}: {label}")
    if not cond:
        failures += 1


def set_site(hub: str | None, event: str | None = None) -> None:
    site.clear()
    site[ct.HUB_URLS[0]] = hub
    site[ct.HUB_URLS[1]] = hub  # same content on both watched pages is fine
    if event is not None:
        site[ct.BASE_URL + "/events-btb/world-cup-semi-final-england-argentina-15-july"] = event


with tempfile.TemporaryDirectory() as tmp:
    ct.STATE_FILE = Path(tmp) / "state.json"

    print("\n-- target matching --")
    check("semi-final slug is a target", ct.is_target("/events-btb/world-cup-semi-final-england-argentina-15-july Semi Final"))
    check("plain 'England V Argentina' text is a target", ct.is_target("/events-btb/x England V Argentina"))
    check("old Argentina v Egypt event is NOT a target", not ct.is_target("/events-btb/world-cup-round-of-16-7-july World Cup 2026: Round of 16 - Argentina V Egypt"))
    check("England v Norway is NOT a target", not ct.is_target("/events-btb/england-norway-11-july World Cup 2026: England V Norway"))

    print("\n-- run 1: seeding, existing events must not alert --")
    set_site(HUB_BEFORE)
    ct.run(dry_run=False, force=True)
    check("no notifications on seed run", not sent)
    check("seeded 3 known urls", len(ct.State.load(ct.STATE_FILE).known_event_urls) == 3)

    print("\n-- run 2: no change --")
    ct.run(dry_run=False, force=True)
    check("still no notifications", not sent)

    print("\n-- run 3: unrelated new event appears --")
    set_site(HUB_WITH_OTHER)
    ct.run(dry_run=False, force=True)
    check("one default-priority heads-up", len(sent) == 1 and sent[0][0] == "default")
    ct.run(dry_run=False, force=True)
    check("not repeated on next run", len(sent) == 1)
    sent.clear()

    print("\n-- run 4: semi-final page appears, no tickets yet --")
    set_site(HUB_WITH_SEMI, EVENT_NO_TICKETS)
    ct.run(dry_run=False, force=True)
    check("one urgent 'event live' alert", len(sent) == 1 and sent[0][0] == "urgent" and "LIVE" in sent[0][1])
    ct.run(dry_run=False, force=True)
    check("not repeated while tickets still absent", len(sent) == 1)
    sent.clear()

    print("\n-- run 5: tickets go on sale --")
    set_site(HUB_WITH_SEMI, EVENT_ON_SALE)
    ct.run(dry_run=False, force=True)
    check("one urgent ON SALE alert", len(sent) == 1 and sent[0][0] == "urgent" and "ON SALE" in sent[0][1])
    check("alert contains the dice.fm booking link", "dice.fm" in sent[0][2])
    ct.run(dry_run=False, force=True)
    check("on-sale alert not repeated", len(sent) == 1)
    sent.clear()

    print("\n-- run 6: sold out --")
    set_site(HUB_WITH_SEMI, EVENT_SOLD_OUT)
    ct.run(dry_run=False, force=True)
    check("one default sold-out notice", len(sent) == 1 and sent[0][0] == "default" and "SOLD OUT" in sent[0][1])
    sent.clear()

    print("\n-- fresh state: event page and tickets appear in the SAME run --")
    ct.STATE_FILE = Path(tmp) / "state2.json"
    set_site(HUB_BEFORE)
    ct.run(dry_run=False, force=True)  # seed
    set_site(HUB_WITH_SEMI, EVENT_ON_SALE)
    ct.run(dry_run=False, force=True)
    check("single combined urgent ON SALE alert (no separate 'event live')", len(sent) == 1 and "ON SALE" in sent[0][1])
    sent.clear()

    print("\n-- fresh state: target already on page at seed time, tickets on sale --")
    ct.STATE_FILE = Path(tmp) / "state3.json"
    set_site(HUB_WITH_SEMI, EVENT_ON_SALE)
    ct.run(dry_run=False, force=True)
    check("seed run still alerts ON SALE for the target", len(sent) == 1 and "ON SALE" in sent[0][1])
    sent.clear()

    print("\n-- site unreachable, then recovery --")
    ct.STATE_FILE = Path(tmp) / "state4.json"
    set_site(HUB_BEFORE)
    ct.run(dry_run=False, force=True)  # seed
    set_site(None)
    for _ in range(ct.FETCH_FAILURES_BEFORE_ALERT):
        ct.run(dry_run=False, force=True)
    check("one high-priority blind-monitor warning after 3 failures", len(sent) == 1 and sent[0][0] == "high")
    ct.run(dry_run=False, force=True)
    check("warning not repeated on 4th failure", len(sent) == 1)
    set_site(HUB_BEFORE)
    ct.run(dry_run=False, force=True)
    check("recovery notice sent once back online", len(sent) == 2 and "back online" in sent[1][1])
    sent.clear()

    print("\n-- dry run sends nothing and writes no state --")
    ct.STATE_FILE = Path(tmp) / "state5.json"
    set_site(HUB_WITH_SEMI, EVENT_ON_SALE)
    ct.run(dry_run=True, force=True)
    check("dry run sent nothing", not sent)
    check("dry run wrote no state file", not ct.STATE_FILE.exists())

print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
sys.exit(1 if failures else 0)
