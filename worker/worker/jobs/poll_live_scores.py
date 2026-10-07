"""College live scores, every two minutes while a game is on (migration 0088).

    python -m worker.jobs.poll_live_scores
    python -m worker.jobs.poll_live_scores --dry-run   # call and match, write nothing

Scheduled every two minutes all week. Each run first asks OUR database whether
any college game is in its window (5 minutes before kickoff to 5 hours after,
not yet final). If none is, it exits at once: no CFBD call and no
`pipeline_runs` row, because 700 idle rows a day would bury the real ones.
When one is, it makes ONE `/scoreboard` call for the whole FBS week and
overwrites those games' `live_scores` rows. Details in
adapters/cfbd/live_scores.py.

COST: about 360 CFBD calls on a Saturday and ~100 on a weeknight with games,
~3,500 a month against a 30,000 allowance. No odds credits. No account
preflight: it would be a second call on every poll.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.live_scores import games_in_window, poll
from worker.config import ConfigError, get_settings
from worker.db import pipeline_run, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "poll_live_scores"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    now = datetime.now(UTC)
    try:
        # The cheap question first, outside any pipeline_run: most runs end here.
        if not games_in_window(now):
            log.info("No college game in its window; nothing to poll.")
            return 0
        with CfbdClient() as client:
            if args.dry_run:
                poll(client, now, dry_run=True)
                return 0
            with pipeline_run(JOB_NAME, metadata={"sport": "cfb"}) as run_id:
                written = poll(client, now) or 0
                set_rows_written(run_id, written)
    except Exception as exc:
        log.error("Live score poll failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
