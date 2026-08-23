# Chelsea home-ticket watch — PLAN

**Status: BUILT (2026-08-22).** Steps 1-4 of the build order are done: the
watcher, its tests, and the workflow all exist and were verified against the
live feed. Remaining: step 5 (cron-job.org trigger, needs Harry) and step 6
(watch one real window open). See `README.md` for how it actually works --
this file is the design record and the reasoning behind it.

Goal: alert Harry when a **new Stamford Bridge home fixture appears** on the
men's ticket page, and when its **ticket application window (ballot)** opens.

Scope agreed 2026-08-22:

- **Home games at Stamford Bridge only**, men's team only.
- **All competitions** (Premier League, Carabao Cup, cups, Europe).
- Membership: **True Blue (£60 paid member)** — not a season ticket holder.
- Primary trigger: **a new fixture being added to the page**, because that
  means the sales window is about to open.
- Also wanted: the **sale/ballot dates** that sit behind "View Details",
  which the agent can read without navigating to the enter-ballot page.

---

## 1. Recon — DONE, and it went much better than expected

### 1.1 There is a clean public JSON API. No HTML scraping needed.

The ticket list on `/en/tickets/mens-tickets` is **not** in the page HTML —
it is a React app that fetches its data client-side. Traced through the
webpack bundle (`runtime.js` chunk map → 152 lazy chunks → the
`fixturesAndResults` service) to find the real endpoint:

```
GET https://www.chelseafc.com/en/api/fixtures/tickets?pageId=4nn76TbMHeor2gxBhp09a8
```

- `pageId` is required (a 400 with `"The pageId field is required."` without
  it). The value is the Contentful entry id of the men's tickets page and is
  published in the page HTML as `"pageId":"4nn76TbMHeor2gxBhp09a8"` — so it
  can be re-read at runtime rather than hardcoded, in case it ever changes.
- **Verified live: HTTP 200, ~50 KB of structured JSON, reachable from this
  dev sandbox.** No auth, no cookies, no browser, no Playwright.
- Optional params that work: `pageSize` (honoured), `competitionId` (filters).
  `page`/`currentPage` are ignored — but `totalItems` is currently **5**, so
  there is nothing to paginate. Chelsea only lists fixtures once ticket info
  exists, which is exactly why "a new fixture appeared" is the right signal.

**This replaces the entire keyword-classifier design from the first draft.**
No guessing at wording, no `fingerprint()` safety net needed as the primary
mechanism — we diff structured data instead. Far more reliable.

### 1.2 The response shape

```jsonc
{
  "items": [                          // grouped by month
    { "monthName": "August", "year": 2026, "items": [
        { "id": "6rixcGFDEWH5V98iSqSPLa",
          "fixture": {
            "home": { "name": "Chelsea", "isOpposition": false },
            "away": { "name": "Hull City", "isOpposition": true },
            "competition": "Premier League",
            "date": "Sat 12 Sept 2026", "time": "3:00 pm",
            "venue": "Stamford Bridge"
          },
          "tickets": [                // one entry per sale window
            { "title": "Ticket Application Window Open ",
              "label": "On-sale: Aug 21, 2026",
              "moreInfoLink": { "url": "https://www.eticketing.co.uk/chelseafc" }
              // note: NO "status" key at all == currently live
            }
          ],
          "fullTicketInfoLink": { "content": "<html blob>" }   // the View Details dropdown
        }
    ]}
  ],
  "competitions": [ /* filter options incl. entry ids */ ],
  "pagination": { "totalItems": 5, "itemsPerPage": 6 }
}
```

Key field semantics, read off the live data:

| Signal | How to read it |
|---|---|
| Home game? | `fixture.home.isOpposition == false` → Chelsea at home. Also cross-check `venue == "Stamford Bridge"`. |
| Stable fixture key | `id` (Contentful entry id, e.g. `6rixcGFDEWH5V98iSqSPLa`). **Use this, not the team name** — kills risk R4 from the first draft entirely. |
| Window is LIVE | the ticket entry has **no `status` key** |
| Window closed | `status.text == "Off Sale"` (colour `red`) |
| Gone | `status.text == "Sold Out"` (colour `red`) |
| Who/when | `title` + `label` (`"On-sale: Aug 21, 2026"`) |
| Full dates | `fullTicketInfoLink.content` — the View Details HTML |

