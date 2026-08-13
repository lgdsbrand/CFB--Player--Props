"""Phase 2d job: team rating snapshots and weather.

    python -m worker.jobs.ingest_ratings
    python -m worker.jobs.ingest_ratings --seasons 2024
    python -m worker.jobs.ingest_ratings --dry-run

Cheap compared to 2c — roughly 20 calls per season — because point-in-time Elo
is read from already-cached /games responses rather than fetched.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.ingest_ratings import estimate_calls, run_ratings_ingest
from worker.adapters.cfbd.quota import (
    QuotaError,
    fetch_account_status,
    require_capacity,
    warn_on_missing_features,
)
from worker.config import ConfigError, get_settings
from worker.db import count_rows, pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_ratings"
REPORTED_TABLES = ("team_rating_snapshots", "game_weather")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only. What the weekly in-season "
             "cron passes; without it the job falls through to backfill_seasons, "
             "which scopes the historical backfill and lags a season behind.",
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

    before = {t: count_rows(t) for t in REPORTED_TABLES}

    try:
        with CfbdClient() as client:
            status = fetch_account_status(client)
            log.info("CFBD account: %s", status.summary())
            warn_on_missing_features(status)

            estimated = sum(estimate_calls(s) for s in seasons)
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
                    counts = run_ratings_ingest(client, season)
                    set_rows_written(run_id, counts.total())

            after = {t: count_rows(t) for t in REPORTED_TABLES}
            width = max(len(t) for t in REPORTED_TABLES)
            lines = [
                f"  {t:<{width}}  {after[t]:>8,}  ({after[t] - before[t]:+,})"
                for t in REPORTED_TABLES
            ]
            log.info(
                "Ingest complete. Row counts (delta):\n%s\n"
                "  live API calls: %d  |  %s",
                "\n".join(lines), client.call_count, client.cache.stats.summary(),
            )
    except Exception as exc:
        log.error("Ingest failed: %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
