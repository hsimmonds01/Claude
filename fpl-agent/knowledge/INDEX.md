# FPL agent knowledge base

Last built: 2026-07-27T16:14:23.353550+00:00

Built by `fpl-agent/knowledge.py`. Official rules pages are stored in full
under `official/` because the agent must never get scoring or chip rules
wrong. Community sources are stored as headlines and links only in
`feeds.json` -- the agent follows a link when it needs detail rather than
archiving other people's writing.

## Official sources

| Source | Status | Notes |
|---|---|---|
| [fpl-rules](https://fantasy.premierleague.com/help/rules) | not captured | only 115 chars -- looks like an empty shell, not saved |
| [fpl-help](https://fantasy.premierleague.com/help) | not captured | only 115 chars -- looks like an empty shell, not saved |
| [fpl-terms](https://fantasy.premierleague.com/help/terms) | not captured | only 115 chars -- looks like an empty shell, not saved |
| [pl-changes-2026-27](https://www.premierleague.com/en/news/4679873) | captured | saved 7103 chars, matched ['fantasy'] |
| [pl-chips-2026-27](https://www.premierleague.com/en/news/4679879) | captured | saved 5596 chars, matched ['chip'] |

## Community feeds (headlines only)

| Feed | Items |
|---|---|
| news.google.com | 40 |
| www.fantasyfootballscout.co.uk | 12 |
