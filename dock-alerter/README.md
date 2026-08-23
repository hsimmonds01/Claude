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

- **08:10** and **08:25** (Europe/London time) -- two one-off snapshots:
  current empty-dock count, sent regardless of how full the station is.
  The **08:10** one also carries a forecast of the day's low point (see
  [Forecasts in the notifications](#forecasts-in-the-notifications)), e.g.
  *"12 empty docks now. Usually dips to ~8 docks around 08:30."*
- **08:00-08:45** -- checked every 5 minutes.
  - If empty docks drop below `LOW_DOCKS_THRESHOLD` (default **3**), you
    get a high-priority alert.
  - Once it recovers to `ALL_CLEAR_DOCKS_THRESHOLD` (default **5**) or more
    *after* an alert was sent, you get an "all clear" notification.
  - The 08:10 and 08:25 snapshot slots take the place of that slot's
    threshold check (they report the count anyway).

**Evening (return commute) -- watches available bikes**, so you know if
you'll be able to pick one up to ride home:

- **17:15** -- a one-off evening summary: current available-bikes count,
  plus the same low-point forecast as the morning summary.
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

### Enabling a one-off Friday

Monitoring normally runs Mon-Thu only. To include a specific Friday, drop a
file at `dock-alerter/friday.flag` containing that Friday's date (e.g.
`2026-07-10`). You can set it any time in advance (e.g. Thursday afternoon)
and it resets itself automatically -- any other Friday won't match, so
there's nothing to clean up. Works via the same 1-tap iOS Shortcut pattern
as the mute flag (same GitHub token, just targeting `friday.flag` and
writing tomorrow's date). If both `friday.flag` and `mute.flag` are set for
the same day, the mute wins.

### Dashboard

`dock-alerter/dashboard.html` is a self-contained phone-friendly dashboard
(no build step, no external libraries) served via GitHub Pages:

    https://hsimmonds01.github.io/Claude/dock-alerter/dashboard.html

It has four views, switched with the bar along the bottom:

- **Now** — live dock/bike status straight from the TfL API (plus the
  Snowsfields backup station), and a "Coming up" strip summarising the
  forecast for the next monitored morning and evening windows.
- **Forecast** — a prediction for the next monitored morning (empty docks)
  and evening (standard bikes), built from `history.csv`. It blends the
  average for that weekday with the average across all recorded weekdays,
  weighted by how much same-weekday data exists, and shows the full range
  seen so far as a shaded band. Headline = the tightest point of the
  window, with an OK / getting-low / critical chip against the alert
  thresholds. Forecasts appear once there are ~3 days of data and sharpen
  as more accumulates. If `friday.flag` is set for the coming Friday, that
  Friday is included as a forecastable day.
- **Patterns** — "a typical day here": average reading at each check time
  for the morning and evening windows, filterable to a single weekday
  (All / Mon / Tue / Wed / Thu), with the observed range as a band and the
  alert thresholds shaded.
- **History** — daily lows for this week / last week per window, plus the
  full log of recent readings.

Every chart has a "See the numbers" table underneath it, and tapping/
hovering a chart shows exact values. Open the page in Safari and use "Add
to Home Screen" for a 1-tap icon; it re-fetches live data automatically
whenever you re-open it, and shows a warning banner if the scheduled
checks appear to have stopped logging.

### Checking on demand

Outside the scheduled windows, you can trigger a one-off check any time
via **Actions -> Tooley Street dock check -> Run workflow**, picking a
`force_mode`:

- `status` -- the one to use for an anytime check. Reports both empty
  docks and available bikes together in a single notification, regardless
  of time of day. No thresholds, no alert/all-clear logic, no effect on
  the morning/evening alert state -- just "here's what it looks like right
  now."
- `gym_route` -- a separate anytime check for a different route (not the
  Tooley Street commute): standard bikes available at **Bricklayers Arms,
  Borough**, and empty docks at **Empire Square** -- falling back to
  **Swan Street** if Empire Square has none, and saying so explicitly
  ("Empire Square is full -- head to Swan Street instead"). Both
  destination stations are looked up by name via the TfL Search endpoint
  (same pattern as the evening backup lookup) and then re-fetched by ID
  for reliable live data, not by a hardcoded ID, and the notification
  always names the exact station it matched so a wrong match is obvious
  immediately. The message opens with **"Pump time"** if there's
  comfortable margin on both ends (at least `GYM_ROUTE_GOOD_BIKES_THRESHOLD`
  bikes and at least `GYM_ROUTE_GOOD_DOCKS_THRESHOLD` docks at whichever
  drop-off is actually usable), or **"Low availability"** otherwise.
  Like `status`, it's stateless: no cooldowns, no
  alert/all-clear logic, and readings aren't written to `history.csv` (that
  file and the dashboard are scoped to the Tooley Street commute, so mixing
  in a different route would skew its averages).
