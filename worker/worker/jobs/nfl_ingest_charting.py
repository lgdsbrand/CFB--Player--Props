"""NFL defensive tendencies: FTN charting -> per-game splits -> point-in-time.

    python -m worker.jobs.nfl_ingest_charting --current
    python -m worker.jobs.nfl_ingest_charting --seasons 2023 2024 2025
    python -m worker.jobs.nfl_ingest_charting --seasons 2026 --dry-run

Two steps in one job, because the second is worthless without the first and
neither is useful alone: the ingest writes what each defense did in each game,
and the ratings pass turns that into "what this defense had done BEFORE week N"
for every N. `build_splits` splits the same work across two jobs only because
its ratings pass is an expensive fit; this one is a group-by.

RUN IT AFTER `nfl_ingest_plays`. The charting file names no team on any row --
the defense comes from our own `plays`, joined on the nflverse play key -- so
without plays there is nothing to attach charting to and the job says so rather
than writing zero rows.

NFL ONLY, and not by a flag: there is no college charting source at any price.

WEEK 2 IS THE FIRST WEEK THIS SAYS ANYTHING, and the lag is worth knowing when
reading a Sunday board. FTN publishes two to three days after a week is played,
so the figures a week-2 board shows are week 1 alone -- about 35 dropbacks per
defense, which clears the floor in `core/charting.py` but is one game. The
sample travels with the number on every surface for that reason.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.ingest_charting import run_nfl_charting_ingest
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.adapters.nflverse.mapping import IMMUTABLE, LIVE_MAX_AGE, SPORT
from worker.config import ConfigError, get_settings
from worker.core.charting import compute_ratings, season_summary
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "nfl_ingest_charting"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only. What the daily cron passes.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve the source and report what it holds, writing nothing.",
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
        seasons = resolve_seasons(args.seasons, current=args.current)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    current_season = seasons[0] if args.current else None
    client = NflverseClient()
    counts = NflReferenceCounts()

    def max_age_for(season: int) -> float | None:
        return LIVE_MAX_AGE if season == current_season else IMMUTABLE

    if args.dry_run:
        try:
            for season in seasons:
                frame = client.fetch(
                    "ftn_charting", season, max_age=max_age_for(season)
                )
                log.info(
                    "%d: %d charted play(s), %d week(s), %d game(s)",
                    season, frame.height, frame["week"].n_unique(),
                    frame["nflverse_game_id"].n_unique(),
                )
        except NflverseError as exc:
            log.error("%s", exc)
            return 3
        log.info("Dry run: source resolved, stopping before any write.")
        return 0

    try:
        for season in seasons:
            with pipeline_run(
                JOB_NAME, metadata={"season": season, "sport": SPORT}
            ) as run_id:
                written = run_nfl_charting_ingest(
                    client, season, counts, max_age=max_age_for(season)
                )
                rated = compute_ratings(season, SPORT)
                set_rows_written(run_id, written + rated)
            log.info(
                "%d: %d game row(s), %d rating row(s)\n%s",
                season, written, rated,
                _render_summary(season_summary(season, SPORT)),
            )
        if counts.skipped:
            log.info(
                "skipped rows by reason: %s",
                ", ".join(f"{k}={v:,}" for k, v in sorted(counts.skipped.items())),
            )
    except Exception as exc:
        log.error("Charting ingest failed: %s", exc, exc_info=True)
        return 1
    return 0


def _render_summary(summary: dict[str, Any]) -> str:
    if not summary:
        return "  (no ratings written)"
    week = summary.get("as_of_week")
    rated = summary.get("rated") or 0
    defenses = summary.get("defenses") or 0

    def pct(key: str) -> str:
        value = summary.get(key)
        return "—" if value is None else f"{float(value):.1%}"

    return (
        f"  entering week {week}: {rated} of {defenses} defense(s) rated\n"
        f"  blitz rate   min {pct('min_blitz')}  mean {pct('mean_blitz')}  "
        f"max {pct('max_blitz')}\n"
        f"  heavy box    mean {pct('mean_heavy_box')}  (rate only, no rank)"
    )


if __name__ == "__main__":
    sys.exit(main())
