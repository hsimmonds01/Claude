#!/usr/bin/env python3
"""Offline tests for deals.py -- the sandbox can't reach HotUKDeals, Reddit,
Gemini or ntfy (confirmed via probe_sources.py), so all four are stubbed at
the requests layer. Covers: feed parsing (heat prefix, merchant/price,
entity-bomb rejection, stale-item filtering), the candidate filter, the
daily cap, permanent seen-dedupe, and -- the point of the whole project --
that a model reply can never inject a url or code into a notification no
matter what extra fields it includes.

Run: python test_deals.py
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import deals


# ── Stub network layer ────────────────────────────────────────────────

class FakeTextResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        pass

    def iter_content(self, chunk_size=8192):
        data = self.text.encode("utf-8")
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    encoding = "utf-8"


class FakeJsonResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRequests:
    def __init__(self, hukd_xml="", reddit_xml="", gemini_text=None):
        self.hukd_xml = hukd_xml
        self.reddit_xml = reddit_xml
        self.gemini_text = gemini_text
        self.ntfy_calls = []

    def get(self, url, **kwargs):
        if "hotukdeals.com" in url:
            return FakeTextResponse(self.hukd_xml)
        if "reddit.com" in url:
            return FakeTextResponse(self.reddit_xml)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, **kwargs):
        if "generativelanguage" in url:
            payload = {"candidates": [{"content": {"parts": [{"text": self.gemini_text}]}}]}
            return FakeJsonResponse(payload)
        if "ntfy.sh" in url:
            self.ntfy_calls.append({"url": url, "headers": kwargs.get("headers", {}), "data": kwargs.get("data")})
            return FakeJsonResponse({}, status=200)
        raise AssertionError(f"unexpected POST {url}")


def set_env(monkeypatch_topic="test-topic", shadow="false"):
    deals.NTFY_TOPIC = monkeypatch_topic
    deals.NTFY_URL = f"https://ntfy.sh/{monkeypatch_topic}"
    deals.SHADOW_MODE = shadow not in ("false", "0", "no") if isinstance(shadow, str) else shadow
    import os
    os.environ["GEMINI_API_KEY"] = "fake-key"


def hukd_item_xml(title, price="£9.99", merchant="Amazon", category="Electronics",
                   description="A great deal", hours_ago=1, extra=""):
    pub = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return f"""<item xmlns:ns0="http://www.pepper.com/rss">
<category>{category}</category>
<ns0:merchant name="{merchant}" price="{price}" />
<title>{title}</title>
<description>{description}</description>
<link>https://www.hotukdeals.com/deals/example-{abs(hash(title)) % 10000}</link>
<pubDate>{pub}</pubDate>
{extra}
</item>"""


def hukd_feed(*items):
    body = "\n".join(items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


def reddit_entry_xml(title, hours_ago=1, content="submitted by /u/someone"):
    pub = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return f"""<entry>
