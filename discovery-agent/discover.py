"""Daily Discovery digest.

Asks Gemini (with Google Search grounding) for the coolest newly-announced
things matching interests.md -- ticket releases, limited drops, London events,
genuinely interesting products -- then emails a styled digest via Resend.

Designed to run headless from GitHub Actions, triggered by cron-job.org
hitting the workflow_dispatch endpoint (GitHub's native schedule is the
best-effort backup, same as the sibling alerter projects).

State files (committed back to main by the workflow):
  state.json    -- last_sent_date + rolling list of already-reported items,
                   so overlapping triggers can't double-send and yesterday's
                   hat doesn't reappear tomorrow.
  history.json  -- append-only archive of every digest, read by
                   dashboard.html for the browsable UI.

Modes:
  (default)          research + email + update state
  --dry-run          research + print the digest, no email, no state writes
  --test-email       send a small sample digest through the real Resend path
  --force            send even if state says today's digest already went out

Env vars: GEMINI_API_KEY (required), RESEND_API_KEY (required unless
--dry-run), DIGEST_TO (defaults to the address below).
"""

from __future__ import annotations

import argparse
import difflib
import html as html_lib
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
INTERESTS_PATH = BASE_DIR / "interests.md"
STATE_PATH = BASE_DIR / "state.json"
HISTORY_PATH = BASE_DIR / "history.json"

# Pro first for taste, Flash as the automatic fallback if Pro errors or the
# free-tier quota is exhausted. Swapping models later is a one-line edit here.
GEMINI_MODELS = ["gemini-2.5-pro", "gemini-2.5-flash"]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_LIST_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

RESEND_URL = "https://api.resend.com/emails"
# Resend's free tier sends from their shared address until a personal domain
# is verified. Deliverable to the account owner's own inbox without setup.
EMAIL_FROM = "Daily Discovery <onboarding@resend.dev>"
EMAIL_TO = os.environ.get("DIGEST_TO") or "hsimmonds01@gmail.com"

DASHBOARD_URL = "https://raw.githack.com/hsimmonds01/Claude/main/discovery-agent/dashboard.html"

REQUEST_TIMEOUT_SECONDS = 120
MAX_ITEMS_PER_DIGEST = 8
# Keep roughly two months of titles for dedupe; cap so state.json can't grow
# without bound.
SEEN_CAP = 250
SEEN_MAX_AGE_DAYS = 60
# Two titles this similar are treated as the same item even if worded
# differently ("Odyssey IMAX tickets" vs "The Odyssey — IMAX on-sale").
FUZZY_MATCH_THRESHOLD = 0.82

URGENCY_STYLES = {
    "act-fast": ("ACT FAST", "#b91c1c", "#fee2e2"),
    "this-week": ("THIS WEEK", "#b45309", "#fef3c7"),
    "heads-up": ("HEADS UP", "#1d4ed8", "#dbeafe"),
}


def today_str() -> str:
    """Date in UK terms -- the digest is a 'this morning' artefact."""
    # UTC is close enough for a morning run; avoids a tz dependency.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── State ──────────────────────────────────────────────────────────────


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_state() -> dict:
    state = load_json(STATE_PATH, {})
    state.setdefault("last_sent_date", None)
    state.setdefault("seen", [])
    return state


def prune_seen(seen: list[dict]) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    fresh = [s for s in seen if s.get("date", "9999") >= cutoff]
    return fresh[-SEEN_CAP:]


def normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def is_seen(title: str, seen: list[dict]) -> bool:
    norm = normalise_title(title)
    if not norm:
        return False
    for entry in seen:
        prev = normalise_title(entry.get("title", ""))
        if not prev:
            continue
        if norm == prev:
            return True
        if difflib.SequenceMatcher(None, norm, prev).ratio() >= FUZZY_MATCH_THRESHOLD:
            return True
    return False


# ── Gemini ─────────────────────────────────────────────────────────────


