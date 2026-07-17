# Daily Discovery

An AI scout that emails you the coolest newly-announced things every
morning — ticket on-sales, limited-edition drops, London events, genuinely
interesting products — researched live with Google Search via Gemini's free
API and delivered as a styled digest via Resend. Free to run.

Perfect-catch examples this exists for: "The Odyssey" IMAX tickets going on
sale, the Wynwood x Strawberry Stellar Ottawa hat drop.

## How it works

```
cron-job.org (reliable scheduler, fires at your chosen hour)
   └─> GitHub Actions workflow_dispatch  (.github/workflows/daily-discovery.yml)
         └─> discover.py
               ├─ reads interests.md ........ your editable taste profile
               ├─ asks Gemini 2.5 Pro ....... web research via Google Search
               │    (falls back to Flash automatically if Pro errors/quota)
               ├─ filters vs state.json ..... never repeats a find (fuzzy match)
               ├─ emails via Resend ......... styled HTML digest
               └─ archives to history.json .. feeds dashboard.html (the UI)
```

GitHub's native schedule (07:20 UTC) is kept as a backup net only — it has
run >1hr late on this repo. The once-per-day guard in `state.json` means
duplicate triggers can't double-send.

## The pieces

| File | What it is |
|---|---|
| `interests.md` | **The taste profile — edit this to tune what you get.** Plain English. |
| `discover.py` | The scout: research → dedupe → email → archive. |
| `dashboard.html` | Browsable archive of every past find (search + filters, phone-friendly). |
| `state.json` | Once-per-day guard + rolling "already told you" memory (auto-committed). |
| `history.json` | Append-only digest archive read by the dashboard (auto-committed). |
| `test_discover.py` | Offline tests with Gemini/Resend stubbed (`python test_discover.py`). |

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

## Run modes (Actions → Daily Discovery digest → Run workflow)

- `send` — the normal daily run
- `dry_run` — research and log the digest without emailing or saving state
- `test_email` — send a sample card through the real Resend path
- `force_send` — send again even if today's digest already went out

## Tuning

Edit `interests.md` — next run picks it up automatically. Model choice is
the `GEMINI_MODELS` list at the top of `discover.py` (Pro first, Flash
fallback); swapping models is a one-line change.

## Known limitations

- Catches things within ~24h **if the press/event sites cover them**; a
  quiet 9am announcement sold out by noon can slip through. For known
  high-priority targets, a dedicated page-watcher (like `ticket-alerter/`)
  is the right tool.
- Google's free-tier terms (especially search grounding) change from time
  to time; if that breaks, the failure email will say so and the script can
  be reworked to fetch sources directly.
- Times are UTC, so the send hour drifts by 1h across UK clock changes
  (same as the sibling projects).
