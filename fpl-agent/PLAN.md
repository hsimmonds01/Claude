# FPL Agent — build plan (2026/27 season)

An automated Fantasy Premier League assistant: it watches your actual team,
runs its own predictive model over every player, reads the news, and emails
you specific recommended moves before each deadline.

Status: **plan only — nothing built yet.** Written 24 July 2026.

---

## 1. Timing — why this is the right moment

| What | When |
|---|---|
| FPL 2026/27 game opens | ~week of 20 July 2026 (may already be live) |
| GW1 deadline | Fri 21 Aug 2026, expected 18:30 BST |
| Season runs to | 30 May 2027 |
| First chip set expires | GW19 deadline, Sat 2 Jan 2027 13:30 |

So there are roughly **four weeks before GW1**. That's comfortably enough to
build the core and have it earning its keep from the very first deadline —
including help picking the initial £100m squad, which is the single
highest-leverage decision of the season.

Rules confirmed for 2026/27 that the agent must encode:
- **8 chips** — Wildcard, Free Hit, Triple Captain, Bench Boost, one set per
  half. First set is use-it-or-lose-it at the GW19 deadline.
- **Up to 5 free transfers** can be banked (unchanged).
- **Defensive Contribution (DefCon) points stay**, and the Bonus Points
  System has been retuned to overlap less with DefCon — which shifts bonus
  towards goalkeepers, full-backs, attacking midfielders and forwards.
- **No AFCON** this season, so no December free-transfer giveaway and no
  January squad exodus to plan around.
- 11 players have been **reclassified into new positions** — worth a
  once-off check at squad-build time, as reclassifications are historically
  where value hides.

---

## 2. How it connects to your team (the important bit)

FPL's own "my team" endpoint requires being logged in, and the login is now
behind bot protection (DataDome). Scripting that means storing your FPL
password in GitHub and re-pasting browser cookies every few days when they
expire. For a system that has to run unattended for ten months, that's the
part that would break constantly.

**Recommended approach: reconstruct your team from public endpoints. No
password, no cookies, nothing to maintain.**

All of these are public and need no authentication:

| Endpoint | Gives us |
|---|---|
| `/api/entry/{id}/` | Overall points/rank, team value, bank at last deadline |
| `/api/entry/{id}/history/` | Every GW's points, rank, bank, value, transfers made, hits taken, **and every chip you've played** |
| `/api/entry/{id}/event/{gw}/picks/` | Your exact 15 players for any completed gameweek |
| `/api/entry/{id}/transfers/` | Every transfer you've ever made, with prices, updating as soon as you confirm one |

Current squad = last gameweek's picks, with every transfer made since then
applied on top. Bank = last deadline's bank, adjusted by the buy/sell prices
in the transfer feed. Free transfers available = derived from the transfer
history and the banking rules (1 per GW, cap 5, reset by Wildcard/Free Hit).

That gives the agent everything it needs to say *"sell Player X, buy Player
Y, you have 2 free transfers and £1.3m in the bank"* — with zero setup on
your end beyond telling me your team ID once.

The only things public data can't see are your **pending captain pick and
bench order for the upcoming gameweek** (private until the deadline). That
doesn't matter, because recommending those is the agent's job anyway — it
just always recommends from scratch rather than saying "change your
captain from A to B".

**Setup needed from you: your FPL team ID.** Note that the obvious page,
`fantasy.premierleague.com/en/my-team`, is the one place the ID *isn't* in
the URL. Two routes that do work:

1. **Works right now, including pre-season.** Logged in on the same browser,
   visit `https://fantasy.premierleague.com/api/me/`. It returns raw text;
   find `"entry":` and the number immediately after it is the team ID.
2. **Once GW1 has been played.** Click the **Points** tab — the URL becomes
   `.../entry/1234567/event/1`.

One-off; after that it lives in a config file.

---

## 3. Data sources

Everything below is free and keyless.

