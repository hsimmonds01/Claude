# Daily Discovery

An AI scout that emails you the coolest newly-announced things every
morning — ticket on-sales, limited-edition drops, London events, genuinely
interesting products — delivered as a styled digest via Resend. Free to
run, with no billing anywhere by default.

Perfect-catch examples this exists for: "The Odyssey" IMAX tickets going on
sale, the Wynwood x Strawberry Stellar Ottawa hat drop.

## How it works

```
cron-job.org (reliable scheduler, fires at your chosen hour)
   └─> GitHub Actions workflow_dispatch  (.github/workflows/daily-discovery.yml)
         └─> discover.py
               ├─ reads interests.md ........ your editable taste profile
               ├─ fetches free RSS/Atom feeds  (Google News searches + culture
               │    outlets) -- the free, no-billing research source
               ├─ asks Gemini (pro, falls back to flash) to pick the best
               │    finds from those headlines + the taste profile
               │    (optionally ALSO live-searches -- see "Turning on live
               │    search" below; off by default)
               ├─ filters vs state.json ..... never repeats a find (fuzzy match)
               ├─ emails via Resend ......... styled HTML digest
               └─ archives to history.json .. feeds dashboard.html (the UI)
```

GitHub's native schedule (07:20 UTC) is kept as a backup net only — it has
run >1hr late on this repo. The once-per-day guard in `state.json` means
duplicate triggers can't double-send.

## Two research sources, one free by default

**Free news feeds (always on, no setup).** The script pulls headlines from
Google News searches (IMAX/tickets, drops/streetwear, London events, tech,
football, free promos) plus a handful of culture outlets (Hypebeast,
Highsnobiety, Sneaker News, Time Out London, Designboom), and hands the
pile to Gemini to pick from. Zero cost, zero billing, works out of the box.
Reliable for anything that gets press coverage (a major IMAX release is
guaranteed); a very small drop announced only on a brand's own Instagram,
with no press pickup anywhere, may not appear — the feeds can only find
what a hooked-up outlet chose to write about.

**Live Google Search (optional, off by default).** Gemini's own web search
can reach much further than a fixed feed list — a brand's own site, a
niche blog, a forum thread. But Google requires a billing method on the
project before it allows *any* grounded search, even within the free
allowance — confirmed via `--diagnose`: plain text generation works with
zero billing, adding the search tool 429s "check your billing" instantly.

**To turn live search on**, once you've added billing + a spend cap in
Google AI Studio / Cloud Console (Google's free grounding quota still
applies at $0 — billing just unlocks it):

1. Repo Settings → Secrets and variables → Actions → **Variables** tab (not
   Secrets — this isn't sensitive) → New repository variable.
2. Name: `GEMINI_ENABLE_SEARCH`, value: `true`.

That's it — no code change. The feed layer keeps running underneath either
way; enabling search only adds a second source, never replaces the free one.

## The pieces

| File | What it is |
|---|---|
| `interests.md` | **The taste profile — edit this to tune what you get.** Plain English. |
| `discover.py` | The scout: fetch feeds → research → dedupe → email → archive. |
| `dashboard.html` | Browsable archive of every past find (search + filters, phone-friendly). |
| `state.json` | Once-per-day guard + rolling "already told you" memory (auto-committed). |
| `history.json` | Append-only digest archive read by the dashboard (auto-committed). |
| `test_discover.py` | Offline tests, network stubbed (`python test_discover.py`). |

## Emails you'll get

| Email | When |
|---|---|
| **✨ Daily Discovery — [date]** | Normal morning digest, 3–8 ranked finds with act-by dates and links. |
| **Daily Discovery — quiet day** | The scout ran fine but found nothing genuinely new. |
| **⚠️ Daily Discovery failed today** | Something broke — no digest should ever fail *silently*. Check the Actions tab or ask Claude. |

So: no email at all means the trigger itself didn't fire — check cron-job.org
and the Actions history.

## Secrets required (repo Settings → Secrets and variables → Actions)

- `GEMINI_API_KEY` — free key from aistudio.google.com (its own project, so
  it doesn't share quota with other Gemini programs).
- `RESEND_API_KEY` — from resend.com (starts `re_`). Sends from
  `onboarding@resend.dev` until a personal domain is verified — first email
  may land in spam once; mark it "not spam".
- `GEMINI_ENABLE_SEARCH` (optional repo **variable**, not secret) — see
  "Turning on live search" above.

## Run modes (Actions → Daily Discovery digest → Run workflow)

- `send` — the normal daily run
- `dry_run` — research and log the digest without emailing or saving state
- `test_email` — send a sample card through the real Resend path
- `force_send` — send again even if today's digest already went out
- `list_models` — print every Gemini model this key can use (diagnostic)
- `diagnose` — test plain generation vs. live search separately, to isolate
  which one is failing (diagnostic)

## Tuning

Edit `interests.md` — next run picks it up automatically. Model choice is
the `GEMINI_MODELS` list at the top of `discover.py` (Pro first, Flash
fallback); swapping models is a one-line change. Feed sources are the
`GOOGLE_NEWS_SEARCH_QUERIES` and `DIRECT_FEEDS` lists in the same file.

## Known limitations

- The free feed layer catches things within ~24h **if the press/culture
  outlets cover them**; a very small drop with zero press pickup can slip
  through until live search is turned on. For known high-priority targets,
  a dedicated page-watcher (like `ticket-alerter/`) is the right tool.
- Google's free-tier terms change from time to time; if a run fails, the
  failure email + `--diagnose`/`--list-models` modes point at the cause
  rather than guessing.
- Times are UTC, so the send hour drifts by 1h across UK clock changes
  (same as the sibling projects) -- unless cron-job.org's own schedule is
  set to a Europe timezone, in which case cron-job.org handles the
  clock change for you (see root CLAUDE.md).
