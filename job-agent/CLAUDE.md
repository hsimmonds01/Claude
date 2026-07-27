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
- **All hours are in her timezone, never the runner's.** GitHub's runners are
  UTC; every hour in `config.yml` is labelled UK time and cron-job.org is set
  to Europe/London. Read the clock via `cfg.now()` and nothing else. Using
  `datetime.now()` meant that through British Summer Time all four triggers
  arrived an hour early, matched no `run_hours`, and the agent no-opped four
  times a day — successfully, so no failure email. `tzdata` is a real
  dependency: `zoneinfo` has no bundled database on Windows.
  - GitHub's own `schedule:` cron is UTC-only with no timezone setting, so it
    drifts an hour in summer. Can't be fixed in code, and it's fine — it's
    only the backup, and an off-hour trigger just no-ops.
- **Digest hours must be a subset of run hours.** The agent only exists at
  `run_hours`; a digest hour outside that set silently never sends. Validated
  at load time with a plain-English error.
- **Every setting in `config.yml` must actually do something.** `explain_scores`
  and `alert_on_failure` shipped parsed-but-unread while the file described
  them as working controls, and `notified` was persisted while never being
  set. She edits YAML from a phone with no way to test it, so a knob that does
  nothing is worse than no knob — she'd conclude the agent is ignoring her.
  If a setting isn't wired yet, say so in the file (see the companies section).
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

## Security invariants — don't regress these

Each has a test in `tests/test_security.py`. They're all silent in
production: a leaked key looks like a normal log line and an inflated score
looks like a good job.

- **Job adverts are untrusted input.** They're written by strangers and go
  into a prompt that also carries her instructions. `scoring._sanitise`
  flattens them and the prompt frames them explicitly as data, telling the
  model to score any advert containing instructions as 0. Don't remove either
  half.
- **Only http/https links are ever rendered.** `models.is_safe_link` rejects
  `javascript:`, `data:` and control characters; an unsafe URL disqualifies
  the job in `is_usable`. Adverts and alert emails can carry any URL, and an
  href is live in some mail clients.
- **All job data is HTML-escaped in the digest.** It's third-party text
  going into an email.
- **Secrets are scrubbed from logs and the failure email** (`redact.py`).
  Adzuna *and Gemini* both take credentials as query parameters, so any
  `requests` exception message contains the key verbatim — including the ones
  from `raise_for_status()` on a status that isn't explicitly short-circuited
  (400 and 403 are the realistic ones: rotated key, project restriction, API
  not enabled). **Any new module that calls an API with a key in the URL must
  scrub before logging.** A test reads the workflow and fails if a secret is
  added there without being added to `SECRET_ENV_NAMES`.
- **The mailbox is opened read-only** and only configured trusted senders are
  treated as job sources. An empty allowlist trusts nothing — that address is
  advertised publicly on job sites.
- **Sender identity comes from `parseaddr`, never a regex.** A From header is
  `Display Name <address>` and the display name is free text chosen by the
  sender, so `From: "jobalerts@linkedin.com" <careers@attacker.example>` beat
  a regex-based check while arriving from a domain the attacker controls —
  passing SPF/DKIM and landing in the inbox rather than spam. That put
  attacker-chosen links into the digest and onto the lock screen. Use
  `is_trusted_sender(message, ...)`, which parses properly, requires every
  address in the header to be trusted, and fails closed on an unparseable
  header. Do not "simplify" it back to string matching.
- **Test the parsing, not just the comparison.** The bypass above existed
  while `is_trusted` had full coverage, because every test handed it a
  pre-parsed domain string. Security tests must build a real message and run
  the real path.
- **Tracking redirects are unwrapped by string parsing, never by fetching.**
  Following one server-side would register a click she never made.

## Testing

`python -m pytest` from the repo root. Network, SMTP and IMAP are stubbed;
tests must pass offline. `python -m ruff check .` and `python -m black .`
before committing.

API response fixtures follow each provider's documented shape — if a digest
goes empty in real life while tests pass, suspect a renamed field first.

**Test where the value comes from, not just what it's compared against.**
This has now caused three separate live bugs, all of which had green tests:

| Bug | What was tested | What wasn't |
|---|---|---|
| Sender-allowlist bypass | `is_trusted("linkedin.com", ...)` | Parsing the `From` header |
| Timezone no-op | Scheduling logic with a stubbed clock | Which clock it reads |
| Template scaffolding sent as her answers | Hand-written fixtures | The real shipped files |

When a test needs a value the code normally derives, prefer controlling the
real source (`Config.now`, a real `email.message`, the actual template file)
over replacing the whole module.
