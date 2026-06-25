# Tooley Street dock alerter

Pushes a phone notification when the Santander Cycles docking station at
**Tooley Street, Bermondsey** (`BikePoints_278`) is getting full, so you
know before you arrive whether you'll be able to dock your bike.

Data comes from the [TfL Unified API](https://api.tfl.gov.uk/) (no API key
needed). Notifications are sent via [ntfy.sh](https://ntfy.sh) (free, no
account needed).

## How it works

- **07:45** (Europe/London time) -- a one-off morning summary: current
  empty-dock count, sent regardless of how full the station is.
- **08:00-08:45** -- checked every 5 minutes.
  - If empty docks drop below `LOW_DOCKS_THRESHOLD` (default **3**), you
    get a high-priority alert.
  - Once it recovers to `ALL_CLEAR_THRESHOLD` (default **5**) or more
    *after* an alert was sent, you get an "all clear" notification.
  - Repeated low-dock alerts are throttled to once every
    `ALERT_COOLDOWN_MINUTES` (default **30**).
- Runs **Monday-Thursday only**.
- All thresholds/timings live as constants at the top of `check_docks.py`
  -- edit them there.

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
     pick `summary`/`check` to force a run right now regardless of time of
     day.
   - Tick `dry_run` first time if you just want to see console output
     without a real phone notification.
   - Check the workflow run logs for the empty-dock count, and check your
     phone for the notification (if not a dry run).

6. Once you're happy, leave it -- it'll run automatically Mon-Thu,
   07:45-08:45 London time, no further action needed.

## Running locally

```bash
cd dock-alerter
pip install -r requirements.txt
python check_docks.py --force-mode summary --dry-run   # see the API + logic work, no notification sent
python check_docks.py --force-mode check                # forces a real check + real notification
```

## TODO -- not built yet

- A second, evening schedule for the reverse commute: summary at 17:00,
  checks every 5 minutes from 17:30-18:00. Should mirror the morning logic
  in `determine_mode`/`run` with its own window constants, kept as a
  separate `mode` value so the morning behaviour isn't disturbed.
- A "mute today" toggle -- e.g. a checked-in flag file or a repository
  variable read at the top of `main()` that, if set, short-circuits the
  whole run for the rest of the current London day.
