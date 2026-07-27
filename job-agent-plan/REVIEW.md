# Fresh-eyes review of PLAN.md

Written 2026-07-27, desktop session, before any code exists.
Research findings in PLAN.md §4 (which job boards have APIs) are **settled and
not re-examined here.** Everything below is about design, sequencing and
things the plan is missing.

Verdict: the plan is sound. The ownership model, the email-alert ingestion
idea, and the Tier A / Tier B split are all right and shouldn't change. The
items below are corrections and gap-fills, ordered by how much damage they'd
do if left alone.

---

## Status — resolved 2026-07-27

Everything below has been actioned in `PLAN.md`. Kept for the reasoning.

| Finding | Outcome |
|---|---|
| A1 digest hours never fire | **Fixed.** Runs moved to 7am / 12pm / 4pm / **6pm**; digests fire on the 7am and 6pm runs. |
| A2 same job three times | **Fixed in plan.** Fingerprint dedupe (company + normalised title + location), sources merged not binned. Build step 2. |
| A3 grounded search not free | **Cut entirely.** It was the only component that would ever have needed a billing card. Nothing else does. |
| A4 unstable scores | **Fixed in plan.** Score once on first sight and freeze; push threshold 9 → **8**, config-tunable. |
| B1 no email mechanism | **Gmail SMTP**, reusing the same app password as the IMAP read. Fallbacks in `SETUP.md` Appendix A. |
| B2 missing token step | **Added** as `SETUP.md` step 10. |
| B3 Actions minutes metered | **Accepted** — `timeout-minutes: 10` on the job from the first commit. |
| B4 `seen.json` grows forever | **Accepted** — 60-day prune, mirroring `discovery-agent`. |
| B5 `feedback.md` rots | **Fixed.** Two sections: standing rules + fading reactions. |
| C1 build best source first | **Reversed, see below.** |
| C2 company list wants a URL | **Kept.** She pastes a careers-page link; the code resolves the ATS. |

### C1 reversed — breadth first after all

I recommended building target-company feeds second, on the grounds that
they're the highest-signal source. That was right in the abstract and wrong
here: the target list is expected to start **thin**, and there's an explicit
preference not to constrain the search to named companies.

Worth being precise about why that preference is well-founded but aimed at
the wrong thing: **company feeds are additive, not restrictive.** Each
company named adds its careers page as an extra feed. Adzuna and Reed keep
searching the whole market either way. Naming ten companies cannot narrow
anything — it can only add.

So the fear doesn't apply — but the thinness does. A source with three
companies in it isn't worth building second. Revised order: **breadth first
(Adzuna + Reed), then the alert inbox, then company feeds whenever the list
exists** — including after go-live, since nothing depends on it.

### New finding — this repo is public

`gh repo view --json visibility` → `PUBLIC`. Discovered while committing this
review. It doesn't change where the code gets built, but it hard-constrains
what data may go near it, and it's now the banner at the top of `PLAN.md`.
See section D.

---

## A. Things that are wrong and would bite

### A1. The digest times can never fire — timing bug

PLAN.md §2 says runs happen at **7am, 12pm, 4pm, 8pm** and email digests go
out at **8am and 6pm**.

Nothing runs at 8am or 6pm. The agent is asleep at both digest times, so
under a literal reading of the plan the digests never send.

**Fix:** make the digest fire *on a run*, not at an independent time. Move
the run hours to **7am, 12pm, 4pm, 6pm** and send digests on the 7am and 6pm
runs. Both numbers live in `config.yml` so she can move them later without
touching code — but they have to be the *same* numbers, and the code should
refuse to start (loudly, in the Actions log) if a digest hour isn't also a
run hour. Cheap guard, saves a silent "why did I stop getting emails".

### A2. The same job will arrive three times

Adzuna, Reed, and a LinkedIn alert email will all carry the same role with
three completely different URLs. PLAN.md §3 dedupes against `seen.json` but
doesn't say on what.

If dedupe is by URL, her very first digest shows the same job three times,
and she stops trusting the tool immediately. This is the single biggest
technical risk in the plan and it shows up on day one.

**Fix:** dedupe on a normalised fingerprint — lowercased company name +
lowercased title with seniority noise stripped + location — *not* the URL.
Keep every URL found for that fingerprint and show the best one (company
careers page > Reed/Adzuna > alert email link). Merging beats filtering here:
a role that shows up on three sources is a *stronger* signal, not a duplicate
to be binned, and the digest can say so.

### A3. Grounded search is not free on a new key — PLAN.md §5 is stale

§5 says Google Search grounding "still works" and is "already proven daily
in this repo's `discovery-agent/`". That's half true and the half that's
missing matters.

From `discovery-agent/discover.py`:

> Two ways to unlock it, tried in this order: `GEMINI_API_KEY_LEGACY` (an
> older, grandfathered Gemini key that still gets free grounded search), or
> `GEMINI_ENABLE_SEARCH=true` on the primary key once billing + a spend cap
> are added.

