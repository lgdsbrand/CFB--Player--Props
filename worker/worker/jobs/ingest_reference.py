"""Phase 2b job: backfill reference and schedule data.

    python -m worker.jobs.ingest_reference               # seasons from app_config
    python -m worker.jobs.ingest_reference --seasons 2024
    python -m worker.jobs.ingest_reference --dry-run     # preflight only

Seasons come from `app_config.backfill_seasons` so the scope is a row edit
rather than a deploy (CLAUDE.md §9 keeps open decisions in configuration).

The job refuses to start if the account cannot afford the work: a partial
backfill leaves the database in a state no row count honestly describes, and
CFBD may suspend a key that blows through its quota.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.ingest_reference import estimate_calls, run_reference_ingest
from worker.adapters.cfbd.quota import (
    QuotaError,
    fetch_account_status,
    require_capacity,
    warn_on_missing_features,
)
from worker.config import ConfigError, get_settings
from worker.db import count_rows, get_config_value, pipeline_run, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_reference"

# Tables this job populates, reported before and after so the deliverable is a
# real delta rather than an absolute number that says nothing about this run.
REPORTED_TABLES = (
    "conferences",
    "venues",
    "teams",
    "team_seasons",
    "games",
    "players",
    "player_team_seasons",
)


def resolve_seasons(explicit: list[int] | None) -> list[int]:
    if explicit:
        return sorted(explicit)

    configured = get_config_value("backfill_seasons")
    if not configured:
        raise ConfigError(
            "app_config.backfill_seasons is empty and no --seasons given."
        )
    return sorted(int(s) for s in configured)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seasons", type=int, nargs="+",
        help="Override app_config.backfill_seasons.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run the quota preflight and report the plan, then stop.",
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)

    try:
        seasons = resolve_seasons(args.seasons)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    estimated = estimate_calls(seasons)
    log.info("Seasons: %s (estimated %d API calls)", seasons, estimated)

    before = {t: count_rows(t) for t in REPORTED_TABLES}

    try:
        with CfbdClient() as client:
            status = fetch_account_status(client)
            log.info("CFBD account: %s", status.summary())
            warn_on_missing_features(status)

            try:
                require_capacity(status, estimated)
            except QuotaError as exc:
                log.error("%s", exc)
                return 3

            if args.dry_run:
                log.info("Dry run: preflight passed, stopping before ingest.")
                return 0

            with pipeline_run(
                JOB_NAME, metadata={"seasons": seasons, "estimated_calls": estimated}
            ) as run_id:
                counts = run_reference_ingest(client, seasons)
                set_rows_written(run_id, counts.total())

            after = {t: count_rows(t) for t in REPORTED_TABLES}

            width = max(len(t) for t in REPORTED_TABLES)
            lines = [
                f"  {t:<{width}}  {after[t]:>7,}  ({after[t] - before[t]:+,})"
                for t in REPORTED_TABLES
            ]
            log.info(
                "Ingest complete. Row counts (delta vs before):\n%s\n"
                "  live API calls: %d  |  %s",
                "\n".join(lines),
                client.call_count,
                client.cache.stats.summary(),
            )
    except Exception as exc:
        log.error("Ingest failed: %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
