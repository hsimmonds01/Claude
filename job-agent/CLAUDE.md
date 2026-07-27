# Notes for Claude sessions in this repo

## Who this is for

The owner is not a developer. When something needs her to click around a
site, give complete numbered steps naming the exact buttons — don't assume
familiarity with tokens, cron syntax, HTTP or git. Explain briefly inline the
first time.

She runs sessions from her phone. Repo-committed files persist between
sessions; local files and anything not committed do not.

## What this is

A job-search agent. Runs on GitHub Actions four times a day, poked by
cron-job.org. Pulls roles from job APIs and her alert inbox, scores each
against `cv.md` / `profile.md` / `feedback.md` with Gemini Flash, then sends
phone alerts and email digests. Full functional docs in `README.md`.

## Rules that are not negotiable

- **This repo is private and must stay private.** Her CV is in it, and she is
  job-hunting while still employed.
- **Phone alerts stay vague.** Lock-screen text never carries a company name
  or job title — see `push.vague_wording` in `config.yml`. This is a
  discretion requirement, not a style preference.
- **No scraping.** Official APIs, public ATS feeds, and her own alert emails
  only. If a source needs scraping, the answer is an email alert from that
  site instead.
- **No billing card, anywhere.** Everything used is free-tier. Grounded
  Google Search was deliberately cut from the design because it's the one
  feature Google requires a card for — don't reintroduce it.
- **Never commit secrets.** Keys live in Settings → Secrets and variables →
  Actions, read via environment variables.

## Design decisions worth not re-deriving

- **Dedupe is by content fingerprint, not URL** (`jobagent/models.py`). The
  same role appears on Adzuna, Reed and in a LinkedIn alert with three
  different URLs. Fingerprint is normalised company + normalised title.
  Seniority words are deliberately *not* stripped — "Operations Manager" and
  "Senior Operations Manager" are different jobs.
- **Location is excluded from the fingerprint** on purpose. Boards write
  "London", "City of London" and "London, Greater London" for the same role.
  Over-merging is a fair trade against showing her the same job three times.
- **Scores are frozen on first sight** (`jobagent/dedupe.py`). An AI asked to
  mark the same ad twice gives two different numbers, and with a phone alert
  hanging off a threshold that means borderline roles ping on random days.
  Never rescore an existing fingerprint.
- **Digest hours must be a subset of run hours.** The agent only exists at
  `run_hours`; a digest hour outside that set silently never sends. Validated
  at load time with a plain-English error.
- **Config validation errors are written for her**, not for a developer. If
  you add a setting, add a readable error for it too.
- **A job with no company is kept, keyed by URL** (`models.py`). Alert emails
  sometimes yield a title and a link but no employer. Dropping those loses
  real vacancies; giving them a shared company-less fingerprint makes every
  "Operations Associate" collide. Keying by URL means such a job can't merge
  with its twin from an API, so she may see it twice — a much smaller failure
  than hiding it.
- **Alert-email job links are matched by whole path segment**
  (`sources/inbox.py`). Anything looser eats real vacancies: substring
  matching rejected `operations-manager` because it contains "manage", and
  adding `endswith` rejected `head-of-search`. Job titles are ordinary words,
  so the exclusion test has to be exact. Both cases have regression tests —
  don't "tidy" that matcher without running them.
- **Notifications fire before the git commit step**, so a failed push costs a
  log row rather than a missed alert.
- **The workflow sits in a `main-git-writer` concurrency group** with
  `cancel-in-progress: false`, so two runs can't race each other's pushes.

## Sources

| Source | Status |
|---|---|
| Adzuna | Live. Free UK API, ~1,000 calls/month, so calls are budgeted. |
| Reed | Live. Basic auth with the API key as username and an **empty** password. |
| Alert inbox (IMAP) | Live, but **built against synthetic emails** — patterns need checking against real alerts. Reaches WTTJ, LinkedIn, Indeed. Opens the mailbox read-only. |
| Company ATS feeds | Not built yet. Additive; she pastes a careers URL and the code resolves the ATS. |

## Channels

| Channel | Notes |
|---|---|
| ntfy push | `notify/push.py`. Vague wording is a discretion requirement. Topic name is the only access control and is never logged. |
| Email digest | `notify/mail.py` — named `mail` because `email` is a stdlib package and shadowing it breaks smtplib. Gmail SMTP over the same app password used for IMAP. |
| Failure email | Always attempted on a crash or a bad config, so silence only ever means "nothing matched". |

## Where things live

- `run.py` — orchestration, and the only place that reads environment
  variables. Modules take values as arguments so they stay testable.
- `seen.json` — long-lived memory of every role judged. Pruned at 60 days.
- `state.json` — daily push counter and last-digest date. Small, churns
  daily, harmless to lose. Kept separate so a counter update doesn't rewrite
  the big file.

Do not spend time re-checking whether Indeed, Welcome to the Jungle or
LinkedIn have usable APIs. They don't — that's settled, and the email-alert
route exists precisely because of it.

## Testing

`python -m pytest` from the repo root. Network is stubbed; tests must pass
offline. API response fixtures follow each provider's documented shape — if a
digest goes empty in real life while tests pass, suspect a renamed field
first.
