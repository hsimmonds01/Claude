"""ntfy phone alerts.

**Discretion is a hard requirement here, not a preference.** She is still
employed and looking quietly, so lock-screen text must never carry a company
name or job title -- anyone glancing at her phone on a desk sees "3 new
matches" and nothing else. Detail exists only behind an unlock and a tap.

The ntfy topic name is the only access control that exists: public topics have
no authentication, so the topic name *is* the password. It is read from a
secret and never logged.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://ntfy.sh"
TIMEOUT = 20


def _vague_body(count: int) -> str:
    if count == 1:
        return "1 new match"
    return f"{count} new matches"


def _detailed_body(jobs) -> str:
    lines = []
    for job in jobs:
        lines.append(f"{job.score}/10 · {job.title} — {job.company}")
    return "\n".join(lines)


def build_message(jobs, *, vague: bool) -> tuple[str, str]:
    """(title, body) for the notification.

    With `vague` on -- the default, and correct while she's still employed --
    nothing identifying reaches the lock screen.
    """
    if vague:
        return "Job agent", _vague_body(len(jobs))
    return f"{len(jobs)} strong match{'es' if len(jobs) != 1 else ''}", _detailed_body(jobs)


def send(topic: str, jobs, *, vague: bool, feedback_url: str = "") -> bool:
    """Send one push covering `jobs`. Returns True if it went.

    Never raises: a failed push must not take down the run that still has an
    email digest to send.
    """
    if not topic:
        log.warning("[ntfy] no topic configured; skipping push")
        return False
    if not jobs:
        return False

    title, body = build_message(jobs, vague=vague)

    headers = {
        "Title": title,
        "Priority": "default",
        # Tapping opens the best-scoring role. Detail only ever appears after
        # an unlock, which is the point.
        "Click": jobs[0].url,
    }
    if feedback_url:
        # The steering loop only gets used if it's one tap away.
        headers["Actions"] = f"view, Not right?, {feedback_url}, clear=true"

    try:
        response = requests.post(
            f"{BASE_URL}/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        # Deliberately does not log the topic -- it is the only secret
        # protecting her notifications.
        log.error("[ntfy] push failed: %s", exc)
        return False

    log.info("[ntfy] pushed %d role(s)", len(jobs))
    return True


def choose(scored, *, threshold: int, allowance: int, quiet: bool) -> list:
    """Pick which roles are worth interrupting her for.

    Anything held back is not lost -- it still reaches her in the next digest,
    which is why suppressing during quiet hours is safe.
    """
    if quiet or allowance <= 0:
        return []
    strong = [job for job in scored if job.score >= threshold]
    strong.sort(key=lambda job: job.score, reverse=True)
    return strong[:allowance]
