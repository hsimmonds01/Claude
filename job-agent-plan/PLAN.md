# Job-search agent — plan and handover notes

> ## READ THIS FIRST — WHERE TO BUILD
>
> **If your working directory is the `hsimmonds01/Claude` repo, do not create
> any project files here.** This document is planning only; the agent is not
> built in this repo, ever.
>
> Build in a **separate local folder with `git init` and no remote** — see
> section 8. It only gets pointed at a GitHub remote at the very end, and that
> remote will be a private repo in *her* account, not mine.
>
> If you find yourself inside `hsimmonds01/Claude` and about to write code,
> stop and tell me — I've started the session in the wrong folder.

**Status: PLANNING ONLY. Nothing has been built.**
Written 2026-07-27 in a phone session, committed to `hsimmonds01/Claude` purely
so it survives into a desktop session.

---

## 1. What we're building

An agent that checks job boards several times a day for roles matching a
specific person (my girlfriend, currently employed elsewhere, actively
looking), scores them against her CV and a set of preferences she writes in
plain English, and sends her the good ones as phone notifications and email
digests.

Key requirement: **she owns it completely.** It must not depend on my accounts,
my devices, or me being available. She must be able to retune it herself
without writing code.

---

## 2. Decisions already made

| Question | Decision |
|---|---|
| Who owns it | Her, entirely. Built directly in her accounts from day one. |
| Where it's built | A **new, private, standalone repo in her GitHub account.** Not this repo. |
| Where it runs | GitHub Actions (free). Never on a phone or laptop. |
| Scheduler | **cron-job.org** — proven reliable in this repo's other projects, unlike GitHub's native `schedule:`. |
| Runs per day | 4 (approx 7am, 12pm, 4pm, 8pm) |
| Notifications | Max 4/day total: up to 2 ntfy push (9/10+ roles only) + 2 email digests (8am, 6pm) |
| AI model | Gemini Flash via Google AI Studio free tier |
| Cost target | £0/month (plus her own Claude Pro, ~£16/mo, optional but planned) |
| Scraping | None. Official APIs and her own email alerts only. |

---

## 3. Architecture

```
cron-job.org (timer, 4x/day)
        |
        v
GitHub Actions  (in HER private repo)
        |
        +--> Adzuna API          (free UK jobs API, ~1000 calls/month)
        +--> Reed API            (free UK jobs API)
        +--> Target companies    (Greenhouse/Lever/Ashby/Workable public JSON feeds)
        +--> Alert inbox         (dedicated Gmail, read over IMAP)
        |
        v
Gemini Flash scores each role 0-10 against cv.md + profile.md + feedback.md
        |
        v
Dedupe against seen.json (committed back to the repo), drop below score cutoff
        |
        +--> ntfy push   (9/10+, max 2/day, deliberately vague wording)
        +--> email digest (8am + 6pm, everything else, best-first)
        |
        v
She edits profile.md / feedback.md from the GitHub mobile app -> next run is sharper
```

A colour version for showing her: `job-agent-flow.png` (source:
`job-agent-flow.html`, rendered via headless Chromium at 760px wide, portrait,
built to be read on an iPhone without zooming).

---

## 4. Research findings — job sources

Checked during the planning session. **Don't re-litigate these, they're settled:**

### Usable

- **Adzuna API** — free, documented, ~1,000 calls/month, good UK coverage,
  includes structured salary data. Register at developer.adzuna.com for an
  `app_id` + `app_key`. This is the backbone.
- **Reed.co.uk API** — free key, UK-specific. Good complement to Adzuna.
- **Company careers pages directly** — Greenhouse, Lever, Ashby and Workable
  each expose a public JSON endpoint per company. **Highest-signal source in
  the whole system**; roles appear here before they reach aggregators.
  Requires her target-company list.
