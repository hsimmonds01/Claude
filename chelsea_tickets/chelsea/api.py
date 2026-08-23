"""Talk to Chelsea's public ticket feed.

The men's ticket page is a React app: the fixture list is NOT in the served
HTML, it is fetched client-side from `/en/api/fixtures/tickets`. That
endpoint is public -- no login, no cookies -- but it requires a `pageId`,
which is the CMS entry id of the tickets page and is published in the page
HTML. We re-read it every run so a CMS rebuild does not silently blind the
watcher, falling back to the known-good value if the page will not parse.
"""

from __future__ import annotations

import html
import re

import requests

TICKETS_PAGE_URL = "https://www.chelseafc.com/en/tickets/mens-tickets"
TICKETS_API_URL = "https://www.chelseafc.com/en/api/fixtures/tickets"

# Verified working 2026-08-22. Only used if the page HTML stops exposing it.
FALLBACK_PAGE_ID = "4nn76TbMHeor2gxBhp09a8"

REQUEST_TIMEOUT_SECONDS = 20
FETCH_ATTEMPTS = 2

# Chelsea's edge serves a 404 HTML page to obvious bots, so present as a
# normal browser. This is the same request the site itself makes.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

_PAGE_ID_PATTERN = re.compile(r'"pageId"\s*:\s*"([A-Za-z0-9_-]{8,64})"')


class FetchError(RuntimeError):
    """Could not retrieve the feed after retrying."""


def _get(url: str, params: dict | None = None) -> requests.Response:
    last: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            response = requests.get(
                url, params=params, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last = exc
            print(f"WARNING: attempt {attempt}/{FETCH_ATTEMPTS} failed for {url}: {exc}")
    raise FetchError(f"{url}: {last}")


def resolve_page_id() -> str:
    """Read the tickets page's CMS id, falling back to the known value."""
    try:
        # The page embeds its JSON inside an HTML attribute, so the quotes
        # arrive as `&quot;`. Unescaping twice (the CMS escapes it once more
        # on the way in) is what makes the plain `"pageId"` pattern match.
        page_html = html.unescape(html.unescape(_get(TICKETS_PAGE_URL).text))
        match = _PAGE_ID_PATTERN.search(page_html)
    except FetchError as exc:
        print(f"WARNING: could not load the tickets page ({exc}); using fallback pageId")
        return FALLBACK_PAGE_ID
    if not match:
        print("WARNING: no pageId found in the tickets page; using fallback pageId")
        return FALLBACK_PAGE_ID
    return match.group(1)


def fetch_ticket_feed(page_id: str | None = None) -> dict:
    """Fetch the raw ticket feed. Raises FetchError if it cannot be reached."""
    resolved = page_id or resolve_page_id()
    # pageSize is honoured by the API; the default of 6 would silently drop
    # fixtures once Chelsea lists more than six at once.
    response = _get(TICKETS_API_URL, params={"pageId": resolved, "pageSize": 100})
    try:
        payload = response.json()
    except ValueError as exc:
        raise FetchError(f"ticket feed did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FetchError("ticket feed returned JSON that was not an object")
    return payload
