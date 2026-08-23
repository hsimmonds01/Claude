"""Push notifications to the phone via ntfy.sh.

Reuses the topic the dock-alerter already uses, so there is no new app to
install or subscribe to.
"""

from __future__ import annotations

import os

import requests

REQUEST_TIMEOUT_SECONDS = 20

# GitHub Actions sets an unset secret to an EMPTY STRING rather than leaving
# the variable absent, so `os.environ.get(..., default)` would hand back ""
# and every notification would 404 into a nonexistent topic. `or` is load
# bearing here -- do not "simplify" it.
DEFAULT_NTFY_TOPIC = "harry-tooley-docks-5494e935"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or DEFAULT_NTFY_TOPIC
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"


def send(title: str, message: str, priority: str = "default", tags: str = "soccer") -> None:
    """Post one notification. Raises on a non-2xx so the run fails loudly."""
    response = requests.post(
        NTFY_URL,
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    # Never log the URL or body: this repo is public, so Actions logs are
    # public, and either would leak the ntfy topic to anyone reading them.
    print(f"ntfy POST -> {response.status_code}")
    response.raise_for_status()


def send_test() -> None:
    """Fire a clearly-labelled alert down the real path, touching no state."""
    send(
        "TEST: Chelsea ticket watch",
        "Practice run of the Chelsea ticket alert - the real one will look "
        "like this when a home fixture is listed or its ballot opens.\n"
        "https://www.eticketing.co.uk/chelseafc",
        priority="urgent",
        tags="rotating_light,soccer",
    )
