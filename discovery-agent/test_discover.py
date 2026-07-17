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


class FakeRequests:
    """Routes gemini/resend URLs to canned responses and records sends."""

    def __init__(self, gemini_text=None, gemini_fail_models=()):
        self.gemini_text = gemini_text
        self.gemini_fail_models = gemini_fail_models
        self.sent_emails = []
        self.models_called = []

    def post(self, url, **kwargs):
        if "generativelanguage" in url:
            model = url.split("/models/")[1].split(":")[0]
            self.models_called.append(model)
            if model in self.gemini_fail_models:
                return FakeResponse({"error": "quota"}, status=429)
            return FakeResponse(
                {"candidates": [{"content": {"parts": [{"text": self.gemini_text}]}}]}
            )
        if "resend" in url:
            self.sent_emails.append(kwargs["json"])
            return FakeResponse({"id": "email_123"})
        raise AssertionError(f"unexpected URL {url}")


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
    fake = FakeRequests(gemini_text=GOOD_REPLY, gemini_fail_models=("gemini-2.5-pro",))
    discover.requests = fake
    discover.run_digest(dry_run=False, force=False)
    assert fake.models_called == ["gemini-2.5-pro", "gemini-2.5-flash"]
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
