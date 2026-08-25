# Chelsea home-ticket watch

Watches Chelsea's [men's ticket page](https://www.chelseafc.com/en/tickets/mens-tickets)
for **home games at Stamford Bridge** and pushes a phone notification via
[ntfy.sh](https://ntfy.sh) — to the same topic the dock-alerter uses, so
there's no new app to install.

Built for a **True Blue member**, all competitions, men's team only.

## What you'll be notified about

| Notification | Priority | When |
|---|---|---|
| **New home fixture listed** | default | A Stamford Bridge fixture appears on the ticket page. Chelsea only lists a game once ticket info exists, so this means a sale window is coming. Includes the application dates if they're already published. |
| **Ticket applications OPEN** | urgent | A members' application window (the ballot) opens. Includes the closing deadline. |
| **New fixture + applications OPEN** | urgent | Both at once — a fixture first seen with its ballot already live. Sent as **one** notification, not two. |
| **Reminder: still OPEN** | urgent | A one-off follow-up, sent once, exactly 6 hours after an "applications OPEN" alert, if that window is still open — in case the original got missed. Never repeats beyond that single nudge. |
| **Watch can't reach the site** | high | 3 runs in a row failed to load the feed, i.e. the watch is blind rather than quiet. |
| **Watch needs attention** | high | Chelsea changed the feed's shape and the watcher can no longer read it. |

**Deliberately silent:** windows closing, selling out, away games, Ticket
Exchange, and Club Chelsea hospitality. All tracked in `state.json`, none
alerted on.

## How it decides

Chelsea's ticket page is a React app — the fixture list is **not** in the
page HTML. It's fetched from a public JSON endpoint that the site itself
calls:

```
GET https://www.chelseafc.com/en/api/fixtures/tickets?pageId=<cms-id>
```

No login, no cookies, no scraping, and **no contact with the eticketing
purchase platform** — the watcher just links you to it. `pageId` is read out
of the page HTML each run so a CMS rebuild can't silently blind the watch.

Each run fetches the feed, keeps home fixtures at Stamford Bridge, and diffs
them against the snapshot in `state.json`. Two field semantics do most of the
work, and both are easy to get backwards:

- **A sale window with no `status` object at all is OPEN.** Chelsea only
  attaches a status once a window has shut (`Off Sale`) or the allocation has
  gone (`Sold Out`).
- **Fixtures are keyed by their CMS `id`**, never by team name, so
  "Brighton" vs "Brighton & Hove Albion" never matters.

Windows are filtered to the ones a member can actually use — anything
mentioning *member*, *application* or *ballot*, minus Ticket Exchange, Club
Chelsea and hospitality. Season-ticket-holder-only windows are ignored.

The **first run is silent**: it records a baseline, because every fixture
already on the page would otherwise look brand new.

## The 6-hour reminder

A missed "applications OPEN" push is the one failure mode that actually
matters here, so the watcher gives it exactly one second chance: if a window
is still open 6 hours after that alert fired, it sends a single follow-up
reminder, then stays quiet regardless of how many more 30-minute polls run
before the window shuts. It only ever fires once per opening — closing and
re-opening (a second batch) starts the 6-hour clock over, same as the
primary alert. This is deliberately *not* a way to bypass Do Not Disturb —
it's a second chance at the same notification, not a louder one.

## How it runs

- `.github/workflows/chelsea-tickets-watch.yml`, every 30 minutes.
- GitHub's own scheduler runs late, so cron-job.org pinging the
  `workflow_dispatch` endpoint is the primary trigger; the schedule is a
  backup. Extra or overlapping runs are safe — an unchanged feed alerts on
  nothing.
- The workflow commits `state.json` back to `main` and joins the
  `main-git-writer` concurrency group so its push can't race a sibling
  project's.

There's no rush baked into the timing: Chelsea's own wording says applying
early in a window confers no advantage, so 30 minutes is plenty.

## Manual runs

```bash
cd chelsea_tickets
pip install -r requirements.txt

python check_tickets.py --recon              # what the feed says right now
python check_tickets.py --dry-run            # decide + print, no send, no state write
python check_tickets.py                      # real check, real notification
python check_tickets.py --test-notification  # labelled test alert down the real path
python -m pytest -q                          # offline tests
```

## Layout

| File | Does |
|---|---|
| `check_tickets.py` | CLI entry point, fetch-failure handling, wiring |
| `chelsea/api.py` | Talks to the feed; resolves `pageId`; retries |
| `chelsea/model.py` | Feed JSON → typed home fixtures; window states; ballot dates |
| `chelsea/detect.py` | Diffs against the snapshot, builds the alerts |
| `chelsea/state.py` | The committed snapshot + audit trail |
| `chelsea/notify.py` | ntfy |
| `fixtures/` | A real captured API response, used by the tests |

`state.json` holds fixture ids, opponents and public sale dates. No
membership number, no login, no personal data — the design never needs any.

## Gotchas worth knowing

- **Window titles can embed a running count in words, not just digits** —
  Chelsea's "additional tickets" route reads "...purchase an additional
  **two** tickets (maximum of **three**...)" and later "...**four**...
  (maximum of **five**...)" as more allocation opens up. `_window_key`
  strips digits *and* spelled-out numbers one–twelve for exactly this
  reason: seen live, an un-stripped word count made every increment look
  like a brand-new window opening, firing a stale "applications OPEN" alert
  days after the real ballot had already closed.
- **`fixture.application_closes` describes the ballot specifically, not
  every window on the fixture.** It's parsed from the "Ticket application
  window closes" wording, so a different window opening (e.g. the
  undated "additional tickets" route above) must not have that date
  attached to its alert — `_is_ballot_window` in `detect.py` gates on
  "application" appearing in the window's own title before including it.
- **`notified` in `state.json` is an audit log, not a mute list.** Suppression
  is the snapshot diff's job. Gating on those keys as well permanently
  swallowed a window that closes and re-opens (Chelsea's second-batch
  releases) — the exact event most worth hearing about. There's a regression
  test for it.
- **An unset GitHub Actions secret arrives as an empty string**, not as an
  absent variable, so `notify.py` uses `os.environ.get(...) or DEFAULT`
  rather than a `.get()` default. Don't "simplify" it.
- **The ntfy topic is never printed.** This repo is public, so Actions logs
  are public too.
- Chelsea's edge serves a 404 page to obvious bots, hence the browser
  `User-Agent`.
