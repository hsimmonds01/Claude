# Deals & Codes

A push-notification agent that checks a few times a day for genuinely good
money-saving finds — big price drops that are real (not RRP-inflated
"was £200 now £99" theatre) and working voucher/discount codes — and pushes
an ntfy notification only when something clears a high bar. Free to run, no
billing anywhere.

**Most checks should send nothing.** Silence is the correct, expected
outcome — a mediocre deal getting pushed is treated as a failure, not a
catch. See "Notification fatigue" below for how that's enforced.

## How it works

```
cron-job.org (reliable scheduler, fires a few times a day)
   └─> GitHub Actions workflow_dispatch  (.github/workflows/deals-agent.yml)
         └─> deals.py
               ├─ fetches HotUKDeals (trending + new) and Reddit r/UKDeals
               │    .............. free, keyless RSS/Atom, no billing
               ├─ filters vs state.json ..... never re-notifies about the
               │                              same deal twice (fuzzy match)
               ├─ deterministic pre-filter ... heat velocity (heat ÷ age,
               │                              not raw heat) + code mentions
               ├─ asks Gemini to RANK ONLY .. picks indices from a numbered
               │    candidate list built entirely from fetched text; never
               │    generates a url, code, or title (see "Never invented"
               │    below)
               ├─ daily cap + shadow mode .... enforces silence as the
               │    default outcome
               ├─ pushes via ntfy ............ only for what clears the bar
               └─ archives to history.json ... every run's candidates and
                    decisions, for tuning
```

GitHub's native schedule (08:05/12:05/16:05/20:05 UTC) is a backup net
only — cron-job.org hitting `workflow_dispatch` is the reliable primary
trigger, same pattern as the sibling projects. The daily cap and permanent
seen-dedupe make duplicate/overlapping triggers safe.

## Sources

Confirmed reachable from a GitHub Actions runner (the dev sandbox's own
proxy blocks all candidate deal sites outright, so this could only be
checked by actually running `probe_sources.py` on a runner):

| Source | Status |
|---|---|
| **HotUKDeals** `/rss/trending` + `/rss/new` | Works — the primary source. Community heat is the free proxy for "is this actually a good price"; it's embedded as a `"115° - "` prefix in each item's title, not a separate field. |
| **Reddit r/UKDeals** `.rss` | Works, but rate-limits fast — the probe found a second rapid request from the same runner IP got `429`'d after the first succeeded. `deals.py` makes exactly **one** Reddit request per run. |
| LatestDeals | Returned `202 Accepted` with an empty body from a runner, both feed URLs tried. Not usable; dropped. |
| MoneySavingExpert | Blocked by a Cloudflare JS challenge. See "MSE and other blocked sources" below. |
| VoucherCodes / MyVoucherCodes | Deliberately not used — affiliate-driven, high rate of stale/dead codes. |

## Never invented: codes and links

**A code or URL in the model's reply is never trusted — because there's no
field for the model to put one in.**

discovery-agent (the sibling project this is modelled on) shipped a digest
linking to `youtube.com/watch?v=dQw4w9WgXcQ` — the rickroll — because an AI
asked for "a link" invented a plausible one when it didn't have a real one.
Asked for "a Deliveroo code," a model will invent `DELIVEROO10` just as
confidently.

So Gemini here is used **only as a ranker**. It's shown a numbered list of
candidates built entirely from feed text this run actually fetched, and its
entire output schema is:

```json
[{"index": 3, "reason": "one honest sentence"}]
```

The url, merchant, price, and any extracted code are filled in **afterwards**
by looking up that index in our own parsed feed data — never from the
model's reply. `parse_selection()` only ever reads `index` and `reason`;
any other key the model includes is silently discarded. There is no code
path by which a hallucinated url or code could reach a notification, even
if the model tried to smuggle one in (there's a test for exactly this:
`test_selection_ignores_extra_model_supplied_fields`).

Codes themselves are extracted by regex from the fetched title/description
text only, never generated. A code the extractor misses is a missed catch;
a code it invents would be the exact failure this design prevents.

## Codes can't be verified — so they're never framed as guaranteed

There's no way to confirm a voucher code works without an account and a
live basket. Every code notification is framed honestly:

> Code: SAVE20 — reported working 3h ago on HotUKDeals, NOT verified

...and flagged if the source text mentions "new customers only" / "first
order," since most posted food-delivery codes are new-customer-only and a
push that doesn't say so is a dead end dressed as a good find.

## Notification fatigue — the main design risk

A push interrupts in a way email doesn't, so the whole design leans toward
silence:

