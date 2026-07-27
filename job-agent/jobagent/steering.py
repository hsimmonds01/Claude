"""Reads the three plain-English files she controls.

`feedback.md` has two sections and they behave differently on purpose:

- **Standing rules** are permanent and all of them are sent every run. She
  edits and deletes these freely.
- **Recent reactions** are append-only and only the newest few are sent, so
  old opinions fade out by themselves and she can contradict herself later
  without having to go back and tidy up. Later wins.

A single append-only list rots: after a few months "more remote please" and
"actually office is fine" both sit in the prompt cancelling each other out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# How many of the newest reactions reach the prompt. Enough to capture a
# genuine change of mind, few enough that a bad week doesn't define her taste
# forever.
RECENT_REACTIONS_LIMIT = 15

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*?)\s*$")


@dataclass(frozen=True)
class Steering:
    cv: str
    profile: str
    standing_rules: tuple[str, ...]
    recent_reactions: tuple[str, ...]

    @property
    def has_any_guidance(self) -> bool:
        """False when she hasn't filled anything in yet.

        Worth knowing: scoring against an empty profile produces confident
        nonsense, so the caller should say so rather than pretend.
        """
        return bool(self.standing_rules or self.recent_reactions or self.profile)


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _strip_template_scaffolding(text: str) -> str:
    """Remove the commented-out examples shipped in the blank templates.

    Without this, the examples ("Never show me anything from an agency") would
    be read as real instructions on day one and quietly steer the scoring
    before she has said anything at all.
    """
    return _HTML_COMMENT.sub("", text)


def _has_real_content(text: str) -> bool:
    """True when a template has actually been filled in.

    An unfilled profile.md is all headings and blank prompts, but it isn't an
    empty string, so a plain truthiness test reported it as real guidance --
    and the "you haven't filled this in yet" warning never fired. Count only
    lines that carry an answer: not headings, not blockquote prompts, not
    empty bullets, not horizontal rules.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("---"):
            continue
        if stripped in {">", "-", "*", "|"}:
            continue
        # A bare "> " prompt or an empty bullet is scaffolding, not an answer.
        if stripped.startswith(">") and not stripped.lstrip("> ").strip():
            continue
        if _BULLET.match(line) and not _BULLET.match(line).group(1).strip():
            continue
        return True
    return False


def _bullets_under(text: str, heading_contains: str) -> list[str]:
    """Bullet lines beneath the first heading matching `heading_contains`."""
    wanted = heading_contains.casefold()
    collecting = False
    found: list[str] = []

    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            # A new heading ends the section we were collecting.
            collecting = wanted in heading.group(1).casefold()
            continue
        if not collecting:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            item = bullet.group(1).strip()
            if item:
                found.append(item)
    return found


def parse_feedback(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(standing rules, recent reactions) from feedback.md."""
    clean = _strip_template_scaffolding(text)
    standing = _bullets_under(clean, "standing rules")
    reactions = _bullets_under(clean, "recent reactions")

    # If she ignores the sections and just types bullets at the top of the
    # file, treat them as reactions rather than losing them. Being lenient
    # here matters more than being tidy -- silently dropping her feedback
    # would make the steering loop look broken.
    if not standing and not reactions:
        reactions = []
        for line in clean.splitlines():
            match = _BULLET.match(line)
            if match and match.group(1).strip():
                reactions.append(match.group(1).strip())

    return tuple(standing), tuple(reactions[-RECENT_REACTIONS_LIMIT:])


def load(root: str | Path) -> Steering:
    base = Path(root)
    standing, reactions = parse_feedback(_read(base / "feedback.md"))

    # An untouched profile.md is headings and blank prompts. Sending that to
    # the model is worse than sending nothing: it reads as a filled-in profile
    # that happens to want nothing in particular, and the model invents
    # preferences to fill the gap.
    profile = _strip_template_scaffolding(_read(base / "profile.md")).strip()
    if not _has_real_content(profile):
        profile = ""

    return Steering(
        cv=_strip_template_scaffolding(_read(base / "cv.md")).strip(),
        profile=profile,
        standing_rules=standing,
        recent_reactions=reactions,
    )