- `accuracy` -- reports how well the forecasts have been doing (see
  [Is it any good?](#is-it-any-good-accuracy-tracking)). Read-only apart
  from scoring any windows that have finished since the last run.
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

### Forecasts in the notifications

The two **summary** notifications (08:10 and 17:15) carry one extra line
predicting the window's **low point** and roughly when it happens:

    12 empty docks now. Usually dips to ~8 docks around 08:30.

The low point is used rather than a fixed end-of-window time because it's
the moment that actually matters -- the tightest it gets if you leave in
the next few minutes.

Deliberately **only** on those two. The 08:25/17:40 snapshots and the
threshold alerts stay forecast-free: by then you're either committed or
being told something urgent, and a prediction would just compete with the
real number.

How it works, and its guardrails:

- For each 5-minute slot in the window, that weekday's own average is
  blended with the average across all recorded weekdays, weighted by how
  much same-weekday data exists (`FORECAST_SHRINK_K`). Early on the
  overall average dominates; as Tuesdays accumulate, Tuesdays take over.
- Nothing is predicted at all below `FORECAST_MIN_DAYS` (**3**) recorded
  days -- the notification then reads exactly as it did before forecasts
  existed. A shaky guess erodes trust faster than no guess.
- Slots with fewer than `FORECAST_MIN_SLOT_READINGS` (**4**) readings are
  ignored. Found the hard way: the 18:00 slot once held exactly two
  readings from one odd evening and dragged the predicted low from ~5 to
  ~2 -- a two-sample fluke presented as a forecast.
- **This logic is duplicated in `dashboard.html`** (same blend, same
  floors, same rounding) so the phone and the Forecast tab can't quote
  different numbers for the same window. If you change one, change both --
  there's a parity check for exactly this in the chat history, and the
  two drifted apart once already (different window bounds and different
  half-rounding) before it was caught.

### Is it any good? (accuracy tracking)

Every forecast is logged to `dock-alerter/predictions.csv` and scored
against what actually happened once its window finishes. Reconciliation
runs on *every* invocation -- including muted days and runs outside the
windows -- so a window missed at the time still gets scored later, with
nothing extra to schedule.

View it either way:

- **Dashboard -> Forecast -> "How accurate has this been?"** -- typical
  error, % within 2, whether it leans optimistic or pessimistic, a
  per-window split, and the recent forecast-vs-actual rows.
- **`--force-mode accuracy`** -- the same summary as a notification.

Three numbers worth knowing: *typically out by* (mean absolute error --
how far off a forecast usually lands), *within 2* (how often it's close
enough to act on), and the *lean* (mean signed error -- a consistent bias
is the fixable kind of wrong, and would mean the blend needs retuning).

### History log

Every real (non-dry-run) check appends a row to `dock-alerter/history.csv`
-- timestamp, mode, metric (`empty_docks` or `available_bikes`), value,
station name, and **temperature/precipitation** at the time. Committed
back to the repo the same way as `state.json`, and read by the dashboard
and the forecaster.

Weather is logged so that "are there fewer bikes when it's wet, and does
it run out earlier?" can eventually be answered from real data. Nothing
correlates it yet, and nothing can for a while: there's no weather on any
row before 2026-08, so that backlog has to build up first. In the
meantime the summary notifications just add an honest caveat when it's
actually raining (`WET_PRECIP_MM`), rather than pretending to model it.

Rows written before weather tracking have only the first five columns;
the script upgrades the header once, in place, and leaves those short
historical rows untouched -- every reader here and in the dashboard
tolerates them.

### Timezone / DST handling

GitHub Actions cron runs in UTC and has no idea about the UK's GMT/BST
clock change. Rather than maintaining two cron schedules and remembering
to swap them around the clock-change weekends, the workflow schedule
(`.github/workflows/checks.yml`) just runs **more often than needed** --
every 5 minutes from 06:40 to 08:50 UTC, which covers the target
08:00-08:45 London window in both BST and GMT. `check_docks.py` then uses
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
   08:00-08:45 and 17:15-18:00 London time, no further action needed.

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
