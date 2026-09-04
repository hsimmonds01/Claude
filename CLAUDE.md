# Repo notes / working preferences

## THIS REPO IS PUBLIC — privacy rules (read before committing anything)
- `hsimmonds01/Claude` is a **PUBLIC** repo. Verified 2026-07-27 via
  `gh repo view --json visibility` -> `"PUBLIC"`. Everything committed here
  is readable by anyone on the internet, and **stays in git history even
  after the file is deleted** -- a later `rm` does not un-publish it.
- **Privacy matters to the user, and they want to be told, not assumed.**
  Before writing or committing anything containing personal information,
  STOP and say plainly that it will be world-readable and who can see it.
  Wait for a decision. Do not quietly commit and mention it afterwards.
  Treat as personal information: real names, CVs, email addresses, phone
  numbers, home/work locations, employer names, salary figures, health or
  financial details, notification topic names, and anything identifying the
  user's routine or whereabouts.
- **This applies to files the user hands over too.** If they paste or upload
  something that would be exposed by committing it, flag it before it goes
  in -- the moment to raise it is *before* the write, not in the summary.
  "You asked me to add it" is not a reason to skip the warning.
- **Default to synthetic stand-ins.** Test with fabricated data and keep the
  real thing out of the repo entirely. Where real data is genuinely needed
  at runtime, it belongs in GitHub Actions **secrets**, never in a file.
- **Third parties get the same protection or stricter** -- they haven't
  chosen to have anything of theirs in a public repo. Live example: the
  `job-agent-plan/` project is built for someone who is job-hunting while
  still employed, so her CV, filled-in preferences, target-company list and
  contact details are banned from this repo outright. See that folder's
  `PLAN.md` banner.
- Existing per-project state files (`state.json`, `history.json`, crowding
  logs, etc.) are already public and are fine -- this rule is about not
  making the exposure *worse*, not about auditing what's already there.

## dock-alerter project
- Santander Cycles dock/bike alerter for Tooley Street, Bermondsey
  (`BikePoints_278`). See `dock-alerter/README.md` for full functional docs.
- On-demand checks should use `--force-mode status` (reports both docks and
  bikes, no thresholds). `check` / `evening_check` are tied to
  morning-docks / evening-bikes semantics respectively and only alert on
  threshold breaches -- don't use them for "just tell me what it looks like
  now" requests.
- GitHub Actions `schedule` triggers are best-effort and can run significantly
  late (seen >1hr delays, missing the monitoring window entirely on both the
  morning and evening run on the same day). If a notification doesn't arrive,
  check actual run timestamps via Actions history / job logs before assuming
  a code bug -- look for `Nothing to do at ... (outside monitoring window or
  weekday)` in the logs, which confirms the script behaved correctly and the
  scheduler was just late.

## Shared repo infrastructure (multiple projects, one repo)
- This repo (`hsimmonds01/Claude`) hosts several independent projects side
  by side, each on its own long-lived branch: dock-alerter
  (`claude/santander-cycles-alerter-krn8d0`), a World Cup fantasy tracker
  (root `index.html`/`app.js`/`config`/`data`/`scripts`), a Northern
  line crowding tracker (`crowding-tracker/`, branch
  `claude/northern-line-busyness-rn7d9m`), page-watchers (`ticket-alerter/`
  retired, `voxi-drop-alerter/`), and a Daily Discovery email digest
  (`discovery-agent/`, branch `claude/daily-discovery-agent-estimate-qasmi1`:
  Gemini free API + Google Search grounding -> Resend email; taste profile
  lives in `discovery-agent/interests.md`), and a Fantasy Premier League
  agent (`fpl-agent/`, branch `claude/fantasy-football-agent-plan-fg86nk`).
  Each has its own GitHub Actions workflow that commits state/history back
  to `main` on a repeating schedule.

## fpl-agent project
- Expected-points model + squad optimiser for the user's FPL team, aiming at
  deadline emails with recommended transfers. `fpl-agent/PLAN.md` is the
  source of truth for scope and build order.
