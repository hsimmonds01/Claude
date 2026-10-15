# VOXI Drop watch -- RETIRED (Oct 2026)

**Status: switched off.** The workflow was removed because the approach cannot
work: the public Drop page is identical whether or not a drop is live (same
copy, same permanent "Sign in to claim rewards" button; the page's own CMS
content hadn't been edited since Dec 2025), and the page exposes no
"drop is live" feature switch or API (`enableVoxiDropLandingPage` is
permanently on). The real claim state is behind the VOXI login, so any
logged-out check -- including the "page changed" fingerprint -- is blind to
drops. Of the announcement channels, only the r/VOXI RSS feed was readable
from a GitHub runner, and it rate-limited repeat requests.

The code is kept for reference/reuse (`discover.py` is a handy read-only probe
of what a page exposes). To revive, restore `.github/workflows/voxi-drop-watch.yml`
from git history. Lesson: before building a watcher, confirm from real
before/after evidence that the watched signal actually changes.

---

# VOXI Drop watch

Watches the [VOXI Drop page](https://www.voxi.co.uk/voxi-drop) for the
monthly Drop going live and pushes a phone notification via
[ntfy.sh](https://ntfy.sh) to the same topic the dock-alerter uses. The Drop
lands on a random day each month and rewards are first-come first-served, so
the watch polls all month and re-arms itself for the next month
automatically. Adapted from the retired one-shot `ticket-alerter/`.

## What you'll be notified about

| Notification | Priority | When |
|---|---|---|
| **VOXI Drop is LIVE** | urgent | The page switches to its claimable state. Once per calendar month. |
| **VOXI Drop coming soon** | default | Teaser wording ("dropping tomorrow" etc.) appears. Once per month. |
| **VOXI Drop page changed** | default | The page's drop wording changed in a way the classifier can't interpret — safety net for redesigns/unexpected wording. |
| **Monitor can't reach the site** | high | 3+ consecutive runs failed to load the page, i.e. the watch is blind. |

Each alert fires **once** (tracked in `state.json`, committed back by the
workflow), so overlapping triggers can't double-alert.

## How the page is read

`check_drop.py` classifies the raw HTML (embedded JSON included) as
live / closed / teaser / unknown via the keyword lists at the top of the
file. A page showing both live and closed wording counts as unknown — better
a soft "changed" nudge than a wrong LIVE alert. Run with `--recon` (or the
workflow's `recon` mode) to print everything the classifier sees on the real
page — status, title, keyword hits, drop-related button texts, visible text —
for tuning those lists. The sandbox Claude works in can't reach voxi.co.uk,
so recon runs happen via GitHub Actions, where the fetch works.

## How it runs

- `.github/workflows/voxi-drop-watch.yml` runs every 10 minutes on GitHub's
  schedule — but GitHub's scheduler runs late, so **cron-job.org pinging the
  `workflow_dispatch` endpoint every 5 minutes is the primary trigger**
  (same pattern and PAT as the dock-alerter job).

## Manual runs

```bash
cd voxi-drop-alerter
pip install -r requirements.txt
python check_drop.py --recon      # print page analysis, no alerts/state
python check_drop.py --dry-run    # classify + print would-be alerts
python check_drop.py              # real check + real notification
python test_check_drop.py         # offline tests with fixture HTML
```
