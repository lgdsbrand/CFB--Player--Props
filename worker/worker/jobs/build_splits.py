"""Phase 2e job: build position splits and opponent-adjusted defensive ratings.

    python -m worker.jobs.build_splits
    python -m worker.jobs.build_splits --seasons 2024

Reads only from the database — no API calls, so this can be re-run freely while
the attribution logic is refined. That is the whole reason the response cache
exists.
"""

from __future__ import annotations

import argparse
import sys

from worker.config import ConfigError, get_settings
from worker.core.splits import run_split_engine
from worker.db import count_rows, get_config_value, pipeline_run, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "build_splits"
REPORTED_TABLES = ("defense_position_game_splits", "defense_position_ratings")


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

    # The goal-line threshold is runtime config, not a constant: it defines what
    # counts as a scoring opportunity for the anytime-TD model (CLAUDE.md §6).
    goal_line = int(get_config_value("goal_line_yards_to_goal") or 10)
    log.info("Seasons %s, goal-line threshold %d yards", seasons, goal_line)

    before = {t: count_rows(t) for t in REPORTED_TABLES}

    try:
        for season in seasons:
            with pipeline_run(JOB_NAME, metadata={"season": season}) as run_id:
                counts = run_split_engine(season, goal_line)
                set_rows_written(run_id, sum(counts.values()))
    except Exception as exc:
        log.error("Split build failed: %s", exc, exc_info=True)
        return 1

    after = {t: count_rows(t) for t in REPORTED_TABLES}
    width = max(len(t) for t in REPORTED_TABLES)
    log.info(
        "Complete. Row counts (delta):\n%s",
        "\n".join(
            f"  {t:<{width}}  {after[t]:>8,}  ({after[t] - before[t]:+,})"
            for t in REPORTED_TABLES
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
