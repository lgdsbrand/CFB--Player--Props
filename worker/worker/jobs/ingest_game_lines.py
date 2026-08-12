"""Game spreads, totals and moneylines from CFBD.

    python -m worker.jobs.ingest_game_lines
    python -m worker.jobs.ingest_game_lines --seasons 2026
    python -m worker.jobs.ingest_game_lines --seasons 2026 --weeks 1 2 --live
    python -m worker.jobs.ingest_game_lines --dry-run

COSTS NO ODDS API CREDITS. This is CFBD, on the tier already paid for. The
metered provider is the one serving PLAYER props; nothing here touches that
quota.

`--live` re-fetches instead of serving the permanent cache. Completed weeks are
immutable and cached forever, but the CURRENT week's spread moves all week, so
the in-week refresh has to be able to say "ask again". Without it this job looks
like it succeeded and quietly writes last Tuesday's number: the same
absence-of-evidence shape as the CFBD probe that reported a stale zero for the
2026 rosters.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.ingest_lines import estimate_calls, ingest_game_lines
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

JOB_NAME = "ingest_game_lines"
REPORTED_TABLES = ("game_lines",)

# How stale an in-week response may be. Fifteen minutes is well under the job's
# own cadence, so `--live` always reaches the network in normal operation while
# still collapsing a burst of manual re-runs into one call.
LIVE_MAX_AGE_SECONDS = 900.0


def resolve_seasons(explicit: list[int] | None) -> list[int]:
    if explicit:
        return sorted(explicit)
    configured = get_config_value("backfill_seasons")
    if not configured:
        raise ConfigError("app_config.backfill_seasons is empty and no --seasons given.")
    return sorted(int(s) for s in configured)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument(
        "--weeks",
        type=int,
        nargs="+",
        help="Limit to these weeks on the season axis. Default: every week with games.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Re-fetch rather than serving the permanent cache. Use in-week.",
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
        seasons = resolve_seasons(args.seasons)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    before = {t: count_rows(t) for t in REPORTED_TABLES}
    max_age = LIVE_MAX_AGE_SECONDS if args.live else None

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
                with pipeline_run(
                    JOB_NAME, metadata={"season": season, "live": args.live}
                ) as run_id:
                    counts = ingest_game_lines(
                        client, season, max_age=max_age, weeks=args.weeks
                    )
                    set_rows_written(run_id, counts.rows)

            after = {t: count_rows(t) for t in REPORTED_TABLES}
            log.info(
                "Ingest complete. game_lines: %d (%+d)  |  live API calls: %d  |  %s",
                after["game_lines"],
                after["game_lines"] - before["game_lines"],
                client.call_count,
                client.cache.stats.summary(),
            )
    except Exception as exc:
        log.error("Ingest failed: %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
