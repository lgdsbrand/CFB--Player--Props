"""N3 job: NFL box scores into `player_game_stats`. N5a added snap counts.

    python -m worker.jobs.nfl_ingest_stats --seasons 2023 2024 2025 --dry-run
    python -m worker.jobs.nfl_ingest_stats --seasons 2023 2024 2025 2026
    python -m worker.jobs.nfl_ingest_stats --seasons 2023 2024 --snaps-only

Requires `nfl_ingest_reference` to have run for the same seasons first: every
stat row is joined to a game by nflverse id and to a player by gsis_id, and a
season with no games stored is refused rather than silently written as nothing.

FRESHNESS IS PER SEASON, NOT A FLAG. A completed season is immutable and its
file never expires; the season being played must not be served from a cache.
`--current-season` names which one is live, so the caller states it rather than
the job guessing from the calendar -- the same reasoning as `AsOf.sport` and
`NflverseClient.fetch(max_age=...)`, both of which refuse a default that would
be wrong half the time.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.adapters.nflverse.ingest_snaps import run_nfl_snaps_ingest
from worker.adapters.nflverse.ingest_stats import run_nfl_stats_ingest
from worker.adapters.nflverse.mapping import IMMUTABLE, LIVE_MAX_AGE
from worker.config import ConfigError, get_settings
from worker.db import (
    count_rows,
    fetch_one,
    pipeline_run,
    record_failed_run,
    set_rows_written,
)
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "nfl_ingest_stats"

REPORTED_TABLES = ("player_game_stats",)


def _require_box_scores(season: int) -> None:
    """Refuse a `--snaps-only` season whose box scores are not loaded.

    Snaps UPDATE `player_game_stats`; with no rows to match, the pass writes
    nothing and reports zero, which is indistinguishable from a season nflverse
    has no snap data for. The normal path cannot hit this because it loads the
    box scores itself, so the check belongs to the flag that skips that step.
    """
    row = fetch_one(
        "select count(*) as n from player_game_stats s "
        "join games g on g.id = s.game_id "
        "where g.sport = 'nfl' and g.season = %s",
        (season,),
    )
    if not row or not row["n"]:
        raise ValueError(
            f"--snaps-only: no NFL box scores stored for {season}, so the snap "
            f"pass would match nothing and report a misleading zero. Run "
            f"without --snaps-only to load them first."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument(
        "--current-season", type=int,
        help="Which season is being played, and so must not be read from a "
             "stale cache. Omit for a pure historical load.",
    )
    snaps = parser.add_mutually_exclusive_group()
    snaps.add_argument(
        "--skip-snaps", action="store_true",
        help="Load box scores only. Snap counts are a SECOND nflverse asset "
             "and a second pass over the same table; this skips it for a "
             "quick reload when the box scores are what changed.",
    )
    snaps.add_argument(
        "--snaps-only", action="store_true",
        help="Load snap counts only, over box scores already stored. The box "
             "score upsert rewrites every row it touches whether or not the "
             "values changed, so re-running it to reach the snap pass costs a "
             "dead tuple per row for nothing -- which is worth avoiding on a "
             "database near its storage cap. Refuses a season whose box "
             "scores are absent rather than reporting the clean zero that "
             "such a season would otherwise produce.",
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

    seasons = sorted(set(args.seasons))
    client = NflverseClient()
    counts = NflReferenceCounts()

    def max_age_for(season: int) -> float | None:
        return LIVE_MAX_AGE if season == args.current_season else IMMUTABLE

    if args.dry_run:
        try:
            for season in seasons:
                if not args.snaps_only:
                    frame = client.fetch(
                        "weekly_stats", season, max_age=max_age_for(season)
                    )
                    log.info(
                        "%d: %d stat row(s), %d week(s), %d player(s)",
                        season, frame.height, frame["week"].n_unique(),
                        frame["player_id"].n_unique(),
                    )
                if not args.skip_snaps:
                    snaps = client.fetch(
                        "snap_counts", season, max_age=max_age_for(season)
                    )
                    log.info(
                        "%d: %d snap row(s), %d player(s)",
                        season, snaps.height,
                        snaps["pfr_player_id"].n_unique(),
                    )
        except NflverseError as exc:
            log.error("%s", exc)
            return 3
        log.info("Dry run: sources resolved, stopping before any write.")
        return 0

    before = {t: count_rows(t) for t in REPORTED_TABLES}
    try:
        with pipeline_run(JOB_NAME, metadata={"seasons": seasons}) as run_id:
            total = 0
            for season in seasons:
                # Box scores first, always. Snaps UPDATE rows rather than
                # insert them, so a snap pass over a season whose box scores
                # are not loaded yet resolves nothing and reports a clean zero
                # -- which reads exactly like "this season has no snap data".
                # `--snaps-only` skips the load but NOT that requirement, so it
                # asserts the box scores are there instead of assuming it.
                if args.snaps_only:
                    _require_box_scores(season)
                else:
                    total += run_nfl_stats_ingest(
                        client, season, counts, max_age=max_age_for(season)
                    )
                if not args.skip_snaps:
                    run_nfl_snaps_ingest(
                        client, season, counts, max_age=max_age_for(season)
                    )
            set_rows_written(run_id, total)
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
