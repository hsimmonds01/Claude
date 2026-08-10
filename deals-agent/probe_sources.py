#!/usr/bin/env python3
"""Source reachability probe for the Deals & Codes agent.

This is a throwaway diagnostic, not part of the agent. It exists because the
dev sandbox cannot reach ANY of the candidate deal sources -- the agent proxy
answers 403 to CONNECT for hotukdeals.com, latestdeals.co.uk and reddit.com
alike (same wall the FPL agent hit with fantasy.premierleague.com). A GitHub
Actions runner can reach the open internet, so the only way to find out what
actually responds is to run this there and read the log.

It answers three questions that the agent's whole design depends on:

  1. Does the source respond at all from a datacentre IP? HotUKDeals sits
     behind Cloudflare and Reddit blocks unauthenticated cloud traffic, so a
     403/503 here is a realistic outcome, not a pessimistic one.
  2. Is the response an actual feed, or a Cloudflare challenge page wearing a
     200? Status code alone can't tell you -- a challenge page is a perfectly
     healthy 200 full of HTML.
  3. For the feeds that DO work: what fields does an item carry? Specifically,
     does HotUKDeals expose its heat score in the RSS? The planned scoring
     model is heat-over-age, which is only buildable if heat ships in the
     feed. If it doesn't, that design needs replacing before it's written.

Nothing here is committed to state, nothing is notified, nothing is written.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET

import requests

TIMEOUT_SECONDS = 25
# A feed is small. Anything much bigger is a web page (i.e. a challenge or an
# error), and we only ever want a sample of it for the log either way.
MAX_BYTES = 2 * 1024 * 1024
SNIPPET_CHARS = 300
# One full item is dumped per working feed so the available fields can be read
# off directly rather than guessed at. Capped so a verbose feed can't bury the
# rest of the log.
RAW_ITEM_CHARS = 2000

# Two user agents per host: Cloudflare and Reddit both vary their behaviour by
# UA, and "blocked outright" vs "blocked only as an obvious bot" are different
# problems with different fixes. If the browser UA works where the bot UA does
# not, the agent can simply use the browser one.
BOT_UA = "Mozilla/5.0 (compatible; DealsCodesBot/1.0; +https://github.com/hsimmonds01/Claude)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Candidate sources, grouped by what they'd be used for. Several are guesses at
# URLs that may not exist -- a 404 here is a useful answer too, it just removes
# a candidate. Ordered most-wanted first.
CANDIDATES: list[tuple[str, str]] = [
    # -- HotUKDeals: the primary source. Community heat is the free proxy for
    #    "is this actually a good price", so losing this hurts most.
    ("hukd-hot", "https://www.hotukdeals.com/rss/hot"),
    ("hukd-new", "https://www.hotukdeals.com/rss/new"),
    ("hukd-discussions", "https://www.hotukdeals.com/rss/discussions"),
    # Voucher codes specifically, if the tag feed pattern exists.
    ("hukd-tag-vouchers", "https://www.hotukdeals.com/tag/voucher-codes/rss"),
    ("hukd-groups-deliveroo", "https://www.hotukdeals.com/tag/deliveroo/rss"),

    # -- LatestDeals: secondary community source.
    ("latestdeals-feed", "https://www.latestdeals.co.uk/feed"),
    ("latestdeals-rss", "https://www.latestdeals.co.uk/rss"),

    # -- Reddit: .rss is keyless, but Reddit 403s cloud IPs aggressively.
    #    old.reddit.com sometimes survives where www does not, so both are
    #    probed for the same sub before drawing a conclusion.
    ("reddit-ukdeals", "https://www.reddit.com/r/UKDeals/.rss"),
    ("reddit-ukdeals-old", "https://old.reddit.com/r/UKDeals/.rss"),
    ("reddit-ukdeals-new", "https://www.reddit.com/r/UKDeals/new/.rss"),
    ("reddit-beermoneyuk", "https://www.reddit.com/r/beermoneyuk/.rss"),
    ("reddit-vouchercodesuk", "https://www.reddit.com/r/VoucherCodesUK/.rss"),
    ("reddit-ukfrugal", "https://www.reddit.com/r/UKFrugal/.rss"),

    # -- Fallbacks if the community sources are blocked. Google News RSS is
    #    already proven to work from a runner by discovery-agent, so it's a
    #    known-good control: if even this fails, the problem is the runner's
    #    network, not the deal sites.
    ("gnews-control", "https://news.google.com/rss/search?q=voucher+code+UK&hl=en-GB&gl=GB&ceid=GB:en"),
    ("mse-rss", "https://www.moneysavingexpert.com/rss.xml"),
]

# Markers that a 200 is really a bot wall rather than content. Checked
# case-insensitively against the body text.
CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "checking your browser",
    "just a moment",
    "attention required",
    "enable javascript and cookies",
    "cdn-cgi/challenge-platform",
    "px-captcha",
    "whoa there, pardner",       # Reddit's own rate-limit page
    "blocked by network security",
)


def fetch(url: str, user_agent: str) -> dict:
    """Fetch one URL and describe what came back. Never raises: a failure is a
    result to report, not a reason to abandon the remaining candidates."""
    headers = {"User-Agent": user_agent, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    try:
        response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS, stream=True)
    except requests.RequestException as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    try:
        body = response.raw.read(MAX_BYTES, decode_content=True) or b""
    except Exception as exc:  # noqa: BLE001 -- a truncated read is still a result
        body = b""
        response.close()
        return {
            "status": response.status_code,
            "error": f"read failed: {type(exc).__name__}: {exc}",
        }
    response.close()

    text = body.decode("utf-8", errors="replace")
    lowered = text.lower()
    return {
        "status": response.status_code,
        "final_url": response.url,
        "content_type": response.headers.get("Content-Type", "?"),
        "server": response.headers.get("Server", "?"),
        "bytes": len(body),
        "text": text,
        "challenge": [m for m in CHALLENGE_MARKERS if m in lowered],
    }


def describe_feed(text: str) -> dict:
    """Parse as a feed and report what's inside. A source that returns 200 but
    doesn't parse is not usable, so this distinction matters more than status."""
    # An entity declaration is the "billion laughs" vector; the real agent
    # rejects these outright, so flag it here rather than parsing it.
    if re.search(r"<!ENTITY", text[:20000], re.IGNORECASE):
        return {"parsed": False, "reason": "contains XML entity declaration (rejected on principle)"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {"parsed": False, "reason": f"XML parse error: {exc}"}

    # RSS uses <item>, Atom uses <entry>; match on the local name so a
    # namespaced Atom feed isn't missed.
    items = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] in ("item", "entry")]
    if not items:
        return {"parsed": True, "count": 0, "reason": "parsed as XML but contains no items/entries"}

    first = items[0]
    fields = sorted({child.tag.rsplit("}", 1)[-1] for child in first})
    title = ""
    for child in first:
        if child.tag.rsplit("}", 1)[-1] == "title":
            title = (child.text or "").strip()
            break
    return {
        "parsed": True,
        "count": len(items),
        "fields": fields,
        "first_title": title,
        "raw_first_item": ET.tostring(first, encoding="unicode")[:RAW_ITEM_CHARS],
    }