def build_prompt(interests: str, seen: list[dict]) -> str:
    recent_titles = "\n".join(f"- {s['title']}" for s in seen[-80:]) or "(none yet)"
    now = datetime.now(timezone.utc)
    return f"""You are a sharp, plugged-in personal culture scout. Today is \
{now.strftime("%A %d %B %Y")}. Use Google Search to find the coolest things \
your client would genuinely want to know about TODAY.

YOUR CLIENT'S TASTE PROFILE:
{interests}

WHAT COUNTS AS A FIND (all must hold):
- Genuinely new: announced/revealed in the last ~4 days, OR an upcoming
  on-sale date, release date, opening or deadline the client can still act on.
- Actionable: there is a link and, wherever possible, a date/time to act.
- Matches the taste profile, including its exclusions.

SEARCH STRATEGY: run several distinct searches across the profile's themes
(film/IMAX ticket on-sales, limited drops/collabs, London event announcements,
notable product launches). Prefer primary sources and reputable coverage.

ALREADY REPORTED -- do NOT repeat any of these (or near-duplicates):
{recent_titles}

OUTPUT: return ONLY a JSON array (inside a ```json code fence) of the best
3-{MAX_ITEMS_PER_DIGEST} finds, ranked coolest first. Quality over quantity --
if only 3 things are genuinely great, return 3. Each item:
{{
  "title": "short punchy headline",
  "category": "one of: film, drop, event, product, other",
  "summary": "1-2 sentences: what it is and why it's cool for THIS client",
  "url": "the most useful single link (booking/product page beats news article)",
  "date_info": "the key date/time, e.g. 'Tickets on sale Fri 19 Jul, 10am UK' -- or '' if none",
  "urgency": "one of: act-fast, this-week, heads-up"
}}
No prose before or after the JSON."""


