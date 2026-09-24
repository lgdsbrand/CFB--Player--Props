"""Point-in-time team strength for the game model (CLAUDE.md §11, G2).

    python -m worker.jobs.build_team_strength --current
    python -m worker.jobs.build_team_strength --seasons 2022 2023 2024 2025

Rebuilds `team_strength_ratings` for each season: every week N fitted on
games before week N only. Reads `plays` and `games`; costs no API calls.
"""

from __future__ import annotations

import argparse
import sys

from worker.config import ConfigError, get_settings
from worker.core.team_strength import build_season
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "build_team_strength"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--sport", default="cfb", choices=("cfb",))
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
        log.error("Team strength build failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
