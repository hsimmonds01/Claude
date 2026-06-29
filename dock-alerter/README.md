# Tooley Street dock alerter

Pushes a phone notification when the Santander Cycles docking station at
**Tooley Street, Bermondsey** (`BikePoints_278`) is getting full, so you
know before you arrive whether you'll be able to dock your bike.

Data comes from the [TfL Unified API](https://api.tfl.gov.uk/) (no API key
needed). Notifications are sent via [ntfy.sh](https://ntfy.sh) (free, no
account needed).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for diagrams of how the pieces fit
together and how a day's checks play out over time.

## How it works

**Morning (outbound commute) -- watches empty docks**, so you know if
you'll be able to dock your bike at Tooley Street later:

- **07:45** (Europe/London time) -- a one-off morning summary: current
  empty-dock count, sent regardless of how full the station is.
- **08:00-08:45** -- checked every 5 minutes.
  - If empty docks drop below `LOW_DOCKS_THRESHOLD` (default **3**), you
    get a high-priority alert.
  - Once it recovers to `ALL_CLEAR_DOCKS_THRESHOLD` (default **5**) or more
    *after* an alert was sent, you get an "all clear" notification.

**Evening (return commute) -- watches available bikes**, so you know if
you'll be able to pick one up to ride home:

- **17:15** -- a one-off evening summary: current available-bikes count.
- **17:30-18:00** -- checked every 5 minutes.
  - If available bikes drop below `LOW_BIKES_THRESHOLD` (default **3**),
    you get a high-priority alert. The alert also looks up **Snowsfields,
    London Bridge** as a nearby backup and includes its bike count, e.g.
    *"Only 2 bikes left at Tooley Street. Snowsfields, London Bridge has
    10 bikes available as a backup."* (best-effort -- if that lookup
    fails for any reason, the main alert still sends without it).
  - Once it recovers to `ALL_CLEAR_BIKES_THRESHOLD` (default **5**) or
    more *after* an alert was sent, you get an "all clear" notification.

Both windows:
- Repeated low alerts are throttled to once every `ALERT_COOLDOWN_MINUTES`
  (default **30**), tracked independently for morning vs evening.
- Runs **Monday-Thursday only**.
- All thresholds/timings live as constants at the top of `check_docks.py`
  -- edit them there.

### Muting for the day

Drop a file at `dock-alerter/mute.flag` containing today's date (e.g.
`2026-06-26`, Europe/London) and every *automatic* (cron-driven) run for
the rest of that day is silently skipped -- no alerts, no API calls. It
resets itself automatically at midnight (tomorrow's date won't match, no
cleanup needed). Manual `--force-mode` runs (e.g. via "Run workflow" in
the Actions tab) bypass the mute, so you can still test things on a muted
day.

The easiest way to set this day's mute flag from your phone is a 1-tap iOS
Shortcut that commits the file via the GitHub Contents API:

1. **Create a fine-grained GitHub Personal Access Token**: GitHub ->
   Settings -> Developer settings -> Personal access tokens -> Fine-grained
   tokens -> Generate new token. Scope it to **only this repository**,
   permission **Contents: Read and write**, and set a long expiry.
2. **Build the Shortcut** (Shortcuts app -> + -> Add Action -> "Get
   Contents of URL"):
   - URL: `https://api.github.com/repos/hsimmonds01/Claude/contents/dock-alerter/mute.flag`
   - Method: `PUT`
   - Headers: `Authorization: Bearer <your token>`, `Accept: application/vnd.github+json`
   - Request body (JSON): `{"message": "Mute today", "content": "<base64 of today's date>", "branch": "main"}`
     -- since the date needs base64-encoding and the file may already
     exist (requiring its current `sha` to update), build this with a few
     extra Shortcuts actions: "Get Contents of URL" (GET, same URL) to fetch
     the existing file's `sha` first (ignore errors if it 404s -- that
     just means no flag is set yet), then "Base64 Encode" the current
     date text, then assemble the JSON body with `sha` included if found.
3. **Add the Shortcut to your Home Screen** (share sheet -> Add to Home
   Screen) for a 1-tap mute icon.

### Checking on demand

Outside the scheduled windows, you can trigger a one-off check any time
via **Actions -> Tooley Street dock check -> Run workflow**, picking a
`force_mode`:

- `status` -- the one to use for an anytime check. Reports both empty
  docks and available bikes together in a single notification, regardless
  of time of day. No thresholds, no alert/all-clear logic, no effect on
  the morning/evening alert state -- just "here's what it looks like right
  now."
- `check` / `evening_check` -- the same logic the scheduled morning/evening
  windows use (threshold alerts, cooldowns, all-clears). Still available
  to force manually, but they're tied to morning-docks/evening-bikes
  semantics respectively, so `status` is usually what you want for a
  spontaneous check.