And from its README, confirmed by its own `--diagnose` mode: plain text
generation works with zero billing; **adding the search tool 429s "check your
billing" instantly.**

So grounding works here only because of a grandfathered key that predates the
restriction. A brand-new key created by her in 2026 will not get it without a
card on file.

**Impact is small** — the plan already correctly relegates grounded search to
an optional long-tail sweep (build step 7). But it should be marked
**"needs her to add a billing card + spend cap"** rather than "free", and
honestly I'd drop step 7 from v1 entirely. Plain scoring, which is the part
that actually matters, needs no billing at all.

### A4. LLM scores are not stable, and a 9/10 push gate makes that visible

Ask a model to score the same job ad twice and you get 7 and then 9. With
push tied to "9/10+", a borderline role either pings her twice on different
days or never pings at all.

**Two fixes, both cheap:**
- **Score once, store it.** The score goes into `seen.json` alongside the
  fingerprint and is never recalculated. A job's score is decided the first
  time it's seen, full stop. This also means re-runs are free.
- **Start the push gate at 8, not 9,** and make it a config number. 9/10 is
  a high bar — a realistic risk is that she gets *zero* push alerts for three
  weeks, concludes it's broken, and disengages. Better to start slightly
  noisy and let her turn it down via `config.yml` once she's seen what an
  8 and a 9 actually look like in practice.

Also worth having the model return a one-line reason with every score. It
costs nothing, makes the digest far more useful, and makes it obvious when
the scoring has gone wrong.

---

## B. Things the plan doesn't mention that it needs to

### B1. There's no email sending mechanism at all

§3 says "email digest" and §9's checklist has no email-sending account in it.
The sibling `discovery-agent/` uses **Resend** — that's another signup,
another API key, and Resend sends from `onboarding@resend.dev` until a
domain is verified, so the first digest reliably lands in spam.

**Better option, and it removes a step rather than adding one:** she's
already creating a dedicated Gmail with an app password so the agent can
*read* her job alerts over IMAP (§9 step 7). The exact same account and the
exact same app password can *send* the digest over SMTP.

- No extra account, no extra API key, no extra secret.
- Arrives from an address she owns, to an address she owns → no spam problem.
- One less thing to break, one less thing to explain.

Recommend Gmail SMTP. Keep Resend noted as the fallback if Google ever pulls
app passwords.

### B2. The cron-job.org step is missing its hardest part

§9 step 8 says "cron-job.org account + one job hitting the workflow_dispatch
endpoint" — but hitting that endpoint requires a **GitHub personal access
token** with Actions write permission, and creating a fine-grained token is
comfortably the fiddliest thing in the whole setup session.

It needs to be its own numbered step, before the cron-job.org one. Included
in `SETUP.md` as step 10.

Related, from this repo's own `CLAUDE.md`: editing a fine-grained token's
permissions later does **not** change an already-copied token string. If we
get the permissions wrong we have to regenerate and re-paste, not just edit.
Worth getting right first time.

### B3. Private repo Actions minutes are metered

Public repos get unlimited GitHub Actions. **Private repos on the free plan
get 2,000 minutes/month.** Hers must be private (her CV is in it), so this is
a real budget.

4 runs/day × ~30 days = ~120 runs. At 2 minutes a run that's 240 minutes —
comfortable. But an IMAP fetch that hangs, or a retry loop, turns a 2-minute
run into a 6-hour one and burns the month in a single afternoon.

**Fix:** `timeout-minutes: 10` on the job. One line. Do it from the first
commit, not after the first incident.

### B4. `seen.json` grows forever

Committed back to the repo on every run, four times a day, never pruned. It
becomes a large file that every run has to read and rewrite, and it bloats
git history.

`discovery-agent/` already solved this — it has a `prune_seen()`. Same
approach: drop entries older than N days (60 in the draft config). A job
posted 60 days ago that resurfaces is arguably worth showing again anyway.

### B5. `feedback.md` as pure append-only will rot

§6 says append-only, most recent N lines injected. Two problems after a
couple of months: she can't retract something she said in March, and
contradictory lines ("more remote" / "actually office is fine") both end up
in the prompt where they cancel out.

**Fix — small change, big difference in longevity:** two sections in the
file.

- **Standing rules** — permanent, always sent to the AI, she edits and
  deletes freely. "Never show me anything under £X." "No agencies."
- **Recent reactions** — append-only, last N lines sent. The quick
  "too junior" / "more like this" reflex notes.

Same one-line-and-save experience for her, but the file stays sane at month
six. Built this way in `job-agent/feedback.md`.

---

## C. Things I'd sequence differently

### C1. Build the *best* source first, not the easiest

PLAN.md §11 build order is: plumbing → Adzuna/Reed → scoring → notifications
→ **target companies (5)** → **email alerts (6)**.

But the plan itself calls target-company feeds "the **highest-signal source
in the whole system**" and email-alert ingestion "*the most important idea in
this plan*". Both are last.