**Core — the official FPL API** (`bootstrap-static`, `fixtures`,
`element-summary/{player}`). Richer than most people realise: as well as
points, price, ownership and form it carries per-player **expected goals,
expected assists, expected goal involvements and expected goals conceded**,
plus `chance_of_playing_next_round` and the official injury `news` string
with its timestamp. It also carries per-fixture difficulty and per-team
attack/defence strength ratings. This alone supports a genuinely good model.

**Past seasons —** `element-summary` returns each player's full
season-by-season history, and the community dataset at
`github.com/vaastav/Fantasy-Premier-League` has every gameweek of every
season back to 2016/17 as CSV. Used to *calibrate* the model (how much does
last season's minutes share predict this season's? how much does xG
over/underperformance regress?) rather than to guess from vibes.

**News —** free RSS: Fantasy Football Scout, official club feeds, BBC Sport,
Google News searches for press-conference and injury keywords. Same
mechanism already working in `discovery-agent/discover.py`.

**Interpretation —** Gemini (the free key already in the repo's GitHub
secrets), with Google Search grounding where available, to read the news
pile and turn the model's numbers into a readable email.

Deliberately **not** used: scraping other people's paid prediction products
(FPL Review, Fix). Fragile, and against their terms. We build our own
numbers — which also means the emails can explain *why*, not just assert.

---

## 4. The predictive model

Not a black box, and not an LLM guessing. A transparent expected-points
model, per player, per gameweek, out to a 5-gameweek horizon:

```
xP = P(plays) × [ appearance pts
                + xG90 × goal_value(position)
                + xA90 × 3
                + P(clean sheet) × cs_value(position)
                + P(DefCon threshold) × 2
                + expected bonus ]
```

Each piece comes from real data:

- **P(plays) and expected minutes** — recent starts, substitution patterns,
  `chance_of_playing_next_round`, the injury news string and its date, plus
  rotation risk inferred from fixture congestion.
- **Attacking rates** — xG/xA per 90 from the FPL API, blended between this
  season's sample and last season's, weighted by how many minutes the player
  has actually played (so a hot three-game start doesn't get over-trusted in
  September, but does by November).
- **Clean sheet probability** — from team attack/defence ratings that the
  agent computes itself from results, updated weekly, adjusted for home/away
  and opponent. More responsive than FPL's static difficulty ratings, which
  are set pre-season and barely move.
- **DefCon probability** — from each player's own per-game defensive-action
  rate against the threshold. This is where the biggest edge currently sits,
  and the retuned bonus system for 2026/27 changes the maths again.
- **Bonus** — modelled from BPS history, adjusted for the 2026/27 retune.

Then a **fixture-aware multi-gameweek view**, so recommendations account for
a good run of fixtures rather than just the next match, and so the agent can
flag "hold — his fixtures turn in three weeks" instead of churning transfers.

