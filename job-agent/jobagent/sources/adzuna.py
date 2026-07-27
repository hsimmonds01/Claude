"""Adzuna search API.

Free tier, roughly 1,000 calls a month, so calls are budgeted: one per
(search term x location) pair per run, not per page. At 4 runs a day with a
handful of terms this stays comfortably inside the allowance.

Field names below are taken from Adzuna's documented response shape. Parsing
is deliberately defensive -- every field is fetched with a fallback -- because
a renamed key should cost one blank salary, not the whole run.
"""

from __future__ import annotations

import logging

import requests

from ..models import Job

log = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/1"
RESULTS_PER_CALL = 50
TIMEOUT = 30


def _one_search(
    app_id: str, app_key: str, term: str, location: str, distance: int, max_age_days: int
) -> list[Job]:
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": RESULTS_PER_CALL,
        "what": term,
        "where": location,
        "distance": distance,
        "max_days_old": max_age_days,
        "content-type": "application/json",
    }
    response = requests.get(BASE_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    jobs = []
    for item in payload.get("results", []) or []:
        company = (item.get("company") or {}).get("display_name") or ""
        location_name = (item.get("location") or {}).get("display_name") or ""
        jobs.append(
            Job(
                source="adzuna",
                title=item.get("title") or "",
                company=company,
                url=item.get("redirect_url") or "",
                location=location_name,
                description=item.get("description") or "",
                salary_min=item.get("salary_min"),
                salary_max=item.get("salary_max"),
                posted=(item.get("created") or "")[:10],
            )
        )
    return jobs


def fetch(app_id: str, app_key: str, config) -> list[Job]:
    """Run every configured search term against every configured location.

    One failing term must not lose the others -- a single malformed query or a
    transient 500 shouldn't cost her the whole run -- so failures are logged
    and skipped rather than raised.
    """
    if not app_id or not app_key:
        log.warning("[adzuna] no credentials; skipping")
        return []

    collected: list[Job] = []
    for term in config.search_terms:
        for location in config.locations:
            try:
                found = _one_search(
                    app_id,
                    app_key,
                    term,
                    location,
                    config.max_distance_miles,
                    config.max_age_days,
                )
            except (requests.RequestException, ValueError) as exc:
                log.warning("[adzuna] '%s' in '%s' failed: %s", term, location, exc)
                continue
            log.info("[adzuna] '%s' in '%s': %d results", term, location, len(found))
            collected.extend(found)
    return collected
