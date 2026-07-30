"""Build the agent's reference knowledge base.

The model needs two different kinds of external material, and they have very
different handling rules:

1. OFFICIAL RULES (fantasy.premierleague.com/help, premierleague.com news).
   Factual, authoritative, and the thing the agent must never get wrong --
   scoring values, DefCon thresholds, chip timing, price-change mechanics.
   Saved in full to knowledge/official/ so the agent reasons from the actual
   rules rather than from a model's recollection of them.

2. COMMUNITY ANALYSIS (Fantasy Football Scout and similar).
   Useful for injury news, press-conference reporting and scout picks, but
   it is someone else's copyrighted writing. Only headlines, links and the
   feed's own summary are stored -- never full article bodies. The agent
   follows a link when it needs the detail; it does not archive the article.

The FPL site is a JavaScript app, so a plain fetch can return a near-empty
shell rather than the page a browser shows. Rather than silently writing that
out as "knowledge", every fetch is checked for expected keywords and the run
reports honestly which sources produced real content and which did not.

Outputs:
  knowledge/official/*.md   full text of official rules pages, with provenance
  knowledge/feeds.json      headlines + links + summaries from community feeds
  knowledge/INDEX.md        what was captured, when, and what failed

Usage:
  python knowledge.py
"""

from __future__ import annotations

import json
import re
import sys
# defusedxml, not the stdlib parser. One of the feeds below is an
# email-to-RSS bridge, so a third party can put arbitrary XML into this
# parser at will -- it is the only input in the whole system an outsider can
# write to. defusedxml rejects DTDs and entity declarations outright rather
# than relying on ElementTree's defaults staying safe across versions.
# Deliberately not wrapped in a try/except ImportError fallback: silently
# dropping back to the unsafe parser would defeat the point. If it is
# missing this step fails, and the workflow marks it continue-on-error, so
# the deadline email still goes out without fresh headlines.
from defusedxml import ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
OFFICIAL_DIR = KNOWLEDGE_DIR / "official"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
TIMEOUT_SECONDS = 30

# Official sources. `expect` lists words that must appear for the fetch to
# count as real content -- the guard against saving an empty SPA shell.
OFFICIAL_SOURCES = [
    {
        "slug": "fpl-rules",
        "url": "https://fantasy.premierleague.com/help/rules",
        "expect": ["points", "goal", "clean sheet"],
        "why": "Core scoring, squad, transfer and chip rules",
    },
    {
        "slug": "fpl-help",
        "url": "https://fantasy.premierleague.com/help",
        "expect": ["fantasy"],
        "why": "General FAQ, including price changes and deadlines",
    },
    {
        "slug": "fpl-terms",
        "url": "https://fantasy.premierleague.com/help/terms",
        "expect": ["terms"],
        "why": "Terms of use -- worth knowing what the game permits",
    },
    {
        "slug": "pl-changes-2026-27",
        "url": "https://www.premierleague.com/en/news/4679873",
        "expect": ["fantasy"],
        "why": "Official write-up of the 2026/27 rule changes",
    },
    {
        "slug": "pl-chips-2026-27",
        "url": "https://www.premierleague.com/en/news/4679879",
        "expect": ["chip"],
        "why": "Official explanation of how chips work this season",
    },
]

# Community feeds -- headlines only, never full articles.
#
# lazyfpl.com is a newsletter the manager already reads and rates, and its
# /p/<slug> URL pattern is the signature of Substack or beehiiv -- both of
# which publish an RSS feed, usually at /feed. Which one it is can't be
# checked from a development session (the sandbox proxy blocks the host and
# the site returns 403 to datacentre fetchers), so the candidates are listed
# and the run reports which responded. A feed means the newsletter can be
# read without touching anyone's email account.
COMMUNITY_FEEDS = [
    # lazyfpl.com: tried /feed and /rss on 27 Jul 2026. Both responded but
    # parsed to zero items -- an HTML page, not a feed -- so this newsletter
    # does not publish RSS at the conventional paths. Left here because a
    # zero-item feed costs one request and nothing else, and newsletter
    # platforms add feeds; if one appears, it starts working with no change.
    # Until then the fallback is an email-to-RSS bridge, which still keeps
    # the agent out of any personal inbox.
    "https://www.lazyfpl.com/feed",
    # The bridge, set up 27 Jul 2026: the newsletter is subscribed with a
    # generated address and each issue becomes an item in this feed. The
    # agent reads one public URL and has no access to any mailbox -- there
    # is no credential here to leak, and unsubscribing revokes it entirely.
    "https://kill-the-newsletter.com/feeds/xixkgmteurdc145777l8.xml",
    "https://www.fantasyfootballscout.co.uk/feed/",
    "https://news.google.com/rss/search?q=%22Fantasy+Premier+League%22+tips&hl=en-GB&gl=GB&ceid=GB:en",
    "https://news.google.com/rss/search?q=Premier+League+injury+news+press+conference&hl=en-GB&gl=GB&ceid=GB:en",
]
FEED_ITEMS_PER_SOURCE = 20

