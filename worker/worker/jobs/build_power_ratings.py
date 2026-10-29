"""Power ratings and game score, point in time (client, 2026-10-29).

    python -m worker.jobs.build_power_ratings --current --sport cfb
    python -m worker.jobs.build_power_ratings --current --sport nfl
    python -m worker.jobs.build_power_ratings --seasons 2025 2026 --sport nfl

Rebuilds `team_power_ratings` and `game_power` for each season
(worker/core/power.py): every week N rated on games before week N only.
Reads `games` and `team_seasons`; costs no API calls. Display only.
"""

from __future__ import annotations

import argparse
import sys

from worker.config import ConfigError, get_settings
from worker.core.power import POWER_SETTINGS, build_season
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "build_power_ratings"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--sport", default="cfb", choices=tuple(POWER_SETTINGS))
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
        for season in seasons:
            with pipeline_run(
                JOB_NAME, metadata={"season": season, "sport": args.sport}
            ) as run_id:
                set_rows_written(run_id, build_season(season, args.sport))
    except Exception as exc:
        log.error("Power ratings build failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
