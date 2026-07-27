"""Reed.co.uk search API.

Authentication is HTTP Basic with the API key as the username and an empty
password -- an unusual arrangement that's easy to get wrong, hence the
explicit empty string rather than omitting it.

Reed leans heavily towards recruitment agencies. That isn't filtered here on
purpose: agency-vs-direct is a judgement for the scoring step, where she can
express it herself with a standing rule in feedback.md, rather than a rule
baked into code she can't edit.
"""

from __future__ import annotations

import logging

import requests

from .. import redact
from ..models import Job

log = logging.getLogger(__name__)

BASE_URL = "https://www.reed.co.uk/api/1.0/search"
RESULTS_PER_CALL = 100
TIMEOUT = 30


def _one_search(api_key: str, term: str, location: str, distance: int) -> list[Job]:
    params = {
        "keywords": term,
        "locationName": location,
        "distanceFromLocation": distance,
        "resultsToTake": RESULTS_PER_CALL,
    }
    response = requests.get(BASE_URL, params=params, auth=(api_key, ""), timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    jobs = []
    for item in payload.get("results", []) or []:
        jobs.append(
            Job(
                source="reed",
                title=item.get("jobTitle") or "",
                company=item.get("employerName") or "",
                url=item.get("jobUrl") or "",
                location=item.get("locationName") or "",
                description=item.get("jobDescription") or "",
                salary_min=item.get("minimumSalary"),
                salary_max=item.get("maximumSalary"),
                posted=_iso_date(item.get("date") or ""),
            )
        )
    return jobs


def _iso_date(raw: str) -> str:
    """Reed returns dd/mm/yyyy; everything downstream expects ISO."""
    parts = raw.split("/")
    if len(parts) == 3 and all(parts):
        day, month, year = parts
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return ""


def fetch(api_key: str, config) -> list[Job]:
    if not api_key:
        log.warning("[reed] no API key; skipping")
        return []

    collected: list[Job] = []
    for term in config.search_terms:
        for location in config.locations:
            try:
                found = _one_search(api_key, term, location, config.max_distance_miles)
            except (requests.RequestException, ValueError) as exc:
                log.warning(
                    "[reed] '%s' in '%s' failed: %s",
                    term,
                    location,
                    redact.scrub(str(exc)),
                )
                continue
            log.info("[reed] '%s' in '%s': %d results", term, location, len(found))
            collected.extend(found)
    return collected