- The dev sandbox CANNOT reach `fantasy.premierleague.com` (agent proxy
  returns 403 on CONNECT), but Actions runners can. That's why
  `snapshot.py`/`history.py` run as a workflow that commits data back --
  don't try to debug the API from a session, push and read the committed
  snapshot instead. `raw.githubusercontent.com` IS reachable, so the
  gameweek archive (`gwdata.py`) can be fetched locally.
- FPL's own site is a React app: `/help/rules` returns a ~115-char shell to
  a plain fetch, so `knowledge.py` falls back to Playwright. Team
  attack/defence strength fields are all ZERO pre-season -- use fixture
  difficulty until results exist.
- Model changes must be TESTED, not reasoned about. `backtest.py`,
  `backtest_gw.py`, `minutes.py --evaluate` and `minutes.py --evaluate-in-season`
  exist for this. Two plausible ideas were already built and then removed for
  failing to replicate (a club-move discount, an expensive-player penalty) --
  `minutes.py` records what was rejected and why, so they don't get
  reinvented. Beware judging a change on one season: recency weighting
  looked like a clear regression on 2025/26 alone and was right on
  aggregate.
- Found 3 Sep 2026 (GW3): a returning player's start probability was frozen
  entirely on LAST season's archive, with no mechanism to notice he's
  started every game of the new one -- `player["starts"]` from the live
  snapshot sat unused in `players.csv` the whole time. A manager's pushback
  on Calafiori being repeatedly flagged as a sell (56% of last 10 GWs
  started in 2025/26, but 2/2 so far in 2026/27) is what surfaced it.
  `minutes.py`'s `PRIOR_GAMES=2` now blends the archive prior with the
  season under way, tested by walking forward inside three seasons of
  archive data rather than reasoned about -- see `minutes.py --evaluate-in-season`
  and the module docstring for the numbers. Re-run that check early each
  new season once a few gameweeks exist, the same way `--evaluate` gets
  re-run when a new season's archive lands.
- Any workflow that commits and pushes to `main` MUST join the
  `main-git-writer` concurrency group (`cancel-in-progress: false`) --
  otherwise its push can race another project's workflow and get rejected
  as non-fast-forward (`! [rejected] main -> main (fetch first)`). Found
  this on 2026-07-13: three workflows (dock-alerter, the crowding logger,
  and the World Cup updater) were colliding, and the fix rolled out to all
  three in separate commits minutes apart, leaving one unprotected in
  between and still failing. Add the group name to any NEW workflow's
  `concurrency:` block up front -- don't wait for a collision to discover
  it's needed. Notifications/side-effects sent earlier in a run are
  unaffected by a later push failure (e.g. dock-alerter's ntfy alert fires
  before the git commit step), so a push race is usually just a lost log
  row, not a lost alert -- still worth fixing, but not urgent/scary when
  it happens.
- Because these projects share one repo, root-level `CLAUDE.md` is
  effectively already the "all sessions read this first" file -- a new
  session starting from `main` picks it up automatically. It only fails to
  reach a session that's already running on a different, not-yet-synced
  branch (that session needs to merge/rebase onto `main` to see updates
  made elsewhere).
