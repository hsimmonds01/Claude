# World Cup Fantasy Tracker

A free, static leaderboard that tracks 10 players' drafted World Cup teams
using a custom scoring system. Match data refreshes automatically every
~15 minutes via a scheduled GitHub Action — no server, no login.

## One-time setup

1. **Get a free API key**
   - Sign up at <https://www.football-data.org/client/register> (instant,
     free tier).
   - Copy the API token you're emailed.

2. **Add it as a repo secret**
   - GitHub repo → Settings → Secrets and variables → Actions → New
     repository secret.
   - Name: `FOOTBALL_DATA_API_KEY`
   - Value: the token from step 1.

3. **Enable GitHub Pages**
   - Settings → Pages → Source: "Deploy from a branch" → pick this branch
     and `/ (root)`.
   - Your page will be live at the URL shown there.

4. **Run the sync once manually**
   - Actions tab → "Update World Cup standings" → Run workflow.
   - This populates `data/standings.json` for the first time. After that
     it runs automatically every 15 minutes.

## How scoring works

- **Group stage**: 3 pts per win, 1 pt per draw (stacks across all group
  matches).
- **Knockout rounds**: a team's bonus is the value of the *single highest
  round they won* — Ro32: 4, Ro16: 6, QF: 10, SF: 15, Final: 23. These
  don't add up; only the best one counts.
- **Third place playoff**: +2 if a team wins it, added on top of their
  group stage points.

## Editing the teams

Player-to-team assignments live in `config/players.json`. Each team entry
has a list of `aliases` used to match it against whatever name
football-data.org returns (to handle spelling differences like "Czechia"
vs "Czech Republic"). Edit that file and push to update the roster.

## Files

- `index.html`, `style.css`, `app.js` — the page itself.
- `config/players.json` — player/team assignments.
- `data/standings.json` — generated snapshot of tracked matches (written
  by the GitHub Action, do not edit by hand).
- `scripts/fetch-scores.mjs` — script the Action runs to pull and filter
  match data.
- `.github/workflows/update-scores.yml` — the scheduled sync job.
