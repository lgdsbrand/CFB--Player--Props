"""N4 job: NFL play-by-play into `plays` and `play_player_stats`.

    python -m worker.jobs.nfl_ingest_plays --seasons 2025 --dry-run
    python -m worker.jobs.nfl_ingest_plays --seasons 2024 2025 2026 --current-season 2026

Requires `nfl_ingest_reference` to have run for the same seasons: every play is
joined to a game by nflverse id and every attribution row to a player by
gsis_id, and a season with no games stored is refused rather than written as
nothing.

RUN `build_splits --sport nfl` AFTERWARDS, not before. This job writes the raw
material; the position splits and the opponent-adjusted ratings are built from
it by the sport-agnostic engine, which now takes a sport precisely so the two
leagues do not share a league mean.

FRESHNESS IS PER SEASON, as in `nfl_ingest_stats`: a completed season is
immutable and its file never expires, and the season being played must not be
served from a cache. `--current-season` names which one is live.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.ingest_plays import (
    run_nfl_plays_ingest,
    select_source_rows,
)
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.adapters.nflverse.mapping import IMMUTABLE, LIVE_MAX_AGE
from worker.config import ConfigError, get_settings
from worker.db import (
    check_storage_headroom,
    count_rows,
    pipeline_run,
    record_failed_run,
    resolve_seasons,
    set_rows_written,
)
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "nfl_ingest_plays"

REPORTED_TABLES = ("plays", "play_player_stats")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--seasons", type=int, nargs="+")
    scope.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only, and treat it as live. "
             "What the daily in-season cron passes, so the schedule carries no "
             "hardcoded year -- see nfl_ingest_reference's docstring for why "
             "that matters more than it looks.",
    )
    parser.add_argument(
        "--current-season", type=int,
        help="Which season is being played, and so must not be read from a "
             "stale cache. Omit for a pure historical load; implied by "
             "--current.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve and filter the source, report what would be written, and "
             "stop before any database write.",
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
    # See nfl_ingest_stats: --current already asserts which season is live, so
    # the cron states the year once rather than twice.
    current_season = seasons[0] if args.current else args.current_season

    client = NflverseClient()
    counts = NflReferenceCounts()

    def max_age_for(season: int) -> float | None:
        return LIVE_MAX_AGE if season == current_season else IMMUTABLE

    if args.dry_run:
        try:
            for season in seasons:
                frame = client.fetch(
                    "play_by_play", season, max_age=max_age_for(season)
                )
                kept = select_source_rows(frame)
                log.info(
                    "%d: %d play(s), %d with a skill id (%.1f%% kept), "
                    "%d game(s), %d week(s)",
                    season, frame.height, kept.height,
                    100.0 * kept.height / max(1, frame.height),
                    frame["game_id"].n_unique(), frame["week"].n_unique(),
                )
        except (NflverseError, ValueError) as exc:
            log.error("%s", exc)
            return 3
        log.info("Dry run: sources resolved, stopping before any write.")
        return 0

    # This job is the reason the guard is here: its Sunday load is what put
    # production over its cap on 2026-09-07, and it is by far the largest
    # writer of the three NFL jobs.
    if not check_storage_headroom(JOB_NAME):
        return 4

    before = {t: count_rows(t) for t in REPORTED_TABLES}
    try:
        for season in seasons:
            # ONE `pipeline_run` PER SEASON, not one for the batch. A
            # three-season backfill that fails on the third has still correctly
            # loaded the first two, and the monitor reads the last successful
            # run per job -- a single wrapping run would report the whole
            # backfill as failed and hide that.
            with pipeline_run(
                JOB_NAME, metadata={"season": season, "sport": "nfl"}
            ) as run_id:
                written = run_nfl_plays_ingest(
                    client, season, counts, max_age=max_age_for(season)
                )
                set_rows_written(run_id, sum(written.values()))
    except (NflverseError, ValueError) as exc:
        log.error("%s", exc)
        record_failed_run(JOB_NAME, f"{type(exc).__name__}: {exc}", {"seasons": seasons})
        return 3

    if counts.skipped:
        log.info(
            "skipped rows by reason: %s",
            ", ".join(f"{k}={v:,}" for k, v in sorted(counts.skipped.items())),
        )
    after = {t: count_rows(t) for t in REPORTED_TABLES}
    for table in REPORTED_TABLES:
        log.info(
            "  %-22s %8s  (%+d)",
            table, f"{after[table]:,}", after[table] - before[table],
        )
    log.info("  downloads=%d cache_hits=%d", client.downloads, client.cache_hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