- When something in one project looks broken, check whether a SIBLING
  project's automation is the actual cause before assuming a bug in the
  project under discussion -- e.g. `git log` across ALL commits to `main`
  around the relevant time (not just the project's own files) to see what
  else touched the branch. This is how the git-push-race above was found:
  the failing commit's author was the right bot, but the colliding push
  turned out to belong to a different project's workflow entirely.
- `squad.py` (added 1 Sep 2026) reconstructs the real squad from the FPL API
  once a gameweek's deadline has passed -- `/api/entry/{id}/event/{n}/picks/`
  needs no login at that point. The team ID lives ONLY in the `FPL_TEAM_ID`
  GitHub secret, never in `my_squad.json` -- that file is committed to this
  public repo, and FPL's entry endpoint returns the manager's real first/
  last name alongside the team data, so the ID and the name must never sit
  in the same public file. `squad.py` fetches both but only ever writes
  team/points/rank data to disk, dropping the name fields unconditionally;
  `test_squad.py` asserts this. Can't be tested against the real API from a
  session (same network block as above) -- verify logic changes with
  `test_squad.py`'s synthetic payload, and treat the first live Actions run
  after any change here as the actual test.

## chelsea_tickets project
- Watches Chelsea's men's ticket page for HOME games at Stamford Bridge and
  alerts (ntfy) on two events only: a new home fixture being listed, and a
  members' ticket application window opening. Harry is a **True Blue** member,
  not a season ticket holder. `chelsea_tickets/README.md` is the functional
  doc; `PLAN.md` is the design record.
- The fixture list is NOT in the page HTML -- chelseafc.com is a React app.
  Data comes from its own public JSON feed:
  `GET https://www.chelseafc.com/en/api/fixtures/tickets?pageId=<cms-id>`
  (no auth, reachable from the dev sandbox, unlike the FPL API). The endpoint
  was found by walking `runtime.js`'s webpack chunk map to the
  `fixturesAndResults` service -- worth remembering as a technique when a
  site's data is client-rendered.
- **Chelsea never says "ballot".** It is a "Ticket Application Window", open
  to True Blue+, True Blue, CFC Blue and Junior Blue. Their own copy says
  applying early in a window confers no advantage, so a 30-minute poll is
  ample -- don't "optimise" the interval down.
- **A sale window with NO `status` key is OPEN.** Chelsea only attaches a
  status once it is `Off Sale` or `Sold Out`. Inverting this would mean never
  detecting an open ballot. Fixtures are keyed by CMS `id`, never team name.
- `state.json`'s `notified` map is an AUDIT LOG, not a mute list. Suppression
  is the snapshot diff's job. Gating on those keys as well permanently
  swallowed a window that closes and re-opens (second-batch releases) -- found
  by an end-to-end test, regression test now covers it.
- Deliberately never touches `eticketing.co.uk` (returns 401 anonymously). No
  login automation, no credentials, no auto-buying -- the design needs none,
  which is part of why it was chosen for a public repo.

## Git / GitHub workflow preferences
- Standard cycle: implement -> test locally (mock external APIs where the
  sandbox has no network access) -> commit -> push to
  `claude/santander-cycles-alerter-krn8d0` -> open a PR -> squash-merge to
  `main`.
- Never force-push without explicit permission. If a branch diverges from
  remote unexpectedly, recover via `git reflog` + `cherry-pick` rather than
  resetting/force-pushing.
- If a PR shows `mergeable_state: "dirty"`, don't assume it's just GitHub
  async lag after one recheck -- simulate the merge locally (e.g. via a
  worktree) to find the actual conflicting file, resolve it, push, then
  recheck before merging.

## GitHub PAT / Shortcuts gotchas (for the iOS Shortcuts integration)
- Editing a fine-grained PAT's permissions in the GitHub UI does NOT
  retroactively change an already-copied token string -- must regenerate and
  re-copy the new token into every Shortcut action that uses it.
- The mute-flag Shortcut has two separate "Get Contents of URL" actions (GET
  for sha, PUT for the update), each with its own independent Authorization
  header -- both need updating after a token regeneration, not just one.
- A `409` response on a Contents API PUT means auth succeeded (token is
  fine) but the `sha` was stale/missing -- different root cause than
  `401`/`403`.

## External scheduling (cron-job.org)
- GitHub Actions' native `schedule` trigger proved unreliable in practice
  (seen >1hr delays on both the morning and evening window on the same day),
  so cron-job.org pings the same `workflow_dispatch` endpoint as a more
  reliable primary trigger. GitHub's native schedule is left in place as a
  harmless backup -- `check_docks.py`'s own window-gating and alert cooldowns
  make duplicate/overlapping triggers safe (extra runs just no-op or get
  throttled, never double-alert).
- One cron-job.org job covers both windows by selecting multiple individual
  hours (6, 7, 8, 16, 17, 18 UTC) rather than needing two separate jobs --
  check whether the scheduler UI offers per-hour checkboxes vs. only a single
  continuous range before assuming two jobs are required.
- Reuses the same fine-grained PAT already issued for the iOS Shortcuts --
  no need for a separate token.
- The user's cron-job.org account has its jobs' schedule timezone set to a
  Europe zone (e.g. Europe/London), not UTC -- this means cron-job.org
  itself handles the BST/GMT clock change, so a job scheduled for "8am"
  fires at 8am UK time year-round with no manual DST adjustment needed.
  This applies across all cron-job.org jobs in this account (dock-alerter,
  Daily Discovery, etc.) -- don't flag the UTC/BST drift as a concern for
  cron-job.org-triggered schedules; it only applies to GitHub's native
  `schedule:` cron (which is always UTC and has no timezone setting) kept
  as the backup trigger.

## Communication / working style preferences
- The user is not a developer -- when a step requires action on their end
  (third-party site setup, iOS Shortcuts, GitHub UI clicks), give complete,
  plain-language, numbered instructions naming exact buttons/labels to look
  for, not technical shorthand. Don't assume familiarity with cron syntax,
  HTTP, tokens, etc. -- explain inline the first time, briefly.
- Don't take the easy/assumed answer when something doesn't work as
  expected (e.g. "nothing happened," a PR stuck on `dirty`) -- verify against
  real evidence (Actions run logs, job timestamps, actual merge attempts)
  before concluding root cause, even if that means a second or third check.
  This was the pattern across diagnosing the stale-token Shortcut failures,
  the GitHub Actions scheduling delays, and the PR #25 merge conflict.
- The user values visual/diagram explanations of how the system works, not
  just prose -- prefers color-coded A4 diagrams: portrait for
  component/architecture views, landscape for time-based flow views. Built
  via HTML/CSS rendered through headless Chromium (Playwright is
  pre-installed in this environment) rather than an AI image generator,
  since accurate text/arrows/layout matter more than illustrative style.
- Default working rhythm: implement/fix -> ship via the standard PR cycle
  above -> proactively suggest 2-3 concrete "what's next" options scoped
  with rough effort, rather than waiting to be asked. The user is happy to
  pick from a short list rather than be handed one prescribed plan.
- Runs Claude Code sessions primarily via phone/web rather than a local
  CLI -- keep this in mind when reasoning about what persists between
  sessions (repo-committed files, e.g. `CLAUDE.md`, yes; local machine
  state or a personal/global config outside the repo, not reliably, since
  each session is a fresh container).
- Enjoys real-world analogies when learning git/GitHub mechanics, and asks
  good follow-up questions when curious rather than just accepting "it's
  done" -- e.g. branch = a photocopy taken to work on, PR = an approval
  note clipped to the photocopy, merge = copying the approved changes into
  the master document. Lean into this style for infrastructure/process
  explanations rather than pure technical definitions.

## About the user
- Lives in south-east London, Bermondsey/Elephant and Castle area -- London
  events and drops near there land best; the dock-alerter's own location
  (Tooley Street, Bermondsey) is a good landmark for "near me."
- Interests feeding `discovery-agent/interests.md` (the taste profile for
  the Daily Discovery digest -- edit that file directly to retune, this is
  just the source-of-truth summary): big-screen film events (IMAX
  releases/re-releases, e.g. wanted to know about "The Odyssey" IMAX
  tickets); limited-edition drops and collabs (streetwear/caps, e.g. the
  Wynwood x Strawberry Stellar Ottawa hats); football; tech and new
  product releases, especially affordable ones; milkshakes and good food,
  including discounts/deals; London events near Elephant and Castle.
- Particularly likes free-to-enter promos that add real value rather than
  just marketing noise -- example given: Nando's ran a World Cup
  score-prediction game with rewards during the tournament. Surface this
  kind of thing (free sign-up, low effort, genuine perk) alongside paid
  ticket/drop finds in the digest, not just things that cost money.
- Not a developer; runs Claude Code sessions primarily via phone/web (see
  Communication preferences above) but is comfortable following clear
  numbered instructions for third-party site setup (Google AI Studio,
  Resend, GitHub secrets, cron-job.org) and has an existing cron-job.org
  account already used for the dock-alerter.
