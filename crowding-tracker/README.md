# Northern line busyness tracker

Logs how busy seven Northern line stations along a Balham / Clapham commute
corridor are through the morning and evening commute windows, using TfL's
near-real-time crowding feed. Same spirit as the sibling `dock-alerter`:
TfL Unified API + GitHub Actions + a static dashboard, kept free and
low-maintenance.

## Stations

All Northern line, in geographic (southbound → northbound) order. NaPTAN IDs
were each verified live against the API before use:

| Station          | NaPTAN        |
|------------------|---------------|
| Colliers Wood    | `940GZZLUCSD` |
| Tooting Broadway | `940GZZLUTBY` |
| Tooting Bec      | `940GZZLUTBC` |
| Balham           | `940GZZLUBLM` |
| Clapham South    | `940GZZLUCPS` |
| Clapham Common   | `940GZZLUCPC` |
| Clapham North    | `940GZZLUCPN` |

## What the busyness number is (and isn't)

The feed's `percentageOfBaseline` is **not a headcount**. Per TfL, it's a
station-level busyness index derived from *anonymised, aggregated station
WiFi data*, expressed as a fraction of the **busiest week** that station has
seen since data collection began in **July 2019**.

Consequences worth remembering:

- **Per-station scale.** `1.0` means "as busy as this station's own busiest
  week". A big interchange and a small local stop both top out at `1.0` for
  wildly different real crowds, so the raw value is **not comparable between
  stations** — only a station against its own history/typical profile.
- **TfL's bands** for the value: `< 0.4` quiet · `0.4–0.7` busy · `> 0.7`
  very busy. It "is generally between 0 and 1, but can be over 1."
- **`dataAvailable: false`** means "no reading in the last ~5 min", *not*
  that the station is empty. Off-peak, Tooting Bec/Broadway were seen
  returning this. The logger records such readings as **blank**, never as 0,
  so they don't drag the trends down with fake zeros.

## Two kinds of "history"

- **Typical profile** (`GET /crowding/{naptan}`) — TfL's aggregated typical
  busyness by day-of-week and 15-min band, available immediately. The
  dashboard reads this live to draw each station's "normal" curve, so there
  is a baseline to compare against from day one.
- **Observed live readings** (`GET /crowding/{naptan}/Live`) — only ever the
  latest value; there is **no** backfill API for past live readings. So the
  observed time-series in `history.csv` only exists from the moment logging
  starts and grows over time.

## Files

| File                | Purpose                                                  |
|---------------------|----------------------------------------------------------|
| `check_crowding.py` | Poller. `--mode live` (window-gated, appends CSV) / `--mode status` (print now). |
| `history.csv`       | Appended log: one row per station per poll.              |
| `dashboard.html`    | Self-contained page: live tiles, typical-day curve, logged trend. |
| `requirements.txt`  | `requests`.                                              |

The workflow is `.github/workflows/crowding.yml`.

## How it runs

GitHub Actions cron polls every ~5 min. Cron is UTC and DST-blind, so the
workflow casts a wide net in UTC and `check_crowding.py` decides — via
`zoneinfo` Europe/London — whether "now" is actually inside a commute
window (**07:00–09:30** and **16:30–19:00**, **Mon–Fri**). Out-of-window
runs exit without writing. In-window runs append to `history.csv` and the
workflow commits it.

Runs are serialised (a concurrency group) so parallel appends never race on
the git push.

### Manual runs

From the Actions tab, *Run workflow* on **Northern line crowding log**:

- `mode: status` — print current busyness for all seven stations (no write).
- `mode: live` + `force: true` — log a row set right now, ignoring the
  window gate.

Locally: `python check_crowding.py --mode status`.

## CSV columns

`timestamp_utc, timestamp_local, naptan, station, percentage_of_baseline,
data_available, band, reading_time_utc`

`percentage_of_baseline` is blank when `data_available` is `false`;
`band` is one of `quiet` / `busy` / `very busy` / `none`.

## Notes / caveats

- **Scheduler reliability.** GitHub's native `schedule` is best-effort and
  can run late or skip (the dock-alerter has seen >1 hr delays). For a
  logger the odd missed 5-min sample is harmless. If gaps get annoying,
  point an external pinger (e.g. cron-job.org) at the `workflow_dispatch`
  endpoint — the window-gating makes duplicate triggers safe.
- **API key.** The feed works keyless. An optional Unified API key can be
  supplied via a `TFL_APP_KEY` repo *secret* for rate-limit headroom; it's
  masked in the (public) Actions logs.
- **Storage.** ~50 bytes/reading × 7 stations × ~60 samples/day × weekdays
  ≈ a few MB per year — negligible for git for many years.