`fullTicketInfoLink.content` is double-HTML-escaped; unescape twice, strip
tags, then regex the window open/close lines. It reliably contains lines of
the form:

```
Ticket application window opens – Friday 21 August 12pm
Ticket application window closes – Wednesday 26 August 12pm
```

That gives us a real **closing deadline**, which is included in the body of
the "applications open" alert.

### 1.3 Answering Q2 — Chelsea does not use the word "ballot"

It is called a **"Ticket Application Window"**. Verbatim from the live Hull
City entry:

> Home tickets for members for men's team matches are now allocated through a
> new application process. For each home match, eligible members have a
> designated application window during which they can apply for tickets.
> [...] The Ticket application window will be open for **True Blue+, True
> Blue, CFC Blue, and Junior Blue** members.

So **True Blue is explicitly eligible**. Applying early confers no advantage
(the text says so), which means the alert's job is "don't miss the window",
not "be first" — so a 30-minute poll is comfortably fast enough.

### 1.4 Live data as of this recon (5 fixtures listed)

| | Fixture | Comp | Date | Window state |
|---|---|---|---|---|
| away | Fulham | PL | Mon 24 Aug | Sold Out |
| **HOME** | Luton Town | Carabao | Thu 27 Aug | Members window **Off Sale** (was 18 Aug); extra-ticket sale live |
| **HOME** | Brighton | PL | Sun 30 Aug | Ticket Exchange + Club Chelsea live |
| away | Arsenal | PL | Sun 6 Sept | on-sale 25/26 Aug |
| **HOME** | Hull City | PL | Sat 12 Sept | **Ticket Application Window OPEN** |

> ⚠️ **Time-sensitive, unrelated to the build:** the Hull City application
> window is **open right now** and **closes Wednesday 26 August at 12pm**.
> True Blue is eligible. That is the exact fixture you mentioned — worth
> applying before the agent exists.

---

## 2. Architecture (revised — much simpler than draft 1)

```
chelsea_tickets/
├── PLAN.md                   <- design record (this file)
├── README.md                 <- how it works day to day
├── requirements.txt
├── pytest.ini
├── check_tickets.py          <- CLI entry, fetch-failure handling, wiring
├── chelsea/
│   ├── api.py                <- feed client, pageId resolution, retries
│   ├── model.py              <- feed JSON -> typed home fixtures
│   ├── detect.py             <- snapshot diff -> alerts
│   ├── state.py              <- committed snapshot + audit trail
│   └── notify.py             <- ntfy
├── fixtures/                 <- a real captured API response, used by tests
└── tests/                    <- 85 tests, 96% coverage
```

Plus `.github/workflows/chelsea-tickets-watch.yml` at repo root.

### Detection = diff, not classification

Each run:

1. `GET /en/api/fixtures/tickets?pageId=…` (pageId re-read from the page HTML).
2. Keep entries where `isOpposition == false` AND the venue is Stamford Bridge
   (the venue guard stops a Wembley cup final counting as a home game).
3. Keep only sale windows a member can use -- anything mentioning
   *member*/*application*/*ballot*, minus Ticket Exchange, Club Chelsea and
   hospitality.
4. Diff against the snapshot in `state.json`.
5. Alert, save the new snapshot, let Actions commit it back.

**Written from scratch, not adapted from `voxi-drop-alerter/`.** Nothing is
inherited from it -- see the decisions section below for why.

### Alerts

