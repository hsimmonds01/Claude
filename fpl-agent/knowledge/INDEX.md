# FPL agent knowledge base

Last built: 2026-07-28T08:33:43.575030+00:00

Built by `fpl-agent/knowledge.py`. Official rules pages are stored in full
under `official/` because the agent must never get scoring or chip rules
wrong. Community sources are stored as headlines and links only in
`feeds.json` -- the agent follows a link when it needs detail rather than
archiving other people's writing.

## Official sources

| Source | Status | Notes |
|---|---|---|
| [fpl-rules](https://fantasy.premierleague.com/help/rules) | not captured | failed: HTTPSConnectionPool(host='fantasy.premierleague.com', port=443): Max retries exceeded with url: /help/rules (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))) |
| [fpl-help](https://fantasy.premierleague.com/help) | not captured | failed: HTTPSConnectionPool(host='fantasy.premierleague.com', port=443): Max retries exceeded with url: /help (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))) |
| [fpl-terms](https://fantasy.premierleague.com/help/terms) | not captured | failed: HTTPSConnectionPool(host='fantasy.premierleague.com', port=443): Max retries exceeded with url: /help/terms (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))) |
| [pl-changes-2026-27](https://www.premierleague.com/en/news/4679873) | not captured | failed: HTTPSConnectionPool(host='www.premierleague.com', port=443): Max retries exceeded with url: /en/news/4679873 (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))) |
| [pl-chips-2026-27](https://www.premierleague.com/en/news/4679879) | not captured | failed: HTTPSConnectionPool(host='www.premierleague.com', port=443): Max retries exceeded with url: /en/news/4679879 (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))) |

## Community feeds (headlines only)

| Feed | Items |
|---|---|
