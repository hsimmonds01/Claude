# Repo notes / working preferences

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
  (root `index.html`/`app.js`/`config`/`data`/`scripts`), and a Northern
  line crowding tracker (`crowding-tracker/`, branch
  `claude/northern-line-busyness-rn7d9m`). Each has its own GitHub Actions
  workflow that commits state/history back to `main` on a repeating
  schedule.
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
