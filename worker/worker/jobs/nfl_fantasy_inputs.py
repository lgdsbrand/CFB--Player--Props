"""Inputs of the client's NFL fantasy game model (migration 0093, phase F1).

    python -m worker.jobs.nfl_fantasy_inputs --current
    python -m worker.jobs.nfl_fantasy_inputs --current --dry-run
    python -m worker.jobs.nfl_fantasy_inputs --seasons 2025 --steps inputs

Three steps, in this order (details in adapters/nflverse/fantasy_model.py):

  inputs       fantasy_team_inputs, every week of the season, rebuilt.
  projections  CBS's projections for each team's next game.
  lineups      each team's five starters for its next game.

`projections` and `lineups` are snapshots of NOW, so they run on the current
season only; `inputs` can rebuild any season. Run after the NFL results
chain, so the day's play-by-play, depth charts and injuries are in.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.cbs.client import CbsClient, CbsError
from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.fantasy_model import (
    build_lineups,
    build_team_inputs,
    capture_projections,
)
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.adapters.nflverse.mapping import LIVE_MAX_AGE, SPORT
from worker.config import ConfigError, get_settings
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "nfl_fantasy_inputs"
STEPS = ("inputs", "projections", "lineups")
SNAPSHOT_STEPS = frozenset({"projections", "lineups"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--current", action="store_true", help="app_config.current_season.")
    scope.add_argument("--seasons", type=int, nargs="+", help="Rebuild these seasons' inputs.")
    parser.add_argument(
        "--steps", nargs="+", choices=STEPS, default=list(STEPS),
        help="Which steps to run (default: all three).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and compute; write nothing.")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    steps = [s for s in STEPS if s in args.steps]
    if args.seasons and SNAPSHOT_STEPS & set(steps):
        log.error("--seasons rebuilds inputs only; projections and lineups are snapshots of now "
                  "(pass --steps inputs, or --current)")
        return 2
    try:
        seasons = resolve_seasons(args.seasons, current=args.current)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    nflverse = NflverseClient()
    cbs = CbsClient()
    counts = NflReferenceCounts()

    def run(season: int) -> int:
        written = 0
        projections = None
        if "inputs" in steps:
            written += len(build_team_inputs(
                nflverse, season, max_age=LIVE_MAX_AGE, dry_run=args.dry_run
            ))
        if "projections" in steps:
            projections = capture_projections(
                cbs, nflverse, season, counts, max_age=LIVE_MAX_AGE, dry_run=args.dry_run
            )
            written += len(projections)
        if "lineups" in steps:
            written += len(build_lineups(
                season, counts, dry_run=args.dry_run,
                # A dry run wrote nothing, so the lineups read what it fetched.
                projections=projections if args.dry_run else None,
            ))
        return written

    try:
        for season in seasons:
            if args.dry_run:
                run(season)
                continue
            metadata = {"season": season, "sport": SPORT, "steps": steps}
            with pipeline_run(JOB_NAME, metadata=metadata) as run_id:
                set_rows_written(run_id, run(season))
    except (NflverseError, CbsError) as exc:
        log.error("%s", exc)
        return 3
    except Exception as exc:
        log.error("Fantasy model inputs failed: %s", exc, exc_info=True)
        return 1
    if counts.skipped:
        log.info(
            "skipped rows by reason: %s",
            ", ".join(f"{k}={v:,}" for k, v in sorted(counts.skipped.items())),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
