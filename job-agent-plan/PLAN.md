# Job-search agent — plan and handover notes

> ## READ THIS FIRST — WHERE TO BUILD, AND WHAT MUST NEVER GO HERE
>
> **`hsimmonds01/Claude` is a PUBLIC repo.** Verified 2026-07-27:
> `gh repo view --json visibility` → `"PUBLIC"`. Everything committed here is
> readable by anyone on the internet, forever, including in git history after
> a later deletion.
>
> **Build the code here.** Sections 8 and 11 previously said to build in a
> local folder with no remote — that was wrong for how these sessions actually
> run. Sessions are run from a phone/web, in a fresh container each time; an
> un-pushed local folder does not survive between them. This repo is the only
> thing that persists, so the code lives here, in `job-agent/`, on its own
> branch, until go-live.
>
> **Her personal data must never be committed here.** Not to a branch, not
> temporarily, not "just to test". A public repo plus a person who is still
> employed and quietly looking is the one combination this project cannot
> have. Specifically banned from this repo:
>
> - her CV, or any extract of it
> - a filled-in `profile.md` (salary floor, dealbreakers, current employer,
>   why she's leaving)
> - her target company list (i.e. who she's applying to)
> - her name, her email addresses, her ntfy topic
>
> Those files exist here only as **blank templates** in `drafts/`. They get
> filled in for the first time inside her own private repo, at go-live.
>
> Go-live is a **copy, not a GitHub "Transfer ownership"** — transfer moves an
> entire repo, and this one holds five unrelated projects. See section 8.

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
| Who owns it | Her, entirely. Her repo, her keys, her notifications, from its first commit. |
| Where the code is built | `job-agent/` in this repo, on a branch — this is the only thing that survives between phone sessions. **Code only, never her data.** |
| Where it ends up | A **new, empty, private repo in her GitHub account**, at go-live. Copied in, not transferred. |
| Where it runs | GitHub Actions (free). Never on a phone or laptop. |
| Scheduler | **cron-job.org** — proven reliable in this repo's other projects, unlike GitHub's native `schedule:`. |
| Runs per day | 4 — **7am, 12pm, 4pm, 6pm** (was 8pm; changed so digest hours land on run hours, see below) |
| Notifications | Max 4/day: up to 2 ntfy push (**8/10+**, was 9) + 2 email digests, sent on the 7am and 6pm runs |
| AI model | Gemini Flash via Google AI Studio free tier. **No billing card, anywhere.** |
| Grounded search | **Cut.** It's the only part that would need a card. Not needed — feeds find the jobs. |
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
        +--> ntfy push   (8/10+, max 2/day, deliberately vague wording)
        +--> email digest (on the 7am + 6pm runs, everything else, best-first)
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

- ~~Google Search grounding **still works** from the Gemini API. Already proven
  daily in this repo's `discovery-agent/`.~~ **Corrected 2026-07-27.** It works
  in `discovery-agent/` only via `GEMINI_API_KEY_LEGACY`, a grandfathered key
  predating the restriction. On any key created today, Google requires a
  billing card on the project before it allows *any* grounded search — proven
  by that project's own `--diagnose` mode: plain generation succeeds with zero
  billing, adding the search tool returns `429 … check your billing` instantly.
- **Consequence: grounded search is cut from this project entirely.** It was
  already only an optional long-tail extra (old build step 7). It is the sole
  component that would have needed a card, and it is not worth a card.
- **Everything else needs no billing.** Scoring job ads is plain text
  generation, which is free on a new key with no payment method attached. The
  hard requirement of "no card anywhere" is met, permanently, not by luck.
- As of April 2026 the free tier is **Flash / Flash-Lite only** (Pro models
  dropped from free). Flash is more than enough for scoring job ads.
- Free tier limits: Gemini 2.5 Flash approx 10 RPM, 1,500 requests/day.
  Batch ~50 jobs per request and this is ~4 requests/day. Trivial.

**Design principle: feeds find the jobs, the AI judges them.** This was always
the design, and dropping grounded search costs the project almost nothing
because of it. Grounded search was never allowed to be the discovery mechanism
anyway — it returns stale and occasionally invented links and is
non-deterministic, which is unacceptable for something that must reliably not
miss things. The real discovery layer is Adzuna + Reed + company careers pages
+ her alert inbox, all of which are exact, free and card-free.

---

## 6. The files she controls (Tier B — no coding required)

All plain markdown, edited from the GitHub mobile app in seconds:

- **`cv.md`** — her CV as text. Converted once from PDF at build time.
- **`profile.md`** — target job titles, seniority, locations / remote / hybrid,
  salary floor, industries in and out, company size, dealbreakers, plus a
  free-text "what I actually want" section in plain English.
- **`feedback.md`** — steering, in **two sections** (revised — pure
  append-only rots by month six, as contradictory lines both reach the prompt
  and cancel out):
  - *Standing rules* — permanent, always sent, she edits and deletes freely.
  - *Recent reactions* — append-only, most recent N sent, old ones fade by
    themselves. "too junior", "more like this one", "stop showing me X".

  This is the cheap, effective version of the agent learning her taste.
- **`config.yml`** — run times, channels on/off, min score, push threshold,
  max alerts per run, quiet hours, target-company list, and a master
  `enabled` kill switch for weeks she doesn't want the noise.

Blank annotated drafts of all four live in `drafts/`. **They stay blank in
this repo** — this repo is public; they're filled in inside hers.

All are read fresh on every run, so an edit takes effect within hours and
nothing is baked in at deploy time.

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
6. **Nothing identifying her goes in `hsimmonds01/Claude`, which is public.**
   Not her CV, not a filled-in `profile.md`, not her target companies, not her
   name. The build happens here; her data does not. Full list in the banner at
   the top of this file. This is the same constraint as points 1–4 — it's the
   discretion rule applied to the workshop rather than the finished product.

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

**Build here, hand over by copy.** Don't wait for her accounts — roughly 90% of
the work needs nothing from her. Build it in `job-agent/` in this repo, on a
branch.

An earlier revision said to build in a local folder with `git init` and no
remote. **That was wrong** for one specific reason: these sessions run from a
phone/web in a fresh container each time, so an un-pushed local folder is gone
by the next session. A pushed branch in this repo is the only thing that
actually persists. Build where the work survives.

Two constraints that fall out of building here, both absolute:

1. **This repo is public** (verified 2026-07-27). Code only. None of her
   personal data, ever — see the banner at the top of this file for the list.
   The control files live here as blank templates and are filled in for the
   first time inside her private repo.
2. **Go-live is a copy, not a GitHub transfer.** GitHub's "Transfer ownership"
   moves an entire repo, and this one holds five unrelated projects, so it is
   simply not available. That's fine — it was never the better route anyway.

### Go-live, concretely

1. She creates a **new, empty, private** repo (`SETUP.md` step 2 — no README,
   or the push bounces).
2. Copy `job-agent/`'s contents into a fresh folder, `git init`, one commit.
3. `git remote add origin <her repo>` → push.

Her repo's entire history is that one commit. Nothing of mine is attached to
it — no fork relationship, no shared history, no reference back to this repo,
nothing to unpick later. From her side it is indistinguishable from having
been built there in the first place, which is the whole point.

Keys never travel — they're pasted straight into her repo's Actions secrets by
her (`SETUP.md` step 12) and have never existed anywhere else. Any throwaway
keys used during the build stay here and get discarded; nothing needs rotating
at handover.

### What still needs her

Only her CV, company list, profile answers, the alert-inbox Gmail, and the
go-live session itself. Everything below is buildable today with no input:

- All code, the workflow file, the scoring prompt, the file templates, the
  email layout, her repo's `CLAUDE.md` and README.
- **Company ATS feeds need no key at all** — fully public, testable now.
- **ntfy needs no account** — invent a topic, install the app, test real push
  notifications in five minutes.
- Adzuna, Reed and Gemini keys are free and instant. Use throwaway ones for
  the build; they never ship.
- Test scoring against **a stand-in CV and profile** — my own, or a realistic
  synthetic one. Good enough to tell whether the picks are sensible.

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

None of this blocks the build. All three are expected to arrive late, rough,
and to change repeatedly — **design for that**, don't design for a clean
one-time intake.

1. **Her CV** (PDF fine, converted to `cv.md`). **Goes straight into her
   private repo, never into this public one.** Until then, build and test
   against a stand-in.
2. **Target company list** — expected to be *thin*, and that's fine. It is an
   **additive** source: each company named adds its careers page as an extra
   feed. It does **not** narrow the broad Adzuna/Reed search, which runs
   regardless. A short list is a small bonus, not a precondition, and it can
   be grown one line at a time forever.
3. **`profile.md` answers** — partial is fine and expected. Known so far:
   **operational roles, possibly in an organisation/charity setting, plus some
   marketing.** Enough to seed search terms; not enough to score well yet.

**Because all three will change:** `cv.md`, `profile.md` and `config.yml` are
read fresh from disk on every single run, with no caching and no build step.
Editing one changes behaviour within hours. Nothing is baked in at deploy
time, so "we'll refine it later" costs nothing.

---

## 11. Suggested build order

Revised 2026-07-27. Company feeds were briefly promoted to second on the
grounds of being the highest-signal source; demoted again once it became clear
the target list will start thin. Breadth first, precision when the list exists.

0. `job-agent/` in this repo, on a branch. Code only — see section 8.
1. Scaffold + GitHub Actions workflow + `workflow_dispatch`, end to end with a
   stub that just sends "hello". Proves the plumbing before any logic.
2. Adzuna + Reed pulls, with **fingerprint dedupe** (company + normalised
   title + location — *not* URL, or the same role arrives three times).
3. Gemini scoring against `cv.md` / `profile.md` / `feedback.md`. Score once
   on first sight and freeze it, so a job can't drift across the push
   threshold day to day.
4. ntfy push + email digest, `config.yml` caps, quiet hours, failure email.
5. Email-alert inbox ingestion (IMAP) — the breadth source that reaches WTTJ,
   LinkedIn and Indeed, and the one she can extend herself.
6. Target-company ATS feeds, once there's a list. Additive; nothing else
   depends on it, so it can land any time, including after go-live.
7. ~~Grounded-search long-tail sweep~~ — **cut**, see section 5.

Test with mocked API responses where the sandbox has no network.

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