# A page shorter than this is a shell or an error page, not content.
MIN_USEFUL_CHARS = 400


def html_to_text(html: str) -> str:
    """Strip tags without pulling in a parser dependency. Crude, but this is
    reference text for a language model to read, not a document to render."""
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
        .replace("&gt;", ">").replace("&quot;", '"').replace("&#039;", "'")
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def render_with_browser(url: str) -> str:
    """Fallback for JavaScript-rendered pages.

    fantasy.premierleague.com is a React app: a plain fetch of /help/rules
    returns a ~115-character shell with no rules in it. Playwright runs a real
    browser so the page renders before we read it. Returns "" if Playwright
    isn't installed or the render fails, so the caller just treats it as a
    miss rather than crashing the whole knowledge build.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[knowledge]   (playwright not installed -- skipping browser render)", file=sys.stderr)
        return ""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(url, wait_until="networkidle", timeout=45000)
            html = page.content()
            browser.close()
        return html
    except Exception as exc:  # noqa: BLE001 -- a failed render is a miss, not a crash
        print(f"[knowledge]   (browser render failed: {exc})", file=sys.stderr)
        return ""


def fetch_official(source: dict) -> dict:
    result = {**source, "ok": False, "chars": 0, "note": ""}
    try:
        response = requests.get(source["url"], headers=HEADERS, timeout=TIMEOUT_SECONDS)
        result["status"] = response.status_code
        response.raise_for_status()
        text = html_to_text(response.text)

        # A JS app returns a near-empty shell to a plain fetch. Re-fetch
        # through a real browser before giving up on the page.
        if len(text) < MIN_USEFUL_CHARS:
            print(f"[knowledge] {source['slug']:<22} shell ({len(text)} chars) -- retrying with browser")
            rendered = render_with_browser(source["url"])
            if rendered:
                text = html_to_text(rendered)
                result["rendered"] = True

        result["chars"] = len(text)

        lowered = text.lower()
        found = [word for word in source["expect"] if word.lower() in lowered]
        via = " (browser-rendered)" if result.get("rendered") else ""
        if len(text) < MIN_USEFUL_CHARS:
            result["note"] = f"only {len(text)} chars{via} -- looks like an empty shell, not saved"
        elif not found:
            result["note"] = f"none of the expected words {source['expect']} present{via} -- not saved"
        else:
            OFFICIAL_DIR.mkdir(parents=True, exist_ok=True)
            path = OFFICIAL_DIR / f"{source['slug']}.md"
            header = (
                f"# {source['slug']}\n\n"
                f"- Source: {source['url']}\n"
                f"- Fetched: {datetime.now(timezone.utc).isoformat()}\n"
                f"- Why it's here: {source['why']}\n\n"
                f"Captured automatically by `fpl-agent/knowledge.py` as reference "
                f"material for the FPL agent.\n\n---\n\n"
            )
            path.write_text(header + text + "\n", encoding="utf-8")
            result["ok"] = True
            result["note"] = f"saved {len(text)} chars{via}, matched {found}"
    except Exception as exc:  # noqa: BLE001 -- one dead source shouldn't sink the run
        result["note"] = f"failed: {exc}"
    print(f"[knowledge] {source['slug']:<22} {result['note']}")
    return result


def parse_feed(xml_text: str, source: str) -> list[dict]:
    """Handles RSS <item> and Atom <entry>, ignoring namespaces."""
    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:  # noqa: BLE001 -- includes defusedxml's refusals
        # A refused document (entity declarations, DTD) lands here alongside
        # ordinary malformed XML. Either way the feed is skipped, but say
        # which so a hostile feed is distinguishable from a broken one.
        print(f"[knowledge] {source}: XML rejected ({type(exc).__name__}: {exc})", file=sys.stderr)
        return []

    def safe_link(url: str) -> str:
        """Only http(s) links are stored.

        A feed item's <link> is attacker-controlled -- one of our sources is
        an email bridge anyone can write to -- and a "javascript:" URL that
        reaches an href becomes script execution on whatever page renders it.
        Rejecting the scheme at ingest kills that at the source rather than
        relying on every consumer to remember.
        """
        url = (url or "").strip()
        return url if url.lower().startswith(("http://", "https://")) else ""

    def child_text(element, name: str) -> str:
        for node in element:
            if node.tag.rsplit("}", 1)[-1] == name:
                return (node.get("href") or node.text or "").strip()
        return ""

    items = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        title = child_text(element, "title")
        if not title:
            continue
        # Deliberately truncated: this is a pointer to someone else's article,
        # not a copy of it. Falls back to Atom's <content>, which is where
        # kill-the-newsletter.com puts the actual email body -- an Atom feed
        # item's <summary> is normally just a short teaser, distinct from
        # <content>, and this bridge leaves <summary> empty entirely. Without
        # this fallback every newsletter issue logged with no text at all,
        # so prefilter() never had anything to match a player or club name
        # against and silently dropped every issue before it reached the
        # model.
        summary = html_to_text(
            child_text(element, "description")
            or child_text(element, "summary")
            or child_text(element, "content")
        )
        items.append({
            "title": title,
            "link": safe_link(child_text(element, "link")),
            "published": child_text(element, "pubDate") or child_text(element, "published"),
            "summary": summary[:300],
            "source": source,
        })
        if len(items) >= FEED_ITEMS_PER_SOURCE:
            break
    return items


def fetch_feeds() -> list[dict]:
    all_items: list[dict] = []
    for url in COMMUNITY_FEEDS:
        source = url.split("/")[2]
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            items = parse_feed(response.text, source)
            all_items.extend(items)
            print(f"[knowledge] feed {source:<28} {len(items)} items")
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge] feed {source:<28} failed: {exc}", file=sys.stderr)
    return all_items


def write_index(official: list[dict], feed_items: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "# FPL agent knowledge base",
        "",
        f"Last built: {now}",
        "",
        "Built by `fpl-agent/knowledge.py`. Official rules pages are stored in full",
        "under `official/` because the agent must never get scoring or chip rules",
        "wrong. Community sources are stored as headlines and links only in",
        "`feeds.json` -- the agent follows a link when it needs detail rather than",
        "archiving other people's writing.",
        "",
        "## Official sources",
        "",
        "| Source | Status | Notes |",
        "|---|---|---|",
    ]
    for entry in official:
        status = "captured" if entry["ok"] else "not captured"
        lines.append(f"| [{entry['slug']}]({entry['url']}) | {status} | {entry['note']} |")

    by_source: dict[str, int] = {}
    for item in feed_items:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    lines += ["", "## Community feeds (headlines only)", "", "| Feed | Items |", "|---|---|"]
    for source, count in sorted(by_source.items()):
        lines.append(f"| {source} | {count} |")
    lines.append("")

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    (KNOWLEDGE_DIR / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[knowledge] wrote knowledge/INDEX.md")


def main() -> None:
    print("[knowledge] fetching official rules pages...")
    official = [fetch_official(source) for source in OFFICIAL_SOURCES]

    print("[knowledge] fetching community feeds...")
    feed_items = fetch_feeds()
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    (KNOWLEDGE_DIR / "feeds.json").write_text(
        json.dumps({"built_at": datetime.now(timezone.utc).isoformat(), "items": feed_items},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[knowledge] wrote knowledge/feeds.json ({len(feed_items)} items)")

    write_index(official, feed_items)

    captured = sum(1 for entry in official if entry["ok"])
    print(f"\n[knowledge] {captured}/{len(official)} official sources captured, "
          f"{len(feed_items)} feed items")
    if captured == 0:
        print("[knowledge] WARNING: no official pages captured -- check INDEX.md for why", file=sys.stderr)


if __name__ == "__main__":
    main()