def call_gemini(api_key: str, prompt: str) -> tuple[str, str]:
    """Returns (response_text, model_used). Tries Pro, falls back to Flash."""
    last_error: Exception | None = None
    for model in GEMINI_MODELS:
        try:
            response = requests.post(
                GEMINI_URL.format(model=model),
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "tools": [{"google_search": {}}],
                    "generationConfig": {"temperature": 0.7},
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in (429, 500, 503):
                raise RuntimeError(f"{model} returned HTTP {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            payload = response.json()
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
            if not text.strip():
                raise RuntimeError(f"{model} returned an empty response")
            return text, model
        except Exception as exc:  # noqa: BLE001 -- any failure means try the next model
            last_error = exc
            print(f"[gemini] {model} failed: {exc}", file=sys.stderr)
            time.sleep(3)
    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def list_available_models(api_key: str) -> None:
    """Diagnostic only -- print every model this key can call generateContent
    on, so model-ID fixes are based on what Google actually reports rather
    than another guess."""
    response = requests.get(GEMINI_LIST_MODELS_URL, params={"key": api_key}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    models = response.json().get("models", [])
    print(f"{len(models)} models visible to this key:\n")
    for m in sorted(models, key=lambda m: m.get("name", "")):
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" in methods:
            print(f"  {m['name']}  (display: {m.get('displayName', '?')})")


def parse_items(text: str) -> list[dict]:
    """Pull the JSON array out of the model's reply, tolerating stray prose."""
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    candidates = []
    if fence:
        candidates.append(fence.group(1))
    bracket = re.search(r"\[.*\]", text, re.DOTALL)
    if bracket:
        candidates.append(bracket.group(0))
    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            items = [d for d in data if isinstance(d, dict) and d.get("title") and d.get("summary")]
            if items:
                return items[:MAX_ITEMS_PER_DIGEST]
    return []


# ── Email ──────────────────────────────────────────────────────────────


def render_item_html(item: dict) -> str:
    # Model output goes into HTML -- escape text fields and only link out to
    # real http(s) URLs so a malformed reply can't inject markup.
    label, fg, bg = URGENCY_STYLES.get(item.get("urgency", ""), URGENCY_STYLES["heads-up"])
    category = html_lib.escape((item.get("category") or "other").strip().lower())
    date_info = html_lib.escape((item.get("date_info") or "").strip())
    title = html_lib.escape((item.get("title") or "").strip())
    summary = html_lib.escape((item.get("summary") or "").strip())
    url = (item.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        url = ""
    url = html_lib.escape(url, quote=True)
    date_row = (
        f'<div style="margin-top:8px;font-size:13px;font-weight:600;color:#111827;">'
        f"&#128197; {date_info}</div>"
        if date_info
        else ""
    )
    link_row = (
        f'<div style="margin-top:10px;"><a href="{url}" '
        f'style="font-size:13px;font-weight:600;color:#4f46e5;text-decoration:none;">'
        f"Open link &#8594;</a></div>"
        if url
        else ""
    )
    return f"""
    <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:18px 20px;margin-bottom:14px;">
      <div>
        <span style="display:inline-block;font-size:11px;font-weight:700;letter-spacing:0.5px;color:{fg};background:{bg};border-radius:999px;padding:3px 10px;">{label}</span>
        <span style="display:inline-block;font-size:11px;font-weight:600;letter-spacing:0.5px;color:#6b7280;background:#f3f4f6;border-radius:999px;padding:3px 10px;margin-left:6px;text-transform:uppercase;">{category}</span>
      </div>
      <div style="font-size:17px;font-weight:700;color:#111827;margin-top:10px;line-height:1.3;">{title}</div>
      <div style="font-size:14px;color:#374151;margin-top:6px;line-height:1.5;">{summary}</div>
      {date_row}
      {link_row}
    </div>"""


def render_email_html(items: list[dict], model_used: str, note: str = "") -> str:
    date_line = datetime.now(timezone.utc).strftime("%A %d %B %Y")
    cards = "\n".join(render_item_html(item) for item in items)
    note_html = (
        f'<div style="font-size:12px;color:#92400e;background:#fef3c7;border-radius:8px;padding:10px 14px;margin-bottom:14px;">{note}</div>'
        if note
        else ""
    )
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f3f4f6;">
  <div style="max-width:560px;margin:0 auto;padding:24px 16px;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <div style="background:#111827;border-radius:14px;padding:26px 24px;margin-bottom:18px;">
      <div style="font-size:22px;font-weight:800;color:#ffffff;">&#10024; Daily Discovery</div>
      <div style="font-size:13px;color:#9ca3af;margin-top:4px;">{date_line} &middot; {len(items)} finds</div>
    </div>
    {note_html}
    {cards}
    <div style="text-align:center;padding:16px 8px;font-size:11px;color:#9ca3af;line-height:1.6;">
      Scouted by {model_used} &middot; <a href="{DASHBOARD_URL}" style="color:#6b7280;">browse past finds</a><br>
      Tune what appears here by editing <b>discovery-agent/interests.md</b> in the repo.
    </div>
  </div>
</body>
</html>"""


def send_email(api_key: str, subject: str, html: str) -> None:
    response = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": EMAIL_FROM, "to": [EMAIL_TO], "subject": subject, "html": html},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    print(f"[resend] sent '{subject}' to {EMAIL_TO} (id {response.json().get('id', '?')})")


def send_failure_email(reason: str) -> None:
    """No digest should ever fail silently -- 'no email' must always mean
    'check the logs', so failures send their own short email."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("[resend] no API key; cannot send failure email", file=sys.stderr)
        return
    html = f"""<div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:20px;">
      <h2 style="color:#b91c1c;">&#9888;&#65039; Daily Discovery failed today</h2>
      <p>The digest didn't go out this morning. Error:</p>
      <pre style="background:#f3f4f6;padding:12px;border-radius:8px;white-space:pre-wrap;font-size:12px;">{reason}</pre>
      <p style="font-size:13px;color:#6b7280;">Check the run logs under the repo's Actions tab, or just ask Claude to investigate.</p>
    </div>"""
    try:
        send_email(api_key, "⚠️ Daily Discovery failed today", html)
    except Exception as exc:  # noqa: BLE001
        print(f"[resend] failure email also failed: {exc}", file=sys.stderr)


# ── Main flow ──────────────────────────────────────────────────────────


def run_digest(dry_run: bool, force: bool) -> None:
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    resend_key = os.environ.get("RESEND_API_KEY")
    if not dry_run and not resend_key:
        raise RuntimeError("RESEND_API_KEY is not set")

    state = load_state()
    today = today_str()
    if state["last_sent_date"] == today and not force and not dry_run:
        # Duplicate trigger (cron-job.org + GitHub's backup schedule both
        # fired) -- the whole point of this guard.
        print(f"Already sent today's digest ({today}); nothing to do.")
        return

    interests = INTERESTS_PATH.read_text(encoding="utf-8")
    state["seen"] = prune_seen(state["seen"])

    text, model_used = call_gemini(gemini_key, build_prompt(interests, state["seen"]))
    items = parse_items(text)
    note = ""
    if not items:
        # The model replied but not in parseable form -- degrade gracefully
        # rather than dying: send its raw text so the morning email still
        # arrives with something useful in it.
        note = "The scout's reply couldn't be fully formatted today — raw notes below."
        items = [{
            "title": "Today's finds (unformatted)",
            "category": "other",
            "summary": text[:2500],
            "url": "",
            "date_info": "",
            "urgency": "heads-up",
        }]

    fresh = [i for i in items if not is_seen(i["title"], state["seen"])]
    if not fresh:
        note = "Everything found today was already covered recently — quiet day."
        fresh = []

    if dry_run:
        print(f"--- DRY RUN ({model_used}) ---")
        print(json.dumps(fresh, indent=2, ensure_ascii=False))
        return

    subject = f"✨ Daily Discovery — {datetime.now(timezone.utc).strftime('%a %d %b')}"
    if fresh:
        html = render_email_html(fresh, model_used, note)
    else:
        html = render_email_html([], model_used, note or "No genuinely new finds today.")
        subject = f"Daily Discovery — quiet day ({datetime.now(timezone.utc).strftime('%a %d %b')})"
    send_email(resend_key, subject, html)

    # Only after a successful send: record state + archive for the dashboard.
    state["last_sent_date"] = today
    for item in fresh:
        state["seen"].append({"title": item["title"], "url": item.get("url", ""), "date": today})
    state["seen"] = prune_seen(state["seen"])
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    history = load_json(HISTORY_PATH, {"digests": []})
    history["digests"] = [d for d in history["digests"] if d.get("date") != today]
    history["digests"].append({"date": today, "model": model_used, "items": fresh})
    HISTORY_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Digest sent: {len(fresh)} items via {model_used}.")


def run_test_email() -> None:
    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        raise RuntimeError("RESEND_API_KEY is not set")
    sample = [{
        "title": "Test: your Daily Discovery pipeline works",
        "category": "other",
        "summary": "This is a sample card sent by --test-email to prove the Resend path end-to-end. The real digest will look like this.",
        "url": "https://github.com/hsimmonds01/Claude",
        "date_info": "Sent just now",
        "urgency": "heads-up",
    }]
    send_email(resend_key, "🧪 Daily Discovery — test email", render_email_html(sample, "test mode"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="research and print, no email, no state writes")
    parser.add_argument("--test-email", action="store_true", help="send a sample digest via the real Resend path")
    parser.add_argument("--force", action="store_true", help="send even if already sent today")
    parser.add_argument("--list-models", action="store_true", help="print models this key can use, then exit")
    args = parser.parse_args()

    if args.list_models:
        list_available_models(os.environ["GEMINI_API_KEY"])
        return

    try:
        if args.test_email:
            run_test_email()
        else:
            run_digest(dry_run=args.dry_run, force=args.force)
    except Exception:
        reason = traceback.format_exc()
        print(reason, file=sys.stderr)
        if not args.dry_run:
            send_failure_email(reason)
        sys.exit(1)


if __name__ == "__main__":
    main()
