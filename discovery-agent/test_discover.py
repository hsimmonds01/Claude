#!/usr/bin/env python3
"""Offline tests for discover.py -- the sandbox can't reach Gemini or Resend,
so both are stubbed at the requests layer. Covers: JSON parsing (fenced,
bare, and broken replies), fuzzy dedupe against seen items, the
once-per-day guard, graceful degradation when the model's reply can't be
parsed, state/history writes after a successful send, and the failure email.

Run: python test_discover.py
"""

import json
import sys
import tempfile
from pathlib import Path

import discover


# ── Stub network layer ─────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeTextResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRequests:
    """Routes gemini/resend/feed URLs to canned responses and records sends."""

    def __init__(self, gemini_text=None, gemini_fail_models=(), feed_xml=None, feed_fail=False):
        self.gemini_text = gemini_text
        self.gemini_fail_models = gemini_fail_models
        self.feed_xml = feed_xml  # single XML string served for every feed URL
        self.feed_fail = feed_fail
        self.sent_emails = []
        self.models_called = []
        self.last_gemini_body = None

    def post(self, url, **kwargs):
        if "generativelanguage" in url:
            model = url.split("/models/")[1].split(":")[0]
            self.models_called.append(model)
            self.last_gemini_body = kwargs.get("json")
            if model in self.gemini_fail_models:
                return FakeResponse({"error": "quota"}, status=429)
            return FakeResponse(
                {"candidates": [{"content": {"parts": [{"text": self.gemini_text}]}}]}
            )
        if "resend" in url:
            self.sent_emails.append(kwargs["json"])
            return FakeResponse({"id": "email_123"})
        raise AssertionError(f"unexpected URL {url}")

    def get(self, url, **kwargs):
        if self.feed_fail:
            raise RuntimeError("simulated feed fetch failure")
        return FakeTextResponse(self.feed_xml or "<rss><channel></channel></rss>")


GOOD_REPLY = """Here are the finds:
```json
[
  {"title": "The Odyssey IMAX tickets on sale", "category": "film",
   "summary": "Nolan's Odyssey gets a 70mm IMAX run; tickets drop Friday.",
   "url": "https://example.com/odyssey", "date_info": "On sale Fri 10am UK",
   "urgency": "act-fast"},
  {"title": "Wynwood x Strawberry Stellar hat drop", "category": "drop",
   "summary": "Second colourway of the Ottawa cap, 200 units.",
   "url": "https://example.com/hat", "date_info": "", "urgency": "this-week"}
]
```"""


def in_temp_dir(fn):
    """Point discover's file constants at a scratch dir for one test."""
    def wrapper():
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old = (discover.INTERESTS_PATH, discover.STATE_PATH, discover.HISTORY_PATH)
            discover.INTERESTS_PATH = tmp_path / "interests.md"
            discover.STATE_PATH = tmp_path / "state.json"
            discover.HISTORY_PATH = tmp_path / "history.json"
            discover.INTERESTS_PATH.write_text("Films, drops, London events.")
            try:
                fn(tmp_path)
            finally:
                (discover.INTERESTS_PATH, discover.STATE_PATH, discover.HISTORY_PATH) = old
    wrapper.__name__ = fn.__name__
    return wrapper


def set_env(monkey=None):
    import os
    os.environ["GEMINI_API_KEY"] = "test-gemini"
    os.environ["RESEND_API_KEY"] = "test-resend"


def _rfc822(dt):
    from email.utils import format_datetime
    return format_datetime(dt)


RSS_FIXTURE_TEMPLATE = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item><title>Recent RSS item</title><link>https://example.com/recent</link>
<pubDate>{recent}</pubDate></item>
<item><title>Stale RSS item</title><link>https://example.com/stale</link>
<pubDate>{stale}</pubDate></item>
</channel></rss>"""

ATOM_FIXTURE_TEMPLATE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Test Atom Feed</title>
<entry><title>Recent Atom entry</title><link href="https://example.com/atom-recent"/>
<updated>{recent}</updated></entry>
</feed>"""


def _fresh_and_stale_xml(template, use_iso=False):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    recent = now - dt.timedelta(days=1)
    stale = now - dt.timedelta(days=discover.FEED_MAX_AGE_DAYS + 5)
    if use_iso:
        return template.format(recent=recent.isoformat().replace("+00:00", "Z"))
    return template.format(recent=_rfc822(recent), stale=_rfc822(stale))


# ── Parsing ────────────────────────────────────────────────────────────