- **Deterministic pre-filter before Gemini ever runs**: a HotUKDeals item
  only becomes a candidate if its heat *velocity* (heat ÷ hours since
  posted) clears `MIN_HEAT_VELOCITY`, or it mentions a code/voucher. Raw
  heat alone rewards deals that have simply had longer to accumulate votes,
  not ones that are hot *right now* — velocity is what wins here.
- **Gemini is told to be skeptical by default** and that returning `[]`
  (nothing) is the expected, correct answer most runs — not a hedge.
- **Hard daily cap** (`MAX_PUSHES_PER_DAY`, currently 3): once hit, anything
  else that would have qualified is logged as `capped_out` in
  `history.json` instead of pushed, so nothing is lost from view — just
  from your phone.
- **Permanent per-item dedupe**: once a deal is selected (pushed or logged
  in shadow mode), its fuzzy-matched title goes into `state.json`'s `seen`
  list and is never proposed again, cross-source (the same deal posted to
  both HotUKDeals and Reddit is also collapsed to one before ranking).
- **A separate ntfy topic** (`DEALS_NTFY_TOPIC`), deliberately with no
  hardcoded fallback — so muting this can never accidentally mute or get
  confused with the dock-alerter's topic.

## Shadow mode — how the bar gets tuned

`DEALS_SHADOW_MODE` is a repo **variable** (not secret). Unset, or anything
other than `false`, means shadow mode: every run still fetches, filters,
ranks, and writes to `history.json` exactly as normal — it just skips the
actual `ntfy` POST. This is deliberate and safe-by-default: the very first
deploy can't push anything before a real week of logs has been reviewed.

To tune: let it run in shadow mode for about a week, then read
`history.json` (or ask Claude to summarise it) — every candidate considered,
what Gemini picked and why, and anything that would have been capped. Adjust
`MIN_HEAT_VELOCITY`, `MAX_PUSHES_PER_DAY`, or `interests.md` from that
evidence, then set the `DEALS_SHADOW_MODE` repo variable to `false` to turn
on real pushes.

## MSE and other blocked sources

MoneySavingExpert sits behind a Cloudflare JS challenge from a datacentre
IP. Deliberately not fought: a headless-browser workaround would be an
arms race against a control designed specifically to stop scripts like this
one, is the kind of thing site terms of service typically forbid, and would
break unpredictably whenever Cloudflare rotates its challenge — not worth
it for a tertiary source when HotUKDeals + Reddit already answer the core
"is this genuinely a good price" question.

A clean alternative exists if MSE coverage is wanted later:
[kill-the-newsletter.com](https://kill-the-newsletter.com) converts an
email subscription (e.g. MSE's own free weekly newsletter) into a plain
RSS feed with no scraping, no bot wall, and no inbox access needed — the
same free-keyless-feed pattern already used here. Not built yet; the feed
URL it issues would need to be stored as a secret (not committed), the same
way `DEALS_NTFY_TOPIC` is.

## The pieces

| File | What it is |
|---|---|
| `interests.md` | **The quality bar — edit this to retune what counts as genuinely good.** Plain English. |
| `deals.py` | The agent: fetch feeds → filter → rank → notify → archive. |
| `probe_sources.py` | One-off diagnostic (not part of the agent) that tested which candidate sources respond from a GitHub runner. Manual-trigger workflow only, sends/writes nothing. |
| `state.json` | Daily push-cap counter + permanent seen-item memory (auto-committed). |
| `history.json` | Append-only log of every run's candidates and decisions (auto-committed) — read this to tune. |
| `test_deals.py` | Offline tests, network stubbed (`python test_deals.py`). |

## Secrets / variables required (repo Settings → Secrets and variables → Actions)

- `GEMINI_API_KEY` — the same free key already used by discovery-agent
  (its own project, doesn't share quota).
- `DEALS_NTFY_TOPIC` — **secret**, not variable. A topic name only you know,
  subscribed to in the ntfy app. Deliberately separate from the
  dock-alerter's topic so muting one never touches the other.
- `DEALS_SHADOW_MODE` — **variable**, not secret. Leave unset (or `true`)
  during tuning; set to `false` once ready for real pushes.

## Run modes (Actions → Deals & Codes agent → Run workflow)

- `check` — the normal run (fetch, filter, rank, push-or-log, commit state)
- `dry_run` — fetch, filter, rank, print — no ntfy, no state/history writes
- `test_ntfy` — send one clearly-labelled real test push through the real
  ntfy path, nothing else touched

## Tuning

Edit `interests.md` — next run picks it up automatically. Numeric
thresholds (`MIN_HEAT_VELOCITY`, `MAX_PUSHES_PER_DAY`, `MAX_REDDIT_CANDIDATES`)
are constants at the top of `deals.py`, each with a comment explaining the
reasoning — change them from evidence in `history.json`, not guesses.
