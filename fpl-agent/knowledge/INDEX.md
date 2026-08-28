# FPL agent knowledge base

Last built: 2026-08-28T17:44:49.609769+00:00

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
| [pl-changes-2026-27](https://www.premierleague.com/en/news/4679873) | captured | saved 6921 chars, matched ['fantasy'] |
| [pl-chips-2026-27](https://www.premierleague.com/en/news/4679879) | captured | saved 5414 chars, matched ['chip'] |

## Community feeds (headlines only)

| Feed | Items |
|---|---|
| kill-the-newsletter.com | 7 |
| news.google.com | 40 |
| www.fantasyfootballscout.co.uk | 12 |
