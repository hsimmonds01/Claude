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