def flatten(text: str, limit: int) -> str:
    """One-line, length-capped sample -- keeps the log readable."""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def probe(name: str, url: str) -> bool:
    """Probe one candidate under both user agents. Returns True if any UA got a
    usable feed back."""
    print(f"\n{'=' * 72}\n{name}\n  {url}")
    usable = False

    for ua_label, ua in (("bot-UA", BOT_UA), ("browser-UA", BROWSER_UA)):
        result = fetch(url, ua)
        if "error" in result and "status" not in result:
            print(f"  [{ua_label}] NETWORK FAIL -- {result['error']}")
            continue

        status = result["status"]
        print(f"  [{ua_label}] HTTP {status} | {result['bytes']} bytes | "
              f"type={result['content_type']} | server={result['server']}")
        if result["final_url"] != url:
            print(f"    redirected to: {result['final_url']}")
        if result["challenge"]:
            print(f"    BOT WALL detected: {', '.join(result['challenge'])}")
        if "error" in result:
            print(f"    {result['error']}")
            continue
        if status != 200:
            print(f"    body: {flatten(result['text'], SNIPPET_CHARS)}")
            continue

        feed = describe_feed(result["text"])
        if not feed["parsed"] or not feed.get("count"):
            print(f"    NOT A FEED -- {feed['reason']}")
            print(f"    body: {flatten(result['text'], SNIPPET_CHARS)}")
            continue

        usable = True
        print(f"    OK -- {feed['count']} items")
        print(f"    item fields: {', '.join(feed['fields'])}")
        print(f"    first title: {flatten(feed['first_title'], 140)}")
        # The decisive one for HotUKDeals: is the heat score in here?
        print(f"    raw first item:\n      {flatten(feed['raw_first_item'], RAW_ITEM_CHARS)}")
        # Only one working UA is needed; don't hammer a source that already
        # answered us.
        break

    return usable


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    targets = [(n, u) for n, u in CANDIDATES if not only or only in n]
    if not targets:
        print(f"No candidate matches {only!r}")
        sys.exit(2)

    print(f"Probing {len(targets)} candidate sources from this runner.")
    working = [name for name, url in targets if probe(name, url)]

    print(f"\n{'=' * 72}\nSUMMARY: {len(working)}/{len(targets)} usable")
    for name, _ in targets:
        print(f"  {'USABLE  ' if name in working else 'no      '} {name}")
    # Always exit 0: a blocked source is the finding, not a build failure. A
    # red X here would just obscure the log we actually came for.


if __name__ == "__main__":
    main()
