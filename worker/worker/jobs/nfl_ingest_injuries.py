"""NFL injury designations for each team's next game (migration 0086).

    python -m worker.jobs.nfl_ingest_injuries --current
    python -m worker.jobs.nfl_ingest_injuries --current --dry-run

Reads Sleeper's public player dump (once a day, as Sleeper asks; the client
reuses the day's copy on a re-run) and writes every designated player as his
team's injury report for its next game's week, replacing that week until the
game kicks off. Past weeks are never rewritten. Details in
adapters/sleeper/ingest_injuries.py.

CURRENT SEASON ONLY, by design: the source is a snapshot of now. One Sleeper
call (~15 MB) and the nflverse roster for the id bridge; no keys, no credits.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.adapters.nflverse.mapping import LIVE_MAX_AGE, SPORT
from worker.adapters.sleeper.client import SleeperClient, SleeperError
from worker.adapters.sleeper.ingest_injuries import run_nfl_injury_ingest
from worker.config import ConfigError, get_settings
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "nfl_ingest_injuries"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current", action="store_true", required=True,
        help="Work on app_config.current_season. Required: there is no other season to write.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve and count; write nothing.")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    try:
        season = resolve_seasons(None, current=True)[0]
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    sleeper, nflverse = SleeperClient(), NflverseClient()
    counts = NflReferenceCounts()
    try:
        if args.dry_run:
            run_nfl_injury_ingest(
                sleeper, nflverse, season, counts, roster_max_age=LIVE_MAX_AGE, dry_run=True
            )
        else:
            with pipeline_run(JOB_NAME, metadata={"season": season, "sport": SPORT}) as run_id:
                written = run_nfl_injury_ingest(
                    sleeper, nflverse, season, counts, roster_max_age=LIVE_MAX_AGE
                )
                set_rows_written(run_id, written)
    except (SleeperError, NflverseError) as exc:
        log.error("%s", exc)
        return 3
    except Exception as exc:
        log.error("Injury ingest failed: %s", exc, exc_info=True)
        return 1
    if counts.skipped:
        log.info(
            "skipped rows by reason: %s",
            ", ".join(f"{k}={v:,}" for k, v in sorted(counts.skipped.items())),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