def test_parse_fenced_json():
    items = discover.parse_items(GOOD_REPLY)
    assert len(items) == 2, items
    assert items[0]["title"].startswith("The Odyssey")


def test_parse_bare_json():
    items = discover.parse_items('[{"title": "A", "summary": "B"}]')
    assert len(items) == 1


def test_parse_garbage_returns_empty():
    assert discover.parse_items("Sorry, I couldn't find anything today.") == []
    assert discover.parse_items("```json\n{not valid json]\n```") == []


def test_parse_caps_items():
    many = json.dumps([{"title": f"T{i}", "summary": "s"} for i in range(20)])
    assert len(discover.parse_items(many)) == discover.MAX_ITEMS_PER_DIGEST


# ── Dedupe ─────────────────────────────────────────────────────────────

def test_seen_exact_and_fuzzy():
    seen = [{"title": "The Odyssey IMAX tickets on sale", "date": "2026-07-16"}]
    assert discover.is_seen("The Odyssey IMAX tickets on sale", seen)
    assert discover.is_seen("The Odyssey IMAX tickets on sale!", seen)  # fuzzy
    assert not discover.is_seen("Completely different hat drop", seen)


def test_prune_seen_caps_and_ages():
    old = [{"title": "ancient", "date": "2020-01-01"}]
    recent = [{"title": f"t{i}", "date": discover.today_str()} for i in range(300)]
    pruned = discover.prune_seen(old + recent)
    assert len(pruned) == discover.SEEN_CAP
    assert all(e["date"] != "2020-01-01" for e in pruned)


# ── Free news feeds ───────────────────────────────────────────────────

def test_parse_rss_filters_stale_items():
    xml = _fresh_and_stale_xml(RSS_FIXTURE_TEMPLATE)
    items = discover._parse_feed_xml(xml, "example.com")
    titles = [i["title"] for i in items]
    assert "Recent RSS item" in titles
    assert "Stale RSS item" not in titles  # older than FEED_MAX_AGE_DAYS -- dropped


def test_parse_atom_feed():
    xml = _fresh_and_stale_xml(ATOM_FIXTURE_TEMPLATE, use_iso=True)
    items = discover._parse_feed_xml(xml, "example.com")
    assert len(items) == 1
    assert items[0]["title"] == "Recent Atom entry"
    assert items[0]["link"] == "https://example.com/atom-recent"


def test_parse_feed_xml_malformed_returns_empty():
    assert discover._parse_feed_xml("not xml at all <<<", "example.com") == []
    assert discover._parse_feed_xml("<rss><channel></channel></rss>", "example.com") == []


def test_parse_feed_xml_unparseable_date_is_kept():
    xml = """<rss><channel><item><title>Undated item</title>
      <link>https://example.com/x</link><pubDate>not a real date</pubDate>
    </item></channel></rss>"""
    items = discover._parse_feed_xml(xml, "example.com")
    assert len(items) == 1  # can't tell it's stale -- keep it rather than drop


def test_fetch_feed_items_aggregates_across_feeds():
    xml = _fresh_and_stale_xml(RSS_FIXTURE_TEMPLATE)
    fake = FakeRequests(feed_xml=xml)
    discover.requests = fake
    items = discover.fetch_feed_items()
    # One "Recent RSS item" per feed URL queried (same fixture served everywhere)
    assert len(items) == len(discover._feed_urls())
    assert all(i["title"] == "Recent RSS item" for i in items)


def test_fetch_feed_items_survives_total_failure():
    fake = FakeRequests(feed_fail=True)
    discover.requests = fake
    assert discover.fetch_feed_items() == []  # degrades, doesn't raise


def test_build_prompt_embeds_feed_headlines():
    feed_items = [{"title": "Cool drop happening", "link": "https://x.com/d", "source": "x.com"}]
    prompt = discover.build_prompt("taste profile text", [], feed_items)
    assert "Cool drop happening" in prompt
    assert "https://x.com/d" in prompt


def test_build_prompt_no_feeds_is_conservative():
    prompt = discover.build_prompt("taste profile text", [], [])
    assert "no feed headlines fetched" in prompt.lower()


def test_search_tool_omitted_by_default():
    assert discover.GEMINI_ENABLE_SEARCH is False  # default: no env var set
    fake = FakeRequests(gemini_text=GOOD_REPLY)
    discover.requests = fake
    discover.call_gemini("key", "prompt")
    assert "tools" not in fake.last_gemini_body


