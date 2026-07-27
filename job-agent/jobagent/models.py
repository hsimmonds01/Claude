"""Core job representation and the fingerprint used to recognise duplicates.

The fingerprint is the load-bearing idea in this module. The same vacancy
routinely appears on Adzuna, on Reed, and in a LinkedIn alert email, each with
a completely different URL and a slightly different title. Deduping by URL
would show her the same job three times in one digest, which is the fastest
way to make her stop trusting the tool -- so identity is derived from the
content instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# Suffixes companies append to their legal name that carry no identity.
_COMPANY_SUFFIXES = re.compile(
    r"\b(ltd|limited|plc|llp|llc|inc|incorporated|group|holdings|uk|"
    r"international|company|co)\b",
    re.IGNORECASE,
)

# Bracketed asides in titles: "(Hybrid)", "[Maternity Cover]", "(12m FTC)".
_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")

# Agency reference numbers: "- Ref 12345", "(JR-2291)", "#4471".
_REF_NUMBER = re.compile(r"\b(ref|req|job|jr|vac)[-_ .]?\d+\b|#\d+", re.IGNORECASE)

# Salary fragments some boards stuff into the title.
_SALARY_IN_TITLE = re.compile(r"£\s?[\d,.]+\s?(k|per annum|pa|p\.a\.)?", re.IGNORECASE)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Deliberately NOT stripped from titles: seniority words. An earlier draft of
# the plan proposed removing them, but "Operations Manager" and "Senior
# Operations Manager" are genuinely different jobs and merging them would hide
# one behind the other. Over-merging is a silent failure; under-merging is a
# visible annoyance. Prefer the visible one.


# Only these schemes are ever rendered as a clickable link. Job data is
# third-party text -- an advert or an alert email can carry any URL it likes,
# and "javascript:" or "data:" in an href is a live scripting payload in some
# mail clients. Anything else is dropped rather than sanitised.
_SAFE_URL_SCHEMES = ("https://", "http://")


def is_safe_link(url: str) -> bool:
    """True when a URL is safe to put in an href or a notification tap target."""
    candidate = (url or "").strip()
    if not candidate.casefold().startswith(_SAFE_URL_SCHEMES):
        return False
    # A control character can split an HTTP header or an email header, so a
    # URL carrying one never gets used.
    return not any(ord(char) < 32 or ord(char) == 127 for char in candidate)


def _normalise_url(raw: str) -> str:
    """Strip tracking noise so one link doesn't look like several.

    Alert emails append per-send campaign parameters, so the same vacancy
    arrives on Monday and Wednesday with different query strings.
    """
    url = raw.split("#", 1)[0]
    url = url.split("?", 1)[0]
    return url.rstrip("/").casefold()


def normalise_company(raw: str) -> str:
    """Reduce a company name to a stable identity token."""
    lowered = raw.casefold()
    lowered = _COMPANY_SUFFIXES.sub(" ", lowered)
    return _NON_ALNUM.sub("", lowered)


def normalise_title(raw: str) -> str:
    """Reduce a job title to a stable identity token."""
    lowered = raw.casefold()
    lowered = _BRACKETED.sub(" ", lowered)
    lowered = _REF_NUMBER.sub(" ", lowered)
    lowered = _SALARY_IN_TITLE.sub(" ", lowered)
    # Boards often tack the location onto the title after a dash or pipe.
    lowered = re.split(r"\s+[-–—|]\s+", lowered)[0]
    return _NON_ALNUM.sub("", lowered)


@dataclass(frozen=True)
class Job:
    """One vacancy, from one source, before any merging."""

    source: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    posted: str = ""  # ISO date string, "" when the source doesn't say

    @property
    def fingerprint(self) -> str:
        """Content-derived identity, shared by the same role across sources.

        Company plus title only -- location is deliberately excluded. The same
        role is described as "London", "City of London" and "London, Greater
        London" by different boards, and any location-aware key would fail to
        match those and let duplicates through. The cost is that a genuinely
        multi-city posting of one title collapses to a single entry, which is
        a fair trade: she still sees the role and can open it.

        When the company is unknown -- which happens with alert emails, where
        the layout sometimes yields a title and a link but no employer -- fall
        back to the URL. Without that fallback every companyless "Operations
        Associate" would share one fingerprint and silently swallow each
        other. Such a job can no longer merge with its twin from an API, so
        she may occasionally see it twice; showing a duplicate is a far
        smaller failure than hiding a real vacancy.
        """
        if not self.company.strip():
            return f"url:{_normalise_url(self.url)}"
        return f"{normalise_company(self.company)}|{normalise_title(self.title)}"

    @property
    def is_usable(self) -> bool:
        """Enough substance to be worth scoring, and safe to link to.

        A company name is *not* required -- see `fingerprint`. A title and a
        safe, working link are the minimum the AI needs to say something
        useful. An unsafe URL disqualifies the job entirely rather than being
        stripped: a job she cannot be given a link to is of no use to her, and
        it is not worth carrying a half-usable record through the pipeline.
        """
        return bool(self.title.strip()) and is_safe_link(self.url)


# Source preference when the same role arrives from several places. A company's
# own careers page is the canonical link -- no agency wrapper, no tracking
# redirect, and it's the one that stays alive longest.
SOURCE_RANK = {
    "company": 0,
    "adzuna": 1,
    "reed": 2,
    "inbox": 3,
}


@dataclass(frozen=True)
class MergedJob:
    """One vacancy after identical roles from several sources are combined.

    A role appearing on three sources is a *stronger* signal, not two pieces
    of rubbish to throw away, so every source and URL found is retained.
    """

    fingerprint: str
    best: Job
    sources: tuple[str, ...] = ()
    all_urls: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return self.best.title

    @property
    def company(self) -> str:
        return self.best.company

    @property
    def url(self) -> str:
        return self.best.url


@dataclass(frozen=True)
class ScoredJob:
    """A merged role plus its frozen verdict, ready to be sent."""

    job: MergedJob
    score: int
    reason: str

    # Delegated so the notification layer doesn't have to reach through two
    # objects to render one row.
    @property
    def title(self) -> str:
        return self.job.title

    @property
    def company(self) -> str:
        return self.job.company

    @property
    def url(self) -> str:
        return self.job.url

    @property
    def best(self) -> Job:
        return self.job.best

    @property
    def sources(self) -> tuple[str, ...]:
        return self.job.sources

    @property
    def locations(self) -> tuple[str, ...]:
        return self.job.locations

    @property
    def fingerprint(self) -> str:
        return self.job.fingerprint


def _rank(job: Job) -> tuple[int, int]:
    """Sort key picking the best representative of a duplicate set.

    Prefer a trusted source, then the richest description -- an entry with a
    real job description scores far better than a bare title from an alert
    email, so the fullest version should be the one that reaches the AI.
    """
    return (SOURCE_RANK.get(job.source, 99), -len(job.description or ""))


def merge(jobs: list[Job]) -> list[MergedJob]:
    """Collapse jobs sharing a fingerprint into one entry each.

    Order is preserved by first appearance, so an upstream source ordering
    stays meaningful and the output is deterministic for tests.
    """
    grouped: dict[str, list[Job]] = {}
    for job in jobs:
        if job.is_usable:
            grouped.setdefault(job.fingerprint, []).append(job)

    merged = []
    for fingerprint, group in grouped.items():
        best = min(group, key=_rank)
        merged.append(
            MergedJob(
                fingerprint=fingerprint,
                best=best,
                sources=tuple(dict.fromkeys(j.source for j in group)),
                all_urls=tuple(dict.fromkeys(j.url for j in group)),
                locations=tuple(
                    dict.fromkeys(j.location for j in group if j.location.strip())
                ),
            )
        )
    return merged
