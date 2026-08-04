# FPL agent knowledge base

Last built: 2026-08-04T17:00:03.177159+00:00

Built by `fpl-agent/knowledge.py`. Official rules pages are stored in full
under `official/` because the agent must never get scoring or chip rules
wrong. Community sources are stored as headlines and links only in
`feeds.json` -- the agent follows a link when it needs detail rather than
archiving other people's writing.

## Official sources

| Source | Status | Notes |
|---|---|---|
| [fpl-rules](https://fantasy.premierleague.com/help/rules) | captured | saved 24511 chars (browser-rendered), matched ['points', 'goal', 'clean sheet'] |
| [fpl-help](https://fantasy.premierleague.com/help) | captured | saved 12206 chars (browser-rendered), matched ['fantasy'] |
| [fpl-terms](https://fantasy.premierleague.com/help/terms) | captured | saved 26068 chars (browser-rendered), matched ['terms'] |
| [pl-changes-2026-27](https://www.premierleague.com/en/news/4679873) | not captured | failed: 403 Client Error: Forbidden for url: https://www.premierleague.com/en/news/4679873 |
| [pl-chips-2026-27](https://www.premierleague.com/en/news/4679879) | not captured | failed: 403 Client Error: Forbidden for url: https://www.premierleague.com/en/news/4679879 |

## Community feeds (headlines only)

| Feed | Items |
|---|---|
| kill-the-newsletter.com | 3 |
| news.google.com | 40 |
| www.fantasyfootballscout.co.uk | 12 |