def test_search_tool_included_when_enabled():
    discover.GEMINI_ENABLE_SEARCH = True
    try:
        fake = FakeRequests(gemini_text=GOOD_REPLY)
        discover.requests = fake
        discover.call_gemini("key", "prompt")
        assert fake.last_gemini_body["tools"] == [{"google_search": {}}]
    finally:
        discover.GEMINI_ENABLE_SEARCH = False  # restore for later tests


# ── Full flow ──────────────────────────────────────────────────────────

@in_temp_dir
def test_send_flow_writes_state_and_history(tmp_path):
    set_env()
    fake = FakeRequests(gemini_text=GOOD_REPLY)
    discover.requests = fake
    discover.run_digest(dry_run=False, force=False)

    assert len(fake.sent_emails) == 1
    email = fake.sent_emails[0]
    assert "Odyssey" in email["html"]
    assert email["to"] == [discover.EMAIL_TO]

    state = json.loads(discover.STATE_PATH.read_text())
    assert state["last_sent_date"] == discover.today_str()
    assert len(state["seen"]) == 2

    history = json.loads(discover.HISTORY_PATH.read_text())
    assert len(history["digests"]) == 1
    assert len(history["digests"][0]["items"]) == 2


@in_temp_dir
def test_once_per_day_guard(tmp_path):
    set_env()
    discover.STATE_PATH.write_text(json.dumps(
        {"last_sent_date": discover.today_str(), "seen": []}))
    fake = FakeRequests(gemini_text=GOOD_REPLY)
    discover.requests = fake
    discover.run_digest(dry_run=False, force=False)
    assert fake.sent_emails == []  # duplicate trigger no-ops
    assert fake.models_called == []  # doesn't even burn Gemini quota


@in_temp_dir
def test_force_overrides_guard(tmp_path):
    set_env()
    discover.STATE_PATH.write_text(json.dumps(
        {"last_sent_date": discover.today_str(), "seen": []}))
    fake = FakeRequests(gemini_text=GOOD_REPLY)
    discover.requests = fake
    discover.run_digest(dry_run=False, force=True)
    assert len(fake.sent_emails) == 1


@in_temp_dir
def test_pro_quota_falls_back_to_flash(tmp_path):
    set_env()
    fake = FakeRequests(gemini_text=GOOD_REPLY, gemini_fail_models=(discover.GEMINI_MODELS[0],))
    discover.requests = fake
    discover.run_digest(dry_run=False, force=False)
    assert fake.models_called == discover.GEMINI_MODELS
    assert len(fake.sent_emails) == 1


@in_temp_dir
def test_unparseable_reply_still_sends_raw_notes(tmp_path):
    set_env()
    fake = FakeRequests(gemini_text="I found some things but forgot the format.")
    discover.requests = fake
    discover.run_digest(dry_run=False, force=False)
    assert len(fake.sent_emails) == 1
    assert "unformatted" in fake.sent_emails[0]["html"].lower()


@in_temp_dir
def test_all_items_already_seen_sends_quiet_day(tmp_path):
    set_env()
    discover.STATE_PATH.write_text(json.dumps({
        "last_sent_date": None,
        "seen": [
            {"title": "The Odyssey IMAX tickets on sale", "date": discover.today_str()},
            {"title": "Wynwood x Strawberry Stellar hat drop", "date": discover.today_str()},
        ],
    }))
    fake = FakeRequests(gemini_text=GOOD_REPLY)
    discover.requests = fake
    discover.run_digest(dry_run=False, force=False)
    assert len(fake.sent_emails) == 1
    assert "quiet day" in fake.sent_emails[0]["subject"].lower()
    history = json.loads(discover.HISTORY_PATH.read_text())
    assert history["digests"][0]["items"] == []


@in_temp_dir
def test_dry_run_sends_nothing_writes_nothing(tmp_path):
    set_env()
    fake = FakeRequests(gemini_text=GOOD_REPLY)
    discover.requests = fake
    discover.run_digest(dry_run=True, force=False)
    assert fake.sent_emails == []
    assert not discover.STATE_PATH.exists()
    assert not discover.HISTORY_PATH.exists()


def test_failure_email_uses_resend():
    set_env()
    fake = FakeRequests()
    discover.requests = fake
    discover.send_failure_email("boom traceback here")
    assert len(fake.sent_emails) == 1
    assert "failed" in fake.sent_emails[0]["subject"].lower()


# ── Runner ─────────────────────────────────────────────────────────────

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
