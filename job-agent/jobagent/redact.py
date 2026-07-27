"""Strip secret values out of anything on its way to a log or an email.

The specific hazard: Adzuna takes its credentials as query parameters, so a
`requests` exception message reads

    401 Client Error for url: https://api.adzuna.com/...?app_id=X&app_key=Y

Log that and the key is in the Actions log; put it in the failure email and
it's in her inbox too. GitHub Actions masks registered secrets in logs, but
that only covers exact matches in that one place -- it does nothing for the
email, and it isn't something to rely on as the only defence.

Values are read from the environment at call time rather than cached, so a
key rotated mid-run is still redacted.
"""

from __future__ import annotations

import os

# Every environment variable holding something that must never be printed.
# Covers two categories, both of which matter:
#
#   - credentials (API keys, the app password, the ntfy topic, which is the
#     only thing protecting her notifications)
#   - personal data (her mailbox addresses), because she is job-hunting while
#     still employed and a log line naming her job-search inbox is exactly the
#     kind of leak the discretion rules exist to prevent
#
# A test cross-checks this against the workflow, so a secret added there
# without being added here fails the suite rather than leaking quietly.
SECRET_ENV_NAMES = (
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "REED_API_KEY",
    "GEMINI_API_KEY",
    "GMAIL_ADDRESS",
    "GMAIL_APP_PASSWORD",
    "NTFY_TOPIC",
    "DIGEST_TO",
)

# Short values would match far too much ordinary text if redacted blindly.
MIN_REDACTABLE_LENGTH = 8

PLACEHOLDER = "[redacted]"


def scrub(text: str) -> str:
    """Replace any known secret appearing in `text`."""
    if not text:
        return text
    for name in SECRET_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if len(value) >= MIN_REDACTABLE_LENGTH and value in text:
            text = text.replace(value, PLACEHOLDER)
    return text
