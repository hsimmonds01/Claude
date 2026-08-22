#!/usr/bin/env python3
"""Watch Chelsea's men's ticket page for home games at Stamford Bridge.

Alerts on exactly two things:

  1. A new home fixture appearing on the ticket page (a sale is coming).
  2. A members' ticket application window -- the ballot -- opening.

Data comes from Chelsea's own public JSON feed, the one the website itself
calls: GET /en/api/fixtures/tickets?pageId=...  No login, no scraping, no
contact with the eticketing purchase platform.

Usage:
    python check_tickets.py                  # real check, real notifications
    python check_tickets.py --dry-run        # decide + print, no send, no state write
    python check_tickets.py --recon          # print what the feed says right now
    python check_tickets.py --test-notification   # labelled test alert
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chelsea import notify
from chelsea.api import FetchError, fetch_ticket_feed
from chelsea.detect import detect
from chelsea.model import FeedError, parse_home_fixtures
from chelsea.state import State

STATE_FILE = Path(__file__).parent / "state.json"

# Below this many consecutive failures, a blip is just a blip. At it, the
# watcher is genuinely blind and silence would be indistinguishable from
# "no news", which is the failure mode that matters for a watcher.
FETCH_FAILURES_BEFORE_ALERT = 3


def _report_fixtures(fixtures: list) -> None:
    print(f"home fixtures at Stamford Bridge: {len(fixtures)}")
    for fixture in fixtures:
        print(f"\n  {fixture.describe}")
        print(f"    id: {fixture.id}")
        if fixture.application_opens or fixture.application_closes:
            print(f"    applications: {fixture.application_opens or '?'}"
                  f"  ->  {fixture.application_closes or '?'}")
        if not fixture.windows:
            print("    (no members' sale windows listed)")
        for window in fixture.windows:
            marker = "OPEN " if window.is_open else window.state
            print(f"    [{marker:8}] {window.title.strip()}  ({window.on_sale_label})")


def _handle_fetch_failure(state: State, exc: Exception, dry_run: bool) -> int:
    state.consecutive_fetch_failures += 1
    print(f"ERROR: {exc} ({state.consecutive_fetch_failures} consecutive)", file=sys.stderr)

    should_warn = (
        state.consecutive_fetch_failures >= FETCH_FAILURES_BEFORE_ALERT
        and not state.fetch_failure_notified
    )
    if should_warn:
        if dry_run:
            print("DRY RUN -- would send fetch-failure warning")
        else:
            notify.send(
                "Chelsea ticket watch can't reach the site",
                f"{state.consecutive_fetch_failures} runs in a row failed to load "
                "Chelsea's ticket feed, so the watch is blind rather than quiet. "
                "Worth checking the page yourself.",
                priority="high",
                tags="warning",
            )
            state.fetch_failure_notified = True
    if not dry_run:
        state.save(STATE_FILE)
    return 1


def _announce_recovery(state: State, dry_run: bool) -> None:
    if not state.fetch_failure_notified:
        state.consecutive_fetch_failures = 0
        return
    if dry_run:
        print("DRY RUN -- would send recovery notice")
    else:
        notify.send(
            "Chelsea ticket watch back online",
            "Reaching Chelsea's ticket feed again; watching as normal.",
            priority="default",
            tags="white_check_mark",
        )
    state.fetch_failure_notified = False
    state.consecutive_fetch_failures = 0


def run(dry_run: bool = False, recon: bool = False) -> int:
    state = State.load(STATE_FILE)

    try:
        payload = fetch_ticket_feed()
        fixtures = parse_home_fixtures(payload)
    except FetchError as exc:
        if recon:
            print(f"RECON: fetch FAILED -- {exc}")
            return 1
        return _handle_fetch_failure(state, exc, dry_run)
    except FeedError as exc:
        # A shape change is not a network problem: retrying will not fix it,
        # and it means the watcher can no longer see anything. Say so loudly.
        print(f"ERROR: {exc}", file=sys.stderr)
        if not dry_run and not recon:
            notify.send(
                "Chelsea ticket watch needs attention",
                f"Chelsea's ticket feed changed shape, so the watch has stopped "
                f"understanding it:\n{exc}",
                priority="high",
                tags="warning",
            )
        return 1

    if recon:
        _report_fixtures(fixtures)
        return 0

    _announce_recovery(state, dry_run)
    _report_fixtures(fixtures)

    if not state.is_seeded:
        print("\nFirst run: recording a baseline, no alerts sent.")
    else:
        alerts = detect(state.fixtures, fixtures, seeded=True)
        for alert in alerts:
            if dry_run:
                print(f"\nDRY RUN -- would send [{alert.priority}] {alert.title}\n{alert.message}")
                continue
            notify.send(alert.title, alert.message, priority=alert.priority, tags=alert.tags)
            # Recorded for audit only. Suppression is the snapshot diff's job:
            # gating on these keys as well would permanently swallow a window
            # that closes and re-opens (Chelsea's second-batch releases),
            # which is exactly the event Harry most wants to hear about.
            for key in alert.keys:
                state.mark_notified(key)
        if not alerts:
            print("\nNo changes worth alerting on.")

    state.snapshot(fixtures)
    state.prune_notified({fixture.id for fixture in fixtures})
    if not dry_run:
        state.save(STATE_FILE)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="decide and print, but send nothing and write no state")
    parser.add_argument("--recon", action="store_true",
                        help="print what the feed says right now; no alerts, no state")
    parser.add_argument("--test-notification", action="store_true",
                        help="send a labelled test alert through the real path")
    args = parser.parse_args()

    if args.test_notification:
        notify.send_test()
        return 0
    return run(dry_run=args.dry_run, recon=args.recon)


if __name__ == "__main__":
    sys.exit(main())
