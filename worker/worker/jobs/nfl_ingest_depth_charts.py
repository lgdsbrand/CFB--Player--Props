"""NFL depth charts for each team's next game (migration 0086).

    python -m worker.jobs.nfl_ingest_depth_charts --current
    python -m worker.jobs.nfl_ingest_depth_charts --current --dry-run

Reads nflverse's newest depth-chart snapshot (ESPN, refreshed twice a day)
and writes it as every team's depth chart for its next game's week,
replacing that week until the game kicks off. Past weeks are never
rewritten, so a game page shows the depth chart going into that game.
Details in adapters/nflverse/ingest_depth_charts.py.

CURRENT SEASON ONLY, by design: the source is a snapshot of now, so there is
no past week this could write honestly. No API calls; one ~3 MB download.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.ingest_depth_charts import run_nfl_depth_chart_ingest
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.adapters.nflverse.mapping import LIVE_MAX_AGE, SPORT
from worker.config import ConfigError, get_settings
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "nfl_ingest_depth_charts"


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

    client = NflverseClient()
    counts = NflReferenceCounts()
    try:
        if args.dry_run:
            run_nfl_depth_chart_ingest(
                client, season, counts, max_age=LIVE_MAX_AGE, dry_run=True
            )
        else:
            with pipeline_run(JOB_NAME, metadata={"season": season, "sport": SPORT}) as run_id:
                written = run_nfl_depth_chart_ingest(
                    client, season, counts, max_age=LIVE_MAX_AGE
                )
                set_rows_written(run_id, written)
    except NflverseError as exc:
        log.error("%s", exc)
        return 3
    except Exception as exc:
        log.error("Depth chart ingest failed: %s", exc, exc_info=True)
        return 1
    if counts.skipped:
        log.info(
            "skipped rows by reason: %s",
            ", ".join(f"{k}={v:,}" for k, v in sorted(counts.skipped.items())),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
