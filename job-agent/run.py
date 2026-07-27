#!/usr/bin/env python3
"""Entry point. One run = wake up, gather, dedupe, score, notify, save.

Ordering rule inherited from the sibling projects in the build repo:
notifications fire BEFORE the state file is committed, so a failed git push
costs a log row rather than a missed alert.

Build status: sources + merge + dedupe are live. Scoring and notifications are
stubbed and clearly marked -- `--dry-run` exercises everything that exists.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from jobagent import config as config_module
from jobagent.dedupe import SeenStore, split_new
from jobagent.models import merge
from jobagent.sources import adzuna, reed

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yml"
SEEN_PATH = ROOT / "seen.json"

log = logging.getLogger("jobagent")


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
    # TODO(build step 5): alert-inbox IMAP ingestion
    # TODO(build step 6): target-company ATS feeds
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the job agent once.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gather and report, but send nothing and save no state.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even outside the configured hours.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    try:
        cfg = config_module.load(CONFIG_PATH)
    except config_module.ConfigError as exc:
        # Her mistake, not a crash -- print it plainly and stop. The failure
        # email (build step 4) will carry this same text.
        log.error("There's a problem with config.yml:\n\n%s", exc)
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

    found = gather(cfg)
    merged = merge(found)
    log.info("%d results from sources -> %d distinct roles", len(found), len(merged))

    store = SeenStore.load(SEEN_PATH)
    fresh, known = split_new(merged, store)
    log.info("%d new, %d already seen", len(fresh), len(known))

    if args.dry_run:
        for job in fresh:
            sources = "+".join(job.sources)
            log.info("  [%s] %s — %s (%s)", sources, job.title, job.company, job.url)
        log.info("Dry run: nothing sent, nothing saved.")
        return 0

    # TODO(build step 3): score `fresh` with Gemini, freeze via store.remember()
    # TODO(build step 4): ntfy push + email digest, then prune and save state
    log.warning("Scoring and notifications are not built yet. Use --dry-run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
