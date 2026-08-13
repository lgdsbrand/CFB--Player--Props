"""Forecast conditions for upcoming games (CLAUDE.md §4, §7).

    python -m worker.jobs.ingest_weather --current
    python -m worker.jobs.ingest_weather --seasons 2026 --weeks 1 2
    python -m worker.jobs.ingest_weather --current --dry-run

WHY THIS EXISTS SEPARATELY FROM `ingest_ratings`, WHICH ALSO WRITES WEATHER.
That one reads CFBD's `/games/weather`, which serves OBSERVED conditions and so
returns nothing at all for a game that has not been played — 0 rows for 2026
weeks 1 and 2 when measured uncached on 2026-08-13. It is the right source for
history and cannot answer the only question the board asks, which is what the
weather will be on Saturday.

Costs nothing: the Open-Meteo forecast endpoint is unauthenticated and free, and
this makes roughly one call per venue per day of the slate.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.open_meteo.client import OpenMeteoClient
from worker.adapters.open_meteo.ingest_weather import run_weather_ingest
from worker.config import ConfigError, get_settings
from worker.db import (
    count_rows,
    pipeline_run,
    record_failed_run,
    resolve_seasons,
    set_rows_written,
)
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_weather"
REPORTED_TABLES = ("game_weather",)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only. What the daily cron "
             "passes; without it the job falls through to backfill_seasons, "
             "which scopes the historical backfill and lags a season behind.",
    )
    parser.add_argument(
        "--weeks", type=int, nargs="+",
        help="Limit to these weeks on the season axis. Default: every upcoming "
             "game inside the forecast horizon.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        record_failed_run(JOB_NAME, f"ConfigError: {exc}")
        return 2

    configure_logging(settings.log_level)

    try:
        seasons = resolve_seasons(args.seasons, current=args.current)
    except ConfigError as exc:
        log.error("%s", exc)
        record_failed_run(JOB_NAME, f"ConfigError: {exc}")
        return 2

    before = {t: count_rows(t) for t in REPORTED_TABLES}
    client = OpenMeteoClient()
    run_opened = False

    try:
        for season in seasons:
            run_opened = True
            with pipeline_run(JOB_NAME, metadata={"season": season}) as run_id:
                counts = run_weather_ingest(
                    client, season, weeks=args.weeks, dry_run=args.dry_run
                )
                set_rows_written(run_id, counts.written)

        after = {t: count_rows(t) for t in REPORTED_TABLES}
        log.info(
            "Complete. game_weather %s (%+d) | %d API call(s)",
            f"{after['game_weather']:,}",
            after["game_weather"] - before["game_weather"],
            client.call_count,
        )
    except Exception as exc:
        log.error("Weather ingest failed: %s", exc, exc_info=True)
        if not run_opened:
            record_failed_run(
                JOB_NAME,
                f"{type(exc).__name__}: {exc}",
                {"seasons": seasons, "phase": "preflight"},
            )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