| Notification | Priority | Trigger |
|---|---|---|
| **New home fixture listed** | default | A home `fixture.id` not in the snapshot. Carries the application dates when already published. |
| **Ticket applications OPEN** | urgent | A members' window transitions to open. Carries the closing deadline. |
| **New fixture + applications OPEN** | urgent | Both at once, as ONE notification rather than two. |
| **Watch can't reach the site** | high | 3 consecutive fetch failures. |
| **Watch needs attention** | high | The feed changed shape and can no longer be parsed. |

Closures, sold-outs, away games, Ticket Exchange and hospitality are tracked
in state but never alerted on.

### Scheduling

- Every **30 minutes**. Primary trigger = cron-job.org
  hitting `workflow_dispatch` (same PAT as dock-alerter); GitHub's native
  `schedule: */30 * * * *` as the unreliable backup.
- **MUST join `concurrency: group: main-git-writer, cancel-in-progress: false`**
  — repo rule from root `CLAUDE.md`. Non-negotiable, prevents push races.
- Reuses the existing `NTFY_TOPIC` secret. No new phone setup.

30-minute polling is polite here: one ~50 KB JSON GET, ~48 requests/day, no
login, no purchase flow, public endpoint the site itself calls on every page
view. Well within "behaving like a browser".

---

## 3. Risks (revised)

**R1 — RESOLVED.** The old worry was the `401` on `eticketing.co.uk`. Moot:
the public JSON API carries everything needed, including window open/close
dates. **No credentials, no login automation, no ToS grey area.** The agent
never touches the buying platform — it just links you to it. Still no
auto-buying, and I'd still push back if asked.

**R2 — REDUCED.** No keyword tuning needed. Residual risk is Chelsea changing
the API response shape, covered by the "feed shape changed" safety-net alert.

**R3 — public repo.** `hsimmonds01/Claude` is **PUBLIC** and git history is
permanent. Nothing personal is required by this design: no membership number,
no login, no email. The committed `state.json` will contain fixture ids and
public sale dates only. The one mild exposure is that it reveals which home
games you are watching, i.e. where you may be on a given evening — flagging
per the repo rule rather than assuming it's fine. Say the word and I can keep
the snapshot to opaque hashes instead.

**R4 — RESOLVED.** Fixture-name normalisation was going to be fiddly; the API
gives a stable `id` per fixture, so it's a dictionary key lookup.

**R5 — NEW, minor.** `pageId` could change if Chelsea rebuilds the page. Fix:
re-read it from the page HTML each run and only fall back to the known value.
Cheap insurance, one extra request.

---

## 4. Build order

1. `check_tickets.py --recon`: fetch, parse, print the home-fixture table.
   Save real responses into `fixtures/` as test data.
2. Diff engine + `fullTicketInfoLink` open/close-date parser, with offline
   tests against the saved responses.
3. ntfy wiring, verified end-to-end with `--test-notification`.
4. Workflow YAML (with the `main-git-writer` concurrency group).
5. cron-job.org trigger — numbered click-by-click instructions for you.
6. Watch one real window open (next fixture added) and confirm it fires.

Steps 1–3 are the bulk and are all now straightforward, because the data is
structured. Ships via the standard branch → PR → squash-merge cycle.

---

## 5. Decisions taken (2026-08-22)

**Poll interval: 30 minutes.** Harry: "there's no rush... I just need to know
when it opens." Chelsea's own copy confirms applying early in a window gives
no advantage.

**Ticket Exchange: OUT.** Harry has never got a ticket on it -- "there are
never any tickets on it". Filtered out entirely rather than tracked silently.

**Alerts: two events only.** A new home fixture appearing, and an application
window opening. Closures and sold-outs are tracked in state but never sent.
Two operational alerts remain (feed unreachable, feed shape changed) because
a watcher that breaks silently is indistinguishable from one with no news --
that is the failure mode that matters.

**Not built on the voxi-drop-alerter pattern.** The first draft of this plan
proposed reusing it. Harry pushed back -- the keyword-classifier approach
never reliably worked there (its `state.json` shows only `changed` nudges,
never a successful live detection). Once the JSON API was found, keyword
classification became unnecessary: this is a structured diff instead.