**Form vs. underlying stats** is handled explicitly: the agent reports both
and tells you when they disagree ("scored 4 in two games but xG says 0.6 —
expect regression"), which is exactly the judgement call that wins seasons.

---

## 5. The recommendation engine

Given your reconstructed squad, bank, free transfers, chip status and the
model's forecasts, a constrained optimiser answers the real questions:

- **Transfers** — evaluates 0, 1, 2 and hit-taking options over the next 5
  gameweeks, respecting budget, the 3-per-club limit and formation validity.
  Recommends taking a −4 only when the projected gain clears a threshold.
- **Captain** — ranked shortlist with projected points and a risk note
  (safe/differential), plus Triple Captain flagging when a fixture is worth it.
- **Starting XI and bench order** — best legal formation from your 15.
- **Chip strategy** — tracks both chip sets and the GW19 expiry, watches for
  announced double and blank gameweeks, and starts nudging when a chip is at
  risk of being wasted.
- **Ownership gap** — see §5a; the primary risk metric for a template-led
  strategy.
- **Wildcard / initial squad** — a full squad optimiser (linear programming)
  that builds the best legal £100m 15 for a given horizon. This is what
  powers the GW1 squad recommendation.

Sanity rail: the numbers come from the optimiser, never from the language
model. Gemini writes the prose around them and adds the news context; it is
never asked to invent a projection. That's what keeps the emails trustworthy.

---

## 5a. Strategy: template-led, with controlled differentials

**Decided:** mainly template, with room for differentials. That is a
measurable strategy, not a mood, so it's implemented as one.

**Ownership gap is the primary risk metric.** Each week the agent lists the
players above a high ownership threshold (roughly 25–30%+) that you *don't*
own, and quantifies the exposure: if that player hauls, what does it cost
you in rank? For a template-led manager, missing players is a bigger
long-term threat than owning a bad one, and it's the thing most people track
by feel. Filling template holes therefore ranks above speculative upgrades
when the optimiser breaks a tie.

**Differentials get a budget, not a veto.** Two or three squad slots are
treated as licensed for sub-10%-owned players, and a differential is only
proposed when the model's projection genuinely beats the template
alternative — not merely because it's contrarian. Being different is a cost
the projection has to pay for.

**Captaincy is where template discipline matters most**, so the captain
section always shows both: the safe (high effective-ownership) pick, and the
differential, with the projected gain *and* the rank downside if it misses.
You make the call with both numbers in front of you.

All of this is tunable in plain English in `strategy.md` — including
hard rules like "never take a −8" — and the balance can shift as the season
develops (protecting a good rank in April is a different game from chasing
in September).

---

## 5b. The dashboard (build later, spec now)

A web dashboard that **looks and feels like the FPL app** -- the same pitch
view and squad layout you already know -- but populated with our projections
instead of FPL's. Requested 2026-07-27; deliberately scheduled after the
weekly email is live, because the email is what actually changes decisions.

What it does:
- **Pitch view of your squad**, FPL-app styling, each player showing our
  projected points, start probability and price alongside the usual info.
- **Click a player -> "swap him out"**, and it lists affordable replacements
  ranked by what they do to your XI projection, respecting your bank, the
  3-per-club limit and position. This is `optimiser.py`'s
  `suggest_transfers` with a UI on top -- the logic already exists.
- ~~A prompt box wired to Gemini~~ **- built, then removed 28 Jul 2026.**
  It was the only thing on the page needing an API key, and a key on a
  public page is a liability the rest of the dashboard simply doesn't have:
  GitHub Pages serves every project site for an account from one origin, so
  browser storage there is shared with every other page the account
  publishes. Restricting a key properly also meant a second key, because the
  existing one is used server-side by Actions where an HTTP-referrer
  restriction would block it. Real setup cost and real ongoing risk, for a
  feature whose questions are better asked in a Claude session that already
  has the same data and keeps the key server-side.
- The recommended changes for the current gameweek, mirroring the email.

Build notes:
- Static HTML + JS reading committed JSON -- no server to run or pay for.
- **Served by GitHub Pages**, which is already enabled on this repo
  (`main`, root): https://hsimmonds01.github.io/Claude/fpl-agent/dashboard.html
  Official, no rate limits, no third-party dependency. Do NOT use
  raw.githack for this project -- it's a one-person free proxy that every
  dashboard link would break with. (The older projects still use it; leave
  them alone unless asked.)
- The dashboard holds no credentials and calls no external API. Everything
  it shows comes from committed JSON generated by the same optimiser the
  emails use, so the page and the email can never disagree.

---

## 6. What lands in your inbox

| Email | When | Contains |
|---|---|---|
| **Main deadline email** | 18–36h before the deadline, scaled to congestion (see below) | Recommended transfers with reasoning, captain shortlist, starting XI, ownership gap, price warnings, chip advice, model vs. news conflicts |
| **Final check** | ~3h before the deadline, **only if something changed** | Late injury, a flagged player in your XI, a press-conference bombshell, a manager's "he's a doubt" |
| **Midweek alert** | As needed | Big injury to one of your players, a player of yours about to rise/fall in price, double/blank gameweek confirmed, a must-act window on a rising asset |
| **Post-gameweek review** | When the gameweek's points go final (**not** a fixed weekday) | How you did, rank movement, what the model got right and wrong, points left on the bench |

**Decided:** the review is a guaranteed email, not conditional. So the
baseline is two per gameweek — recommendations, then review — plus
conditional extras.

### Handling midweek gameweeks

Nothing is scheduled by day of the week. The API publishes every deadline,
and a `data_checked` flag that flips true once a gameweek's points are final
(bonus applied, no further changes). The agent runs a check every few hours
and asks only "is an email due right now?" — so a Saturday round, a Tuesday
round and a festive pile-up are all handled by the same logic, with no
special cases and nothing to adjust by hand.

**Lead time scales with congestion.** A fixed 36h breaks in midweek rounds:
for a Tuesday 18:30 deadline it lands Monday breakfast, *before* the Monday
afternoon press conference — missing the news the timing exists to capture.

| Gap since previous deadline | Main email | Final check |
|---|---|---|
| ≥6 days (normal week) | 36h before | 3h before |
| 3–5 days (midweek round) | 24h before | 3h before |
| <3 days (festive crush) | 18h before | only if your XI is affected |

**The review fires on data, not on a weekday** — when the previous
gameweek's points go final. Typically Tuesday after a weekend round;
Thursday after a midweek one.

**Emails merge when they would collide.** If a review comes due within 24h
of the next main email, they combine into one — "here's how last week went,
and here's what to do next". During a congested run that becomes the normal
case, which is the right outcome: in a midweek week you want one good email,
not three fragments.

**Hard cap of 3 emails per rolling 72 hours**, with one override that always
sends: your captain or a starting player becoming a doubt.

### Tone and depth

**Decided:** well-rounded and thorough. The main email is a full briefing,
not a one-line tip — recommended move *and* the case for doing nothing,
costed; a ranked captain shortlist rather than a single name; the ownership
gap; the 5-gameweek fixture ticker; and an explicit section wherever the
model and the news disagree. The headline verdict sits at the top so it's
skimmable on a phone, with the reasoning underneath for when you want it.

Same delivery stack already proven in this repo: Resend for email, and a
`strategy.md` file (the equivalent of `discovery-agent/interests.md`) where
you set preferences in plain English and the agent respects them.

---

## 7. How it fits the repo

New `fpl-agent/` directory, following the patterns already working here:

```
fpl-agent/
  fpl_agent.py        entry point + modes (--dry-run, --test-email, --force)
  fpl_api.py          official API client + caching
  squad.py            reconstruct current team from public endpoints
  model.py            expected points model
  optimiser.py        transfers, captain, XI, wildcard/initial squad
  news.py             RSS + Gemini research layer
  email_render.py     HTML email
  strategy.md         your preferences, in plain English
  state.json          last-sent tracking, dedupe
  history.json        every recommendation + what actually happened
  dashboard.html      browsable season history
  data/               cached API snapshots, model calibration
```

A single GitHub Actions workflow (`.github/workflows/fpl-agent.yml`) that:
- runs as a **"tick" every few hours and decides for itself what to do** by
  reading the real deadlines and `data_checked` flags from the API (§6) —
  the same window-gating trick `check_docks.py` uses, because GitHub's own
  scheduler is unreliable (documented in `CLAUDE.md`). `state.json` records
  what has already been sent, so overlapping or duplicate triggers can never
  double-email — the same guard `discover.py` uses;
- is triggered primarily by **cron-job.org** hitting `workflow_dispatch`
  (your existing account and PAT already do this for two other projects),
  with GitHub's native schedule as backup;
- joins the **`main-git-writer` concurrency group** — mandatory for anything
  in this repo that pushes to `main`, per `CLAUDE.md`;
- reuses the existing `GEMINI_API_KEY` and `RESEND_API_KEY` secrets. **No
  new accounts, no new keys, no cost.**

One constraint found while researching: this development sandbox cannot
reach `fantasy.premierleague.com` (the network proxy blocks it), though
GitHub Actions runners can. So the build uses **recorded API fixtures for
local testing** and verifies live behaviour through Actions dry-runs — the
same "mock external APIs in the sandbox" approach `CLAUDE.md` already
records for the other projects.

---

## 8. Build order

Sized in Claude sessions, and ordered so that each phase is independently
useful — if we stop after any one of them, you still have something working.

**Phase 1 — See your team, and build the GW1 squad.** *(~1 session, before GW1)*
API client, squad reconstruction, the initial-squad optimiser, and the first
real email. Deliverable: an email recommending your opening £100m squad with
reasoning. Testable immediately.

**Phase 2 — The model and the weekly email.** *(~1–2 sessions, live by GW1)*
Expected points model, 5-gameweek horizon, transfer/captain/XI optimiser,
the main deadline email, and the automated schedule. This is the core
product. After this it runs itself every week.

**Phase 3 — News, review email, late-breaking alerts.** *(~1 session, by ~GW2)*
RSS + Gemini research layer merged into the recommendations, the guaranteed
post-gameweek review email, the merge-when-colliding logic, the final-check
email, and midweek injury/price alerts. Pulled earlier than originally
planned now that the review is a fixed weekly email rather than optional.

**Phase 4 — Memory and calibration.** *(~1 session, by ~GW6)*
History archive, the browsable dashboard, and model calibration against past
seasons — scoring the agent's own past projections against what actually
happened, so the model measurably improves rather than just existing.

**Phase 5 — Chip and season strategy.** *(~1 session, autumn)*
Double/blank gameweek detection, chip planning against the GW19 expiry,
mini-league-aware advice (chase or protect, depending on your rank).

Phases 1 and 2 are the ones that matter before 21 August. The rest can land
during the season without disruption.

---

## 9. Honest limitations

- **It recommends, it doesn't act.** Making transfers automatically needs
  the authenticated API, and I'd argue against it even if it were easy —
  you'd be handing over the decision that makes the game fun, to a model
  that's right maybe 60% of the time.
- **Nobody's projections are that accurate.** A good model gains a few
  points a week over a well-informed manager, mostly by avoiding mistakes
  (playing injured players, missing price rises, wasting chips). Expect an
  edge, not clairvoyance. The post-gameweek review will show you honestly
  how it's tracking.
- **Price-change predictions are estimates.** FPL now ships its own price
  prediction tool for 2026/27; the agent will read the official transfer
  numbers rather than pretending to know the secret formula.
- **Deadline timing needs care.** Deadlines move (midweek rounds, festive
  fixtures). The agent always reads the live deadline from the API rather
  than assuming Saturday 11am — that's the single most likely source of a
  missed email, so it's designed out from day one.

---

## 10. Decisions made, and what's still open

**Settled:**
- **Email volume** — two guaranteed per gameweek (recommendations + review),
  plus conditional final-check and midweek alerts, capped at 3 per 72h (§6).
- **Midweek gameweeks** — no weekday scheduling anywhere; congestion-scaled
  lead times, data-driven review timing, and automatic merging when emails
  would collide (§6).
- **Style** — template-led with a controlled differential budget, tracked via
  the ownership-gap metric (§5a).
- **Depth** — thorough full briefings, headline verdict first for phone
  skimming (§6).

**Still open:**
1. **Team ID** — the one input needed to point Phase 1 at your real team.
   See §2: `fantasy.premierleague.com/api/me/` while logged in, find
   `"entry":`. Phase 1 builds fine without it if the game hasn't opened yet;
   it just plugs into a config file when you have it.

Nothing blocks starting Phase 1.
