#!/usr/bin/env python3
"""Entry point. One run = wake up, gather, dedupe, score, notify, save.

Ordering rule inherited from the sibling projects: notifications fire BEFORE
the state file is written and committed, so a failed git push costs a log row
rather than a missed alert.

Build status: sources, merge, dedupe, scoring and notifications are live.
Still to come: alert-inbox IMAP ingestion and target-company ATS feeds.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

from jobagent import config as config_module
from jobagent import scoring, state, steering
from jobagent.dedupe import SeenStore, split_new
from jobagent.models import ScoredJob, merge
from jobagent.notify import mail, push
from jobagent.sources import adzuna, inbox, reed

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yml"
SEEN_PATH = ROOT / "seen.json"
STATE_PATH = ROOT / "state.json"

log = logging.getLogger("jobagent")


def feedback_url() -> str:
    """Deep link to the GitHub mobile edit view for feedback.md.

    The steering loop only gets used if it's one tap from the notification,
    so this is load-bearing rather than a nicety.
    """
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        return ""
    return f"https://github.com/{repo}/edit/main/feedback.md"


def gather(cfg) -> list:
    """Pull from every enabled source. A dead source never kills the run."""
    found = []
    if cfg.adzuna_enabled:
        found += adzuna.fetch(
            os.environ.get("ADZUNA_APP_ID", ""),
            os.environ.get("ADZUNA_APP_KEY", ""),
            cfg,
        )
    if cfg.reed_enabled:
        found += reed.fetch(os.environ.get("REED_API_KEY", ""), cfg)
    if cfg.inbox_enabled:
        found += inbox.fetch(
            os.environ.get("GMAIL_ADDRESS", ""),
            os.environ.get("GMAIL_APP_PASSWORD", ""),
            cfg,
        )
    # TODO(build step 6): target-company ATS feeds
    return found


def _notify(cfg, scored: list[ScoredJob], run_state: state.RunState, hour: int) -> None:
    """Send push and digest. Neither channel can break the other."""
    link = feedback_url()
    today = date.today()

    if cfg.push_enabled:
        quiet = cfg.quiet_hours.covers(hour)
        allowance = run_state.pushes_left(cfg.push_max_per_day, today=today)
        chosen = push.choose(
            scored,
            threshold=cfg.push_threshold,
            allowance=allowance,
            quiet=quiet,
        )
        if chosen:
            if push.send(
                os.environ.get("NTFY_TOPIC", ""),
                chosen,
                vague=cfg.push_vague_wording,
                feedback_url=link,
            ):
                run_state.record_pushes(len(chosen), today=today)
        elif quiet:
            log.info("[ntfy] quiet hours; anything strong goes in the next digest")

    if not cfg.email_enabled or not cfg.is_digest_hour(hour):
        return
    if run_state.digest_already_sent_today(today=today):
        # cron-job.org is primary and GitHub's schedule is a backup, so two
        # triggers landing in one window is expected, not exceptional.
        log.info("[email] a digest already went today; not sending another")
        return

    for_digest = sorted(scored, key=lambda job: job.score, reverse=True)[
        : cfg.email_max_roles
    ]
    if not for_digest and not cfg.email_send_when_empty:
        log.info("[email] nothing to send and send_when_empty is off")
        return

    body = mail.render(for_digest, feedback_url=link) if for_digest else mail.render_empty()
    subject = (
        f"{len(for_digest)} job{'s' if len(for_digest) != 1 else ''} worth a look"
        if for_digest
        else "Job agent — nothing new today"
    )
    if mail.send(
        address=os.environ.get("GMAIL_ADDRESS", ""),
        app_password=os.environ.get("GMAIL_APP_PASSWORD", ""),
        to=os.environ.get("DIGEST_TO", ""),
        subject=subject,
        body_html=body,
    ):
        run_state.record_digest(today=today)


def run(args) -> int:
    try:
        cfg = config_module.load(CONFIG_PATH)
    except config_module.ConfigError as exc:
        # Her mistake, not a crash. Report it in words she can act on.
        log.error("There's a problem with config.yml:\n\n%s", exc)
        _tell_her_it_broke(f"config.yml has a problem:\n\n{exc}")
        return 2

    if not cfg.enabled and not args.force:
        log.info("Master switch is off in config.yml (enabled: false). Nothing to do.")
        return 0

    hour = datetime.now().hour
    if hour not in cfg.run_hours and not args.force:
        log.info(
            "Not a scheduled hour (now %02d:00, runs at %s). Nothing to do.",
            hour,
            ", ".join(f"{h:02d}:00" for h in cfg.run_hours),
        )
        return 0

    if not cfg.search_terms:
        log.warning(
            "No search_terms in config.yml, so the job sites have nothing to "
            "search for. Add a few under sources.search_terms."
        )

    guidance = steering.load(ROOT)
    if not guidance.has_any_guidance:
        log.warning(
            "profile.md and feedback.md are both still blank, so scoring has "
            "very little to go on. Expect the marks to be rough until they're "
            "filled in."
        )

    found = gather(cfg)
    merged = merge(found)
    log.info("%d results from sources -> %d distinct roles", len(found), len(merged))

    seen = SeenStore.load(SEEN_PATH)
    fresh, _known = split_new(merged, seen)
    log.info("%d new, %d already seen", len(fresh), len(merged) - len(fresh))

    verdicts = scoring.score(os.environ.get("GEMINI_API_KEY", ""), fresh, guidance)
    by_fingerprint = {job.fingerprint: job for job in fresh}

    scored: list[ScoredJob] = []
    for verdict in verdicts:
        job = by_fingerprint.get(verdict.fingerprint)
        if job is None:
            continue
        if verdict.score < cfg.min_score_to_keep:
            # Still recorded, so it's never scored or considered again.
            if not args.dry_run:
                seen.remember(verdict.fingerprint, verdict.score, verdict.reason)
            continue
        scored.append(ScoredJob(job=job, score=verdict.score, reason=verdict.reason))

    scored.sort(key=lambda job: job.score, reverse=True)
    log.info("%d roles cleared the cut-off of %d", len(scored), cfg.min_score_to_keep)

    if args.dry_run:
        for job in scored:
            log.info(
                "  %d/10 [%s] %s — %s (%s)",
                job.score,
                "+".join(job.sources),
                job.title,
                job.company,
                job.url,
            )
        log.info("Dry run: nothing sent, nothing saved.")
        return 0

    run_state = state.RunState.load(STATE_PATH)
    # Side effects first, state second -- a failed push must not cost the
    # digest, and a failed git push must not cost either.
    _notify(cfg, scored, run_state, hour)

    for job in scored:
        seen.remember(job.fingerprint, job.score, job.reason)
    removed = seen.prune(cfg.seen_retention_days)
    if removed:
        log.info("pruned %d entries older than %d days", removed, cfg.seen_retention_days)

    seen.save(SEEN_PATH)
    run_state.save(STATE_PATH)
    return 0


def _tell_her_it_broke(reason: str) -> None:
    """Best-effort failure email. Never raises -- it's the last line of defence."""
    try:
        mail.send_failure_notice(
            address=os.environ.get("GMAIL_ADDRESS", ""),
            app_password=os.environ.get("GMAIL_APP_PASSWORD", ""),
            to=os.environ.get("DIGEST_TO", ""),
            reason=reason,
        )
    except Exception:  # noqa: BLE001 -- nothing useful left to do if this fails
        log.exception("could not send the failure email either")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the job agent once.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gather, score and report, but send nothing and save no state.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even outside the configured hours or with the master switch off.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    try:
        return run(args)
    except Exception:  # noqa: BLE001 -- a crash must still reach her
        log.exception("the run failed")
        _tell_her_it_broke(traceback.format_exc(limit=3))
        return 1


if __name__ == "__main__":
    sys.exit(main())
