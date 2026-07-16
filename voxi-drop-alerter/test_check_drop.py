#!/usr/bin/env python3
"""Offline tests for check_drop.py using fixture HTML (the sandbox can't
reach voxi.co.uk). Walks the monthly lifecycle: closed -> teaser -> live ->
closed -> next month live again, plus the unknown/changed safety net and
fetch-failure warning.

Run: python test_check_drop.py
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import check_drop as cd

PAGE_CLOSED = """<html><title>VOXI Drop</title><body>
<h1>VOXI Drop</h1><p>You've missed this month's drop. Come back next month!</p>
<a href="/plans">See plans</a></body></html>"""

PAGE_TEASER = """<html><title>VOXI Drop</title><body>
<h1>VOXI Drop</h1><p>Get ready - dropping tomorrow!</p></body></html>"""

PAGE_LIVE = """<html><title>VOXI Drop</title><body>
<h1>VOXI Drop</h1><p>It's here!</p>
<a href="/account/login">Sign in to claim</a>
<button>Claim now</button></body></html>"""

PAGE_UNKNOWN_A = """<html><title>VOXI Drop</title><body>
<h1>VOXI Drop</h1><p>Something something rewards.</p></body></html>"""

PAGE_UNKNOWN_B = """<html><title>VOXI Drop</title><body>
<h1>VOXI Drop</h1><p>Totally new mystery wording. Keep your eyes peeled!</p></body></html>"""

PAGE_AMBIGUOUS = """<html><body><p>Claim now!</p><p>you've missed it</p></body></html>"""

sent: list[tuple[str, str, str]] = []
current_page: list = [None]

cd.fetch_html = lambda url: current_page[0]


def fake_send(title, message, priority="default", tags=""):
    sent.append((priority, title, message))
    print(f"  [notify/{priority}] {title}")


cd.send_notification = fake_send

failures = 0


def check(label, cond):
    global failures
    print(f"{'PASS' if cond else 'FAIL'}: {label}")
    if not cond:
        failures += 1


def run_with(page):
    current_page[0] = page
    cd.run(dry_run=False, recon_mode=False)


with tempfile.TemporaryDirectory() as tmp:
    cd.STATE_FILE = Path(tmp) / "state.json"

    print("\n-- classification --")
    check("closed page -> closed", cd.classify(PAGE_CLOSED)[0] == "closed")
    check("teaser page -> teaser", cd.classify(PAGE_TEASER)[0] == "teaser")
    check("live page -> live", cd.classify(PAGE_LIVE)[0] == "live")
    check("mystery page -> unknown", cd.classify(PAGE_UNKNOWN_A)[0] == "unknown")
    check("live+closed wording together -> not live", cd.classify(PAGE_AMBIGUOUS)[0] != "live")

    print("\n-- month 1: closed baseline, no alerts --")
    run_with(PAGE_CLOSED)
    run_with(PAGE_CLOSED)
    check("no alerts while closed", not sent)

    print("\n-- teaser appears --")
    run_with(PAGE_TEASER)
    check("one default teaser alert", len(sent) == 1 and sent[0][0] == "default" and "coming soon" in sent[0][1])
    run_with(PAGE_TEASER)
    check("teaser not repeated", len(sent) == 1)
    sent.clear()

    print("\n-- drop goes LIVE --")
    run_with(PAGE_LIVE)
    check("one urgent LIVE alert", len(sent) == 1 and sent[0][0] == "urgent" and "LIVE" in sent[0][1])
    run_with(PAGE_LIVE)
    run_with(PAGE_LIVE)
    check("LIVE not repeated while page stays live", len(sent) == 1)
    sent.clear()

    print("\n-- drop closes again, still same month: quiet --")
    run_with(PAGE_CLOSED)
    check("no alert on live->closed", not sent)
    run_with(PAGE_LIVE)
    check("no second LIVE alert same month", not sent)
    run_with(PAGE_CLOSED)

    print("\n-- next month: LIVE alerts again --")
    real_now = cd.datetime.now

    class NextMonth(datetime):
        @classmethod
        def now(cls, tz=None):
            n = real_now(tz)
            return n.replace(year=n.year + (1 if n.month == 12 else 0), month=1 if n.month == 12 else n.month + 1, day=1)

    cd.datetime = NextMonth
    run_with(PAGE_LIVE)
    cd.datetime = datetime
    check("new month -> LIVE alert fires again", len(sent) == 1 and "LIVE" in sent[0][1])
    sent.clear()

    print("\n-- unknown wording change safety net --")
    cd.STATE_FILE = Path(tmp) / "state2.json"
    run_with(PAGE_UNKNOWN_A)
    check("first unknown page sets baseline, no alert", not sent)
    run_with(PAGE_UNKNOWN_B)
    check("changed unknown wording -> one default alert", len(sent) == 1 and "changed" in sent[0][1])
    run_with(PAGE_UNKNOWN_B)
    check("change alert not repeated", len(sent) == 1)
    sent.clear()

    print("\n-- fetch failures then recovery --")
    cd.STATE_FILE = Path(tmp) / "state3.json"
    run_with(PAGE_CLOSED)
    for _ in range(cd.FETCH_FAILURES_BEFORE_ALERT):
        run_with(None)
    check("one high-priority blind warning", len(sent) == 1 and sent[0][0] == "high")
    run_with(None)
    check("warning not repeated", len(sent) == 1)
    run_with(PAGE_CLOSED)
    check("recovery notice once back online", len(sent) == 2 and "back online" in sent[1][1])
    sent.clear()

    print("\n-- dry run --")
    cd.STATE_FILE = Path(tmp) / "state4.json"
    current_page[0] = PAGE_LIVE
    cd.run(dry_run=True, recon_mode=False)
    check("dry run sends nothing", not sent)
    check("dry run writes no state", not cd.STATE_FILE.exists())

    print("\n-- recon mode --")
    cd.run(dry_run=False, recon_mode=True)
    check("recon writes no state", not cd.STATE_FILE.exists())

print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
sys.exit(1 if failures else 0)