Meanwhile Adzuna and Reed are broad aggregators — high volume, lots of
recruiter reposts and agency noise. If her first week is Adzuna-only, her
first impression of the agent is a firehose of jobs she doesn't want.

**Recommend swapping to:**

1. Plumbing end-to-end with a stub (unchanged — right call)
2. **Target-company ATS feeds** — public JSON, no key, no rate limit, and
   the roles she actually cares about
3. Scoring + dedupe
4. Notifications (ntfy + email) with the config caps
5. Adzuna + Reed for breadth
6. Email-alert inbox over IMAP
7. ~~Grounded long-tail sweep~~ — drop from v1, see A3

Same total work, but the version she sees first is the good one. It also
front-loads the thing that needs her target-company list, so we find out
early if that list is thin.

### C2. Company list should ask her for a URL, not an ATS name

The ATS feeds need to know that Monzo is on Greenhouse with board token
`monzo`. She is not going to know or care what Greenhouse is.

**Fix:** in `config.yml` she writes the company name and pastes the URL of
their careers page. The code works out which ATS it is and what the token is
from the URL. Drafted that way. If a URL can't be resolved, the run logs it
and the digest tells her which company needs a different link — it never
silently drops one.

---

## D. Smaller notes

- **`hsimmonds01/Claude` is public, and her data must never enter it.**
  Verified 2026-07-27. This is the sharpest constraint in the project and it
  wasn't in the original plan, because the plan assumed the build would
  happen elsewhere. The build now happens here (it's the only thing that
  survives between phone sessions), so the constraint has to be explicit
  instead of implicit. Banned: her CV, a filled-in `profile.md`, her target
  company list, her name, her email addresses, her ntfy topic. The control
  files live here as blank templates only. Note this is the *same* rule as
  PLAN.md §7's discretion requirements — a public commit history of "roles
  she's targeting while still employed" is exactly the exposure §7 exists to
  prevent. It just applies to the workshop rather than the product.
- **Local-first build has one sharp edge: her repo must be created empty.**
  PLAN.md §8 (build locally with no remote, point it at her GitHub at the
  end) is the right call. But the default "create a repository" flow on
  GitHub has **Add a README file** sitting there ready to tick, and ticking
  it puts a commit on GitHub's side that the local history doesn't share.
  The push then bounces with *"Updates were rejected because the remote
  contains work that you do not have locally"* — mid-session, in front of
  her, and the fix is a merge rather than a click. `SETUP.md` step 2 now
  calls this out explicitly. (Hit exactly this failure while committing this
  review, which is what prompted the note.)

- **IMAP alert parsing is thinner than it sounds.** LinkedIn and Indeed alert
  emails wrap every link in tracking redirects, and the useful metadata
  (salary, full description) is on the far side of a page that LinkedIn will
  block a server from fetching. Realistically we get: a link, a job title, a
  company name. That's enough to score on, but email-sourced roles will be
  scored on less information than API-sourced ones. Worth telling her so a
  lower score on a WTTJ role doesn't read as the agent disliking WTTJ.
- **Gmail app passwords need 2-Step Verification switched on first,** and
  Google has been narrowing access to them over time. I'm not certain they're
  still available on a brand-new account created today. `SETUP.md` puts this
  step early on purpose — if it's blocked, we find out at the start of the
  session and switch to Resend for sending and a different approach for
  reading, rather than discovering it at the end.
- **ntfy discretion is sound but not secret.** Messages pass through
  ntfy.sh's servers in the clear. The random topic name is the only control.
  That's fine given the plan already mandates vague wording — just don't let
  the wording rule slip later.
- **`config.yml` needs a kill switch.** One `enabled: false` at the top that
  stops all notifications without deleting anything — for holidays, or for a
  week when she's interviewing and doesn't want the distraction. Drafted.
- **Failure must be loud.** Copy `discovery-agent`'s pattern: if a run breaks,
  it emails her a failure notice. Silence should only ever mean "nothing
  matched", never "it's been dead for nine days".
- **Concurrency group** — PLAN.md §12 is right and I'd go further: her repo
  will only have one workflow at first, so the group costs nothing now, but
  add it on the very first commit anyway. This repo learned that lesson the
  expensive way.

---

## E. Things I'd leave exactly as they are

Not everything needs changing:

- **Ownership model.** Building it in her account from commit one, with no
  handover, is correct and worth the small extra faff of setting up in her
  name while sat next to her.
- **Email-alert ingestion as the universal escape hatch.** This is the best
  idea in the plan. It legitimately unlocks every board that exists,
  including WTTJ and LinkedIn, without a single line of scraping, and she can
  add a new source herself by making a saved search — no code, no Claude
  session.
- **The Tier A / Tier B framing.** Keep it, keep building the Tier B controls
  properly even though she'll have Claude Pro.
- **Discretion constraints in §7.** All correct, all cheap, all bake into v1.
- **cron-job.org over GitHub's native `schedule:`.** Proven in this repo.
- **Gemini Flash for scoring.** Right tool. Plenty good enough to read a job
  ad against a CV, and free without billing as long as we don't ask it to
  search.