- **Email-alert ingestion** — *the most important idea in this plan.* She sets
  up saved-search job alerts on any site she likes (WTTJ, LinkedIn, Indeed,
  niche boards), pointed at a dedicated Gmail. The agent reads that inbox over
  IMAP (Gmail app password), extracts the job links, and re-scores them
  alongside everything else. This unlocks **every board that exists**, is
  completely legitimate (she's a user reading her own alerts), and lets her add
  new sources herself with zero code changes.

### Not usable — confirmed, don't waste time retrying

- **Indeed** — public Publisher API deprecated 2023/24; publisher programme
  closed to new applicants since Oct 2022. Remaining APIs are employer-side
  and have no job-search endpoint. No legitimate route in.
- **Welcome to the Jungle** — no official developer API. (Note: WTTJ absorbed
  Otta; `app.welcometothejungle.com` is the old Otta experience, same account.)
  Their site runs on public Algolia indexes that third-party scrapers read, but
  it's undocumented, breaks without warning, and is a ToS grey area. **She
  particularly likes WTTJ**, so reach it via the email-alert route instead.
- **LinkedIn** — no usable free API, scraping against ToS. Email alerts only.

---

## 5. Research findings — the AI

- Google Search grounding **still works** from the Gemini API. Already proven
  daily in this repo's `discovery-agent/`.
- As of April 2026 the free tier is **Flash / Flash-Lite only** (Pro models
  dropped from free). Flash is more than enough for scoring job ads.
- Grounding: roughly **5,000 free grounded prompts/month**, then ~$14/1,000.
  At 4 runs/day this lands around 500/month. Free.
- Free tier limits: Gemini 2.5 Flash approx 10 RPM, 1,500 requests/day.
  Batch ~50 jobs per request and this is ~4 requests/day. Trivial.

**Design principle: feeds find the jobs, the AI judges them.** Do not make
grounded search the primary discovery mechanism — it returns stale and
occasionally invented links and is non-deterministic, which is unacceptable for
something that must reliably not miss things. Keep a grounded-search sweep as
an optional extra for the long tail, with every link verified by a fetch before
it is sent.

---

## 6. The files she controls (Tier B — no coding required)

All plain markdown, edited from the GitHub mobile app in seconds:

- **`cv.md`** — her CV as text. Converted once from PDF at build time.
- **`profile.md`** — target job titles, seniority, locations / remote / hybrid,
  salary floor, industries in and out, company size, dealbreakers, plus a
  free-text "what I actually want" section in plain English.
- **`feedback.md`** — append-only steering. She writes lines like
  "too junior, stop showing these" or "more like the Head of X role at Y".
  The most recent N lines get injected into the scoring prompt each run.
  This is the cheap, effective version of the agent learning her taste.
- **`config.yml`** — run times, channels on/off, min score, max alerts per run,
  digest vs instant, quiet hours.

**Every notification must carry a one-tap link straight to the GitHub mobile
edit view of `feedback.md`.** This is what makes the steering loop actually
get used rather than theoretically available.

### Tier A vs Tier B (an explanation that landed well — reuse it)

- **Tier B** = the steering wheel, pedals and radio. Editing the files above.
  Needs nothing but a free GitHub account. Covers ~90% of weeks.
- **Tier A** = rebuilding the engine. New job boards, changed scoring logic,
  fixing it when a site changes shape. Needs her own Claude Pro (~£16/mo).

She's getting Claude Pro, so she has both — but **build the Tier B controls
properly regardless**, so daily retuning never costs a Claude session.

---

## 7. Discretion — she is still employed

This is a real constraint, not a nice-to-have. Bake in:

1. **Vague notification text.** Lock screen reads "3 new matches" — never a
   company name or job title. Detail only after tap + unlock.
2. **Long random ntfy topic name** (e.g. `hj-9f4k2xq7m3`, not `hannah-jobs`).
   ntfy has no auth on public topics — the topic name *is* the password.
3. **Repo private from creation**, GitHub account on a personal email. Her CV
   lives in this repo.
4. **Digests to a personal address only.**
5. Worth flagging to her (unrelated to our build): LinkedIn job alerts are
   private, but the "Open to Work" badge is public. Different setting.

---

## 8. Ownership and handover

**The realisation that settled this:** if it's built on her phone, in her
GitHub account, with her Claude subscription, **there is no handover** — it's
hers from the first commit. No transfer, no key rotation, no dependency on me
to unpick later.

Also worth stating plainly, because it was a misconception at the start:
**nothing ever runs on anybody's phone.** It runs on GitHub's servers on a
timer. Phones are just the remote control. The thing that needed cutting was
never a device, it was account dependency.

**Build local-first, and don't wait for her accounts.** "Not in my repo" means
the finished thing doesn't *live* in my repo — it does not mean waiting around.
Build the whole agent in a plain local folder with `git init` and no remote.
Full history, everything works normally, it just has no GitHub address yet.
When she has an account: `git remote add origin <her repo>` and push, and the
entire build lands as her repo's first commit with nothing of mine attached.

Roughly 90% of the work needs nothing from her:

- All code, the workflow file, the scoring prompt, the file templates, the
  email layout, her repo's `CLAUDE.md` and README — no accounts needed.
- **Company ATS feeds need no key at all** — fully public, testable today.
- **ntfy needs no account** — invent a topic, install the app, test real push
  notifications in five minutes.
- Adzuna, Reed and Gemini keys are free and instant. Use throwaway ones of mine
  for the build and never ship them.
- Test the scoring with **my own CV and preferences** as stand-in data — I'll
  know immediately whether the roles it picks are sensible.

Only her CV, company list, profile answers, the alert-inbox Gmail and the final
go-live actually need her — and the first three don't need her GitHub account,
just a conversation.

Fallback if it ever *is* built in my account first: GitHub Settings → General →
Danger Zone → **Transfer ownership** → her username → she accepts by email.
Repo-level Actions secrets travel with the repo, which is exactly why keys
should be rotated at that point anyway. Then repoint cron-job.org. About 20
minutes — but it means doing the key setup twice for no reason. Avoid.

---

## 9. Setup session checklist (~45–60 min, sat with her — AFTER the build)

Deliberately at the end, not the start. Turning up with a working agent she can
poke at beats turning up with an empty checklist: she gets to react to
something real ("that scoring's too generous", "I'd never want that job")
before any of it is load-bearing.


Everything created **in her name**. She is not a developer either — give
click-by-click instructions naming exact buttons.

| # | Step | Time |
|---|---|---|
| 1 | GitHub account (free, personal email, not work) | 5 min |
| 2 | Claude Pro on her phone | 3 min |
| 3 | Adzuna developer key (`app_id` + `app_key`) | 5 min |
| 4 | Reed API key | 5 min |
| 5 | Google AI Studio key (Gemini) | 5 min |
| 6 | ntfy app installed, private random topic chosen | 5 min |
| 7 | Dedicated Gmail for job alerts + app password for IMAP | 10 min |
| 8 | cron-job.org account + one job hitting the workflow_dispatch endpoint | 10 min |
| 9 | Paste all keys into the repo's Settings → Secrets page | 10 min |

Note on cron-job.org: set the account's schedule timezone to Europe/London so
it handles BST/GMT itself. One job can cover all four run times by ticking
multiple individual hours rather than needing four jobs.

---

## 10. Still needed from her

1. **Her CV** (PDF fine, convert to `cv.md` at build time).
2. **Target company list**, even rough — feeds the highest-quality source.
3. **`profile.md` answers**: titles she'd say yes to, seniority, location /
   remote preference, salary floor, industries in and out, dealbreakers.

---

## 11. Suggested build order

0. `mkdir job-agent && git init` in a local folder with **no remote**. Build
   everything here first (see section 8). Only point it at her GitHub at the
   very end.
1. Repo scaffold + GitHub Actions workflow + cron-job.org trigger, end to end
   with a stub that just sends "hello" — proves the plumbing before any logic.
2. Adzuna + Reed pulls, `seen.json` dedupe.
3. Gemini scoring against `cv.md` / `profile.md` / `feedback.md`.
4. ntfy push + email digest, with the `config.yml` caps and quiet hours.
5. Target-company ATS feeds.
6. Email-alert inbox ingestion (IMAP).
7. Optional: grounded-search long-tail sweep with link verification.

Test locally with mocked API responses where the sandbox has no network.

---

## 12. Repo conventions inherited from this repo

Worth carrying over even though it'll be a different repo:

- Any workflow that commits state back to its default branch should sit in a
  named `concurrency` group with `cancel-in-progress: false`, so parallel runs
  can't race each other's pushes. (Learned the hard way here — see root
  `CLAUDE.md`.)
- Side effects (notifications) should fire *before* the git commit step, so a
  failed push costs a log row rather than a missed alert.
- Write a `CLAUDE.md` in her repo from day one so her future sessions pick up
  context automatically.