<title>{title}</title>
<link href="https://www.reddit.com/r/ukdeals/comments/example/" />
<published>{pub}</published>
<content type="html">{content}</content>
</entry>"""


def reddit_feed(*entries):
    body = "\n".join(entries)
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'


# ── Feed parsing ───────────────────────────────────────────────────────

def test_heat_prefix_parsed_and_stripped():
    xml = hukd_feed(hukd_item_xml("250° - Amazing Widget"))
    items = deals.parse_hukd_xml(xml, "trending")
    assert items[0]["heat"] == 250
    assert items[0]["clean_title"] == "Amazing Widget"


def test_title_without_heat_prefix_kept_as_is():
    xml = hukd_feed(hukd_item_xml("No Heat Here"))
    items = deals.parse_hukd_xml(xml, "trending")
    assert items[0]["heat"] is None
    assert items[0]["clean_title"] == "No Heat Here"


def test_merchant_and_price_extracted():
    xml = hukd_feed(hukd_item_xml("100° - Thing", price="£49.99", merchant="Argos"))
    items = deals.parse_hukd_xml(xml, "trending")
    assert items[0]["merchant"] == "Argos"
    assert items[0]["price"] == "£49.99"


def test_stale_hukd_item_filtered_out():
    xml = hukd_feed(hukd_item_xml("100° - Old", hours_ago=200))
    assert deals.parse_hukd_xml(xml, "trending") == []


def test_hukd_entity_bomb_rejected():
    xml = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><rss><channel><item><title>t</title></item></channel></rss>'
    assert deals.parse_hukd_xml(xml, "trending") == []


def test_reddit_atom_parsed():
    xml = reddit_feed(reddit_entry_xml("Great Reddit Deal"))
    items = deals.parse_reddit_atom(xml)
    assert len(items) == 1
    assert items[0]["title"] == "Great Reddit Deal"
    assert items[0]["source"] == "reddit"


def test_reddit_sorted_most_recent_first():
    xml = reddit_feed(reddit_entry_xml("Older", hours_ago=5), reddit_entry_xml("Newer", hours_ago=1))
    items = deals.parse_reddit_atom(xml)
    assert [i["title"] for i in items] == ["Newer", "Older"]


def test_reddit_entity_bomb_rejected():
    xml = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><feed><entry><title>t</title></entry></feed>'
    assert deals.parse_reddit_atom(xml) == []


# ── Scoring / extraction ────────────────────────────────────────────────

def test_heat_velocity_computed():
    xml = hukd_feed(hukd_item_xml("120° - Fast Mover", hours_ago=2))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    v = deals.heat_velocity(item)
    assert 59 < v < 61  # 120 / 2h = 60/hr


def test_heat_velocity_none_without_heat():
    xml = hukd_feed(hukd_item_xml("No Heat"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert deals.heat_velocity(item) is None


def test_code_extracted_from_description():
    xml = hukd_feed(hukd_item_xml("50° - Deliveroo offer", description="Use code SAVE20 at checkout"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert deals.extract_code(item) == "SAVE20"


def test_code_not_extracted_from_stopword():
    xml = hukd_feed(hukd_item_xml("50° - Offer", description="see code below for details"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert deals.extract_code(item) is None


def test_no_code_returns_none():
    xml = hukd_feed(hukd_item_xml("50° - Plain deal", description="just a good price"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert deals.extract_code(item) is None


def test_new_customer_flagged():
    xml = hukd_feed(hukd_item_xml("50° - Offer", description="new customers only, first order"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert deals.mentions_new_customer(item)


def test_existing_customer_not_flagged():
    xml = hukd_feed(hukd_item_xml("50° - Offer", description="great value for everyone"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert not deals.mentions_new_customer(item)


# ── Candidate filtering ──────────────────────────────────────────────────

def test_high_velocity_item_is_candidate():
    xml = hukd_feed(hukd_item_xml("500° - Hot", hours_ago=1))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert deals.is_candidate(item)


def test_low_velocity_item_without_code_is_not_candidate():
    xml = hukd_feed(hukd_item_xml("10° - Cold", hours_ago=20, description="nothing special"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert not deals.is_candidate(item)


def test_low_velocity_item_with_code_mention_is_still_candidate():
    xml = hukd_feed(hukd_item_xml("10° - Cold", hours_ago=20, description="use voucher code SAVE5"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    assert deals.is_candidate(item)


def test_reddit_item_always_candidate():
    xml = reddit_feed(reddit_entry_xml("Reddit thing"))
    item = deals.parse_reddit_atom(xml)[0]
    assert deals.is_candidate(item)


def test_dedupe_prefers_hukd_over_reddit_for_same_title():
    xml_h = hukd_feed(hukd_item_xml("500° - Same Deal Here", merchant="Argos"))
    xml_r = reddit_feed(reddit_entry_xml("Same Deal Here"))
    hukd_item = deals.parse_hukd_xml(xml_h, "trending")[0]
    reddit_item = deals.parse_reddit_atom(xml_r)[0]
    kept = deals.dedupe_candidates([hukd_item, reddit_item])
    assert len(kept) == 1
    assert kept[0]["source"] == "hotukdeals"


def test_dedupe_keeps_genuinely_different_titles():
    xml = hukd_feed(hukd_item_xml("500° - First Thing"), hukd_item_xml("500° - Totally Different Item"))
    items = deals.parse_hukd_xml(xml, "trending")
    assert len(deals.dedupe_candidates(items)) == 2


# ── Seen / dedupe memory ─────────────────────────────────────────────────

def test_seen_exact_and_fuzzy():
    seen = [{"title": "Amazing Widget Pro", "date": "2026-08-01"}]
    assert deals.is_seen("Amazing Widget Pro", seen)
    assert deals.is_seen("Amazing Widget Pro!!", seen)  # near-duplicate
    assert not deals.is_seen("Completely Unrelated Item", seen)


def test_prune_seen_drops_old_entries():
    old = (datetime.now(timezone.utc) - timedelta(days=deals.SEEN_MAX_AGE_DAYS + 5)).strftime("%Y-%m-%d")
    seen = [{"title": "old one", "date": old}, {"title": "recent one", "date": deals.today_str()}]
    pruned = deals.prune_seen(seen)
    assert [s["title"] for s in pruned] == ["recent one"]


# ── Selection parsing -- the security-critical part ──────────────────────

def test_selection_parses_fenced_json():
    text = '```json\n[{"index": 1, "reason": "genuinely rare price"}]\n```'
    result = deals.parse_selection(text, n_candidates=3)
    assert result == [{"index": 0, "reason": "genuinely rare price"}]


def test_selection_drops_out_of_range_index():
    text = '[{"index": 99, "reason": "nope"}]'
    assert deals.parse_selection(text, n_candidates=3) == []


def test_selection_drops_entry_missing_reason():
    text = '[{"index": 1}]'
    assert deals.parse_selection(text, n_candidates=3) == []


def test_selection_empty_array_means_silence():
    assert deals.parse_selection("[]", n_candidates=5) == []


def test_selection_ignores_extra_model_supplied_fields():
    """The whole point: even if the model tries to smuggle a url/code/title
    into its reply, parse_selection only ever reads index/reason -- so those
    other fields have no way to reach a notification."""
    text = json.dumps([{
        "index": 1, "reason": "looks good",
        "url": "https://evil.example/rickroll", "code": "FAKE123", "title": "hallucinated title",
    }])
    result = deals.parse_selection(text, n_candidates=2)
    assert result == [{"index": 0, "reason": "looks good"}]
    assert "url" not in result[0] and "code" not in result[0]


def test_selection_garbage_returns_empty():
    assert deals.parse_selection("not json at all", n_candidates=3) == []


def test_selection_deduplicates_repeated_index():
    text = '[{"index": 1, "reason": "a"}, {"index": 1, "reason": "b"}]'
    assert len(deals.parse_selection(text, n_candidates=3)) == 1


# ── Notification building ────────────────────────────────────────────────

def test_notification_frames_code_as_unverified():
    xml = hukd_feed(hukd_item_xml("50° - Deliveroo offer", description="use code SAVE20 today"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    _, message = deals.build_notification(item, "solid discount")
    assert "SAVE20" in message and "NOT verified" in message


def test_notification_shows_new_customer_flag():
    xml = hukd_feed(hukd_item_xml("50° - Offer", description="new customers only"))
    item = deals.parse_hukd_xml(xml, "trending")[0]
    _, message = deals.build_notification(item, "reason")
    assert "new-customers-only" in message.lower()


def test_safe_link_rejects_non_http():
    assert deals.safe_link("javascript:alert(1)") == ""
    assert deals.safe_link("https://www.hotukdeals.com/deals/x") == "https://www.hotukdeals.com/deals/x"


# ── End-to-end run() ──────────────────────────────────────────────────────

def test_daily_cap_enforced_across_multiple_candidates(tmp_path):
    set_env()
    deals.STATE_PATH = tmp_path / "state.json"
    deals.HISTORY_PATH = tmp_path / "history.json"
    product_titles = ["Wireless Headphones", "Cast Iron Skillet", "Robot Vacuum", "Coffee Grinder", "Standing Desk"]
    xml = hukd_feed(*[hukd_item_xml(f"500° - {name}", hours_ago=1) for name in product_titles])
    gemini_reply = json.dumps([{"index": i + 1, "reason": f"reason {i}"} for i in range(5)])
    fake = FakeRequests(hukd_xml=xml, reddit_xml=reddit_feed(), gemini_text=gemini_reply)
    deals.requests = fake

    deals.run(dry_run=False)

    assert len(fake.ntfy_calls) == deals.MAX_PUSHES_PER_DAY
    state = json.loads(deals.STATE_PATH.read_text())
    assert state["push_count"] == deals.MAX_PUSHES_PER_DAY
    history = json.loads(deals.HISTORY_PATH.read_text())
    assert len(history[-1]["capped_out"]) == 5 - deals.MAX_PUSHES_PER_DAY


def test_shadow_mode_logs_but_does_not_push(tmp_path):
    set_env(shadow="true")
    deals.STATE_PATH = tmp_path / "state.json"
    deals.HISTORY_PATH = tmp_path / "history.json"
    xml = hukd_feed(hukd_item_xml("500° - Shadow Deal", hours_ago=1))
    fake = FakeRequests(hukd_xml=xml, reddit_xml=reddit_feed(),
                         gemini_text='[{"index": 1, "reason": "great find"}]')
    deals.requests = fake

    deals.run(dry_run=False)

    assert fake.ntfy_calls == []
    state = json.loads(deals.STATE_PATH.read_text())
    assert state["push_count"] == 0  # shadow mode doesn't consume the cap
    assert len(state["seen"]) == 1  # but still remembered, so it won't be re-proposed


def test_dry_run_writes_no_state():
    set_env()
    deals.STATE_PATH = Path("/tmp/deals-agent-should-not-exist-state.json")
    deals.HISTORY_PATH = Path("/tmp/deals-agent-should-not-exist-history.json")
    deals.STATE_PATH.unlink(missing_ok=True)
    deals.HISTORY_PATH.unlink(missing_ok=True)
    xml = hukd_feed(hukd_item_xml("500° - Dry Run Deal", hours_ago=1))
    fake = FakeRequests(hukd_xml=xml, reddit_xml=reddit_feed(),
                         gemini_text='[{"index": 1, "reason": "great find"}]')
    deals.requests = fake

    deals.run(dry_run=True)

    assert fake.ntfy_calls == []
    assert not deals.STATE_PATH.exists()
    assert not deals.HISTORY_PATH.exists()


def test_permanent_dedupe_across_runs(tmp_path):
    set_env()
    deals.STATE_PATH = tmp_path / "state.json"
    deals.HISTORY_PATH = tmp_path / "history.json"
    xml = hukd_feed(hukd_item_xml("500° - Repeat Deal", hours_ago=1))
    fake = FakeRequests(hukd_xml=xml, reddit_xml=reddit_feed(),
                         gemini_text='[{"index": 1, "reason": "great find"}]')
    deals.requests = fake

    deals.run(dry_run=False)
    assert len(fake.ntfy_calls) == 1

    fake.ntfy_calls = []
    deals.run(dry_run=False)  # same feed content again, same run
    assert fake.ntfy_calls == []  # already seen -- never pushed twice


def test_missing_ntfy_topic_raises_when_actually_sending(tmp_path):
    set_env()
    deals.NTFY_TOPIC = ""
    deals.STATE_PATH = tmp_path / "state.json"
    deals.HISTORY_PATH = tmp_path / "history.json"
    xml = hukd_feed(hukd_item_xml("500° - Deal", hours_ago=1))
    fake = FakeRequests(hukd_xml=xml, reddit_xml=reddit_feed(),
                         gemini_text='[{"index": 1, "reason": "great find"}]')
    deals.requests = fake

    threw = False
    try:
        deals.run(dry_run=False)
    except RuntimeError as exc:
        threw = "DEALS_NTFY_TOPIC" in str(exc)
    assert threw


def test_quiet_run_needs_no_gemini_call_when_no_candidates(tmp_path):
    set_env()
    deals.STATE_PATH = tmp_path / "state.json"
    deals.HISTORY_PATH = tmp_path / "history.json"
    import os
    del os.environ["GEMINI_API_KEY"]  # would raise if the code path tried to call Gemini
    xml = hukd_feed(hukd_item_xml("2° - Totally cold", hours_ago=48, description="boring"))
    fake = FakeRequests(hukd_xml=xml, reddit_xml=reddit_feed())
    deals.requests = fake

    deals.run(dry_run=False)  # must not raise
    os.environ["GEMINI_API_KEY"] = "fake-key"


# ── Runner ─────────────────────────────────────────────────────────────

def main():
    import tempfile
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            if "tmp_path" in test.__code__.co_varnames[:test.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    test(Path(d))
            else:
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
