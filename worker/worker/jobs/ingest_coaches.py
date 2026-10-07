"""Head coaches and their records, for the game page's coach panel.

    python -m worker.jobs.ingest_coaches --current
    python -m worker.jobs.ingest_coaches --seasons 2026
    python -m worker.jobs.ingest_coaches --current --dry-run

COSTS NO ODDS API CREDITS, and ~139 CFBD calls per season: one for the
season's coach list, one per head coach for their seasons. Weekly, chained
LAST onto the Sunday `cfb-props-ingest-week` cron so a coach-data failure can
never hold up the splits that run before it.

ALWAYS LIVE. A coach changes mid-season and a season's record grows weekly,
so the cache is honoured only for `LIVE_MAX_AGE_SECONDS` — a permanent cache
here would succeed every week while serving September's coaches.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.ingest_coaches import estimate_calls, ingest_coaches
from worker.adapters.cfbd.quota import (
    QuotaError,
    fetch_account_status,
    require_capacity,
    warn_on_missing_features,
)
from worker.config import ConfigError, get_settings
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_coaches"
LIVE_MAX_AGE_SECONDS = 900.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only. What the weekly cron passes.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    try:
        seasons = resolve_seasons(args.seasons, current=args.current)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        with CfbdClient() as client:
            status = fetch_account_status(client)
            log.info("CFBD account: %s", status.summary())
            warn_on_missing_features(status)

            estimated = estimate_calls() * len(seasons)
            log.info("Seasons %s: estimated %d API calls", seasons, estimated)
            try:
                require_capacity(status, estimated)
            except QuotaError as exc:
                log.error("%s", exc)
                return 3

            if args.dry_run:
                log.info("Dry run: preflights passed, stopping before ingest.")
                return 0

            for season in seasons:
                with pipeline_run(JOB_NAME, metadata={"season": season}) as run_id:
                    counts = ingest_coaches(
                        client, season, max_age=LIVE_MAX_AGE_SECONDS
                    )
                    set_rows_written(
                        run_id, counts.coaches + counts.seasons + counts.team_coaches
                    )
            log.info(
                "Ingest complete. live API calls: %d  |  %s",
                client.call_count,
                client.cache.stats.summary(),
            )
    except Exception as exc:
        log.error("Ingest failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
