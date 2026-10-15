#!/usr/bin/env python3
"""One-off discovery for the VOXI Drop watch. Read-only: sends no alerts and
touches no state. Run it from GitHub Actions (the sandbox can't reach VOXI).

It answers two questions, printing everything prefixed with 'DISCOVER:'
(Actions logs are public, so key/secret/token-looking values are never
printed):

1. Does the public Drop page expose a machine-readable "drop is live" signal
   that doesn't need a login? It lists the page's embedded feature switches
   (LaunchDarkly flags) and any drop/reward-looking paths or switches in the
   page's JavaScript bundles.
2. Which public announcement channels can a GitHub runner actually read?
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import requests

from check_drop import BROWSER_HEADERS, DROP_URL

TIMEOUT = 20
SECRET_LOOKING = re.compile(r"key|secret|token|password|auth|dsn", re.IGNORECASE)
DROP_HINT = re.compile(r"drop|reward|giveaway|prize", re.IGNORECASE)

SOURCES = [
    ("reddit r/VOXI newest posts", "https://www.reddit.com/r/VOXI/new.rss"),
    ("reddit r/VOXI search 'drop'", "https://www.reddit.com/r/VOXI/search.rss?q=drop&restrict_sr=1&sort=new"),
    ("X / Twitter VOXI", "https://x.com/voxi"),
    ("Facebook VOXI", "https://www.facebook.com/VOXImobile"),
    ("Instagram VOXI", "https://www.instagram.com/voximobile/"),
    ("VOXI help: Drop category", "https://www.voxi.co.uk/help/categories/4e3aff50-8302-4880-aa70-d4a35bb78dbb"),
    ("VOXI sitemap", "https://www.voxi.co.uk/sitemap.xml"),
]


def say(text: str) -> None:
    print(f"DISCOVER: {text}")


def get(url: str) -> requests.Response | None:
    try:
        return requests.get(url, headers=BROWSER_HEADERS, timeout=TIMEOUT)
    except requests.RequestException as exc:
        say(f"  fetch failed for {url}: {exc.__class__.__name__}")
        return None


def feature_flags(html: str) -> None:
    say("== embedded feature switches ==")
    idx = html.find('"$flagsState"')
    if idx == -1:
        say("no $flagsState block found in the page")
        return
    start = html.rfind("'{", 0, idx)
    end = html.find("'", idx)
    flags = None
    if start != -1 and end != -1:
        try:
            flags = json.loads(html[start + 1:end])
        except ValueError:
            flags = None
    if flags is None:
        names = sorted(set(re.findall(r'\\?"([A-Za-z0-9_]{3,40})\\?":\s*(?:true|false)', html[max(0, idx - 4000):idx])))
        say(f"could not parse as JSON; boolean-looking names nearby: {names}")
        return
    for name in sorted(k for k in flags if not k.startswith("$")):
        value = flags[name]
        shown = "(hidden)" if SECRET_LOOKING.search(name) else json.dumps(value)[:80]
        marker = "   <-- drop/reward related" if DROP_HINT.search(name) else ""
        say(f"  {name} = {shown}{marker}")


def bundle_urls(html: str) -> list[str]:
    found = re.findall(r'<script[^>]+src="([^"]+)"', html, re.IGNORECASE)
    found += re.findall(r'href="([^"]+\.js[^"]*)"', html, re.IGNORECASE)
    urls = []
    for raw in found:
        url = urljoin(DROP_URL, raw)
        if url not in urls:
            urls.append(url)
    return urls


def scan_bundles(html: str) -> None:
    say("== drop/reward paths and switches inside the page's JavaScript ==")
    urls = bundle_urls(html)
    say(f"{len(urls)} script files referenced; scanning up to 25")
    path_pat = re.compile(r"""["'`]((?:https?://[^"'`\s]{0,80})?/[A-Za-z0-9_\-./{}$]*(?:drop|reward|giveaway)[A-Za-z0-9_\-./{}$?=&]*)["'`]""", re.IGNORECASE)
    ident_pat = re.compile(r"\b([A-Za-z_]*(?:voxiDrop|VoxiDrop|dropLive|isDrop|dropStatus|dropActive|dropOpen|claimable)[A-Za-z_]*)\b")
    paths, idents = set(), set()
    for url in urls[:25]:
        resp = get(url)
        if resp is None or resp.status_code != 200 or len(resp.text) > 6_000_000:
            continue
        for match in path_pat.findall(resp.text):
            paths.add(match)
        for match in ident_pat.findall(resp.text):
            idents.add(match)
        host = urlparse(url).netloc
        say(f"  scanned {host}{urlparse(url).path[-50:]} ({len(resp.text)} chars)")
    for match in path_pat.findall(html):
        paths.add(match)
    say(f"drop/reward-looking paths found ({len(paths)}):")
    for p in sorted(paths)[:60]:
        say(f"  {p}")
    say(f"drop-state-looking identifiers found ({len(idents)}):")
    for i in sorted(idents)[:60]:
        say(f"  {i}")


def check_sources() -> None:
    say("== can a GitHub runner read the announcement channels? ==")
    for label, url in SOURCES:
        resp = get(url)
        if resp is None:
            say(f"{label}: UNREACHABLE")
            continue
        body = resp.text
        titles = re.findall(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
        walled = bool(re.search(r"log ?in|sign ?in|login", body[:6000], re.IGNORECASE)) and resp.status_code == 200
        say(f"{label}: HTTP {resp.status_code}, {len(body)} chars" + (", looks login-walled" if walled else ""))
        for t in titles[1:6] if len(titles) > 1 else titles[:3]:
            clean = " ".join(t.split())[:110]
            say(f"    title: {clean}")
        if "sitemap" in url:
            hits = [u for u in re.findall(r"<loc>(.*?)</loc>", body) if DROP_HINT.search(u)]
            say(f"    drop/reward URLs in sitemap ({len(hits)}): {hits[:10]}")


def reddit_history() -> None:
    """Dump recent public r/VOXI post titles with timestamps, to judge whether
    community posts reliably mark drop days (and how soon after release)."""
    say("== r/VOXI recent posts (public titles, newest first) ==")
    resp = get("https://www.reddit.com/r/VOXI/new.rss?limit=100")
    if resp is None or resp.status_code != 200:
        say(f"could not read the feed (HTTP {resp.status_code if resp else 'none'})")
        return
    entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL)
    say(f"{len(entries)} posts in feed")
    for entry in entries:
        title = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
        when = re.search(r"<updated>(.*?)</updated>", entry)
        if title and when:
            say(f"  {when.group(1)[:16]}  {' '.join(title.group(1).split())[:100]}")


def main() -> None:
    resp = get(DROP_URL)
    if resp is None or resp.status_code != 200:
        say("could not load the Drop page; stopping")
        return
    html = resp.text
    say(f"Drop page loaded: {len(html)} chars")
    feature_flags(html)
    scan_bundles(html)
    check_sources()
    reddit_history()


if __name__ == "__main__":
    main()
