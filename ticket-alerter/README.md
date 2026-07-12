# England v Argentina ticket watch

Watches [Between The Bridges' World Cup page](https://www.betweenthebridges.co.uk/fifa-world-cup-2026)
(and their [ticketed events page](https://www.betweenthebridges.co.uk/ticketed))
for the **England v Argentina semi-final (Wed 15 July 2026)** screening event
going live, and pushes a phone notification via [ntfy.sh](https://ntfy.sh) to
the same topic the dock-alerter uses -- no new app setup needed.

## What you'll be notified about

| Notification | Priority | When |
|---|---|---|
| **TICKETS ON SALE** | urgent | The event page exists and has a ticket link (DICE, Eventbrite, etc.) or a book/buy button. Includes the booking link. |
| **Event page is LIVE** | urgent | The event page appeared but has no ticket link yet. The watch continues and alerts again when tickets appear. |
| **Showing as SOLD OUT** | default | The event page shows sold-out wording. |
| **New event on the page** | default | Some other new event link appeared -- safety net in case the venue names the page unexpectedly. |
| **Monitor can't reach the site** | high | 3+ consecutive runs failed to load the site, i.e. the watch is blind (e.g. bot-blocking). Worth checking the page manually. |

Each alert is sent **once** (tracked in `state.json`, committed back by the
workflow), so overlapping triggers can't double-alert. The whole monitor
no-ops after Wed 15 July 22:00 UK time.

## How it runs

- `.github/workflows/ticket-watch.yml` runs every 10 minutes on GitHub's
  schedule, 12-16 July only -- but GitHub's scheduler runs late, so
  **cron-job.org pinging the `workflow_dispatch` endpoint every 5 minutes is
  the primary trigger** (same pattern, same PAT as the dock-alerter job).
- The first run seeds `state.json` with the event links already on the page
  (so the old Argentina v Egypt round-of-16 page etc. don't trigger anything).

## Manual runs

```bash
cd ticket-alerter
pip install -r requirements.txt
python check_tickets.py --dry-run          # check now, print instead of notify
python check_tickets.py                    # check now, real notification
python check_tickets.py --force --dry-run  # ignore the end-date gate (testing)
python test_check_tickets.py               # offline tests with fixture HTML
```