There's also a 1-tap iOS Shortcut for this (see the chat history / ask for
the setup steps if you want it added to your Home Screen). It needs an
extra permission on your existing GitHub token (Actions: Read and write,
in addition to Contents: Read and write for the mute toggle), and should
POST `{"ref": "main", "inputs": {"force_mode": "status", "dry_run": "false"}}`
to the workflow's dispatch endpoint.

### History log

Every real (non-dry-run) check appends a row to `dock-alerter/history.csv`
-- timestamp, mode, metric (`empty_docks` or `available_bikes`), value,
and station name. Committed back to the repo the same way as `state.json`.
This is just a running log for now (nothing reads it yet) -- a natural
base for a future dashboard or trend-based alerting, without needing to
backfill data once you decide to build one.

### Timezone / DST handling

GitHub Actions cron runs in UTC and has no idea about the UK's GMT/BST
clock change. Rather than maintaining two cron schedules and remembering
to swap them around the clock-change weekends, the workflow schedule
(`.github/workflows/checks.yml`) just runs **more often than needed** --
every 5 minutes from 06:40 to 08:50 UTC, which covers the target
07:45-08:45 London window in both BST and GMT. `check_docks.py` then uses
Python's `zoneinfo` (`Europe/London`) to work out the *actual* local time
on every invocation and only acts if it's really inside the summary or
check window; otherwise it exits immediately without calling the TfL API
or sending anything. This means DST is handled automatically, with no
manual offset maths and nothing to remember twice a year.

### State persistence

Each Actions run starts from a clean checkout, so "did we already alert"
needs to persist somewhere. This project uses a small **`state.json` file
committed back into the repo** by the workflow after each run, rather
than GitHub Actions cache. Reasoning: a committed file is simple, never
silently evicted (Actions cache entries can be cleaned up by GitHub),
trivially inspectable/debuggable in the repo's history, and this workflow
already has write access to commit -- there's no real downside for a
file this small and infrequently changed.

## A note on verifying the TfL JSON shape

This was built using TfL's documented, long-stable `BikePoint` schema:

```json
{
  "id": "BikePoints_278",
  "commonName": "Tooley Street, Bermondsey",
  "additionalProperties": [
    {"key": "NbBikes", "value": "..."},
    {"key": "NbEmptyDocks", "value": "..."},
    {"key": "NbDocks", "value": "..."}
  ]
}
```

The sandbox this was developed in could not reach `api.tfl.gov.uk`
directly (outbound network policy blocked it), so this shape was not
re-verified live before shipping. **Before relying on this for real
mornings, run it once yourself** -- see "Running locally" below, or just
trigger the workflow manually (step 5) -- and check the console output
shows a sensible empty-dock number and the station name contains "Tooley
Street". `check_docks.py` will also print a `WARNING` to the logs if the
station name doesn't match, and will raise an error if `NbEmptyDocks` is
missing, so a schema change won't fail silently.

## Setup

1. **Create the repo** (already done if you're reading this from it) and
   push these files to GitHub.

2. **Pick an ntfy topic.** A random one is already set as the default in
   `check_docks.py`:

   ```
   harry-tooley-docks-5494e935
   ```

   Topic names on ntfy.sh are public knowledge of the topic string -- it's
   *not* secret, anyone who knows/guesses it can read or post to it.
   Since this one has a random suffix, it's fine to leave as-is, but you
   can pick your own by editing `DEFAULT_NTFY_TOPIC` in `check_docks.py`,
   or by setting a repository variable named `NTFY_TOPIC` in
   **Settings -> Secrets and variables -> Actions -> Variables** (the
   workflow already passes this through and it overrides the constant in
   code, so you can change topics without editing/committing code).

3. **Install the ntfy app on your phone** (iOS App Store / Google Play:
   search "ntfy").

4. **Subscribe to your topic** in the app: tap **+**, enter the exact
   topic name from step 2, and subscribe. No account or login required.

5. **Test it manually:**
   - In GitHub, go to **Actions -> Tooley Street dock check -> Run workflow**.
   - Leave `force_mode` as `auto` to test the real time-window logic, or
     pick `summary`/`check`/`evening_summary`/`evening_check` to force a
     run right now regardless of time of day.
   - Tick `dry_run` first time if you just want to see console output
     without a real phone notification.
   - Check the workflow run logs for the dock/bike count, and check your
     phone for the notification (if not a dry run).

6. Once you're happy, leave it -- it'll run automatically Mon-Thu,
   07:45-08:45 and 17:15-18:00 London time, no further action needed.

## Running locally

```bash
cd dock-alerter
pip install -r requirements.txt
python check_docks.py --force-mode summary --dry-run          # morning summary, no notification sent
python check_docks.py --force-mode check                       # morning check + real notification
python check_docks.py --force-mode evening_summary --dry-run   # evening summary, no notification sent
python check_docks.py --force-mode evening_check                # evening check + real notification
python check_docks.py --force-mode status --dry-run             # anytime docks+bikes status, no notification sent
python check_docks.py --force-mode status                       # anytime docks+bikes status + real notification
```
