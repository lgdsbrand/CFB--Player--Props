"""Phase 2c job: backfill box scores, play-by-play and per-play attribution.

    python -m worker.jobs.ingest_stats --seasons 2024
    python -m worker.jobs.ingest_stats --dry-run

Runs ONE SEASON AT A TIME by default. This is the highest-volume job in the
pipeline and the development database is on Supabase's free 500 MB tier, so a
season is loaded, measured, and only then is the next one started — rather than
discovering the ceiling with a season and a half in and no clean way to say
which half is trustworthy.

Two preflights guard the run: CFBD quota (a full season is ~950 calls) and
database headroom.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.ingest_stats import estimate_calls, run_stats_ingest
from worker.adapters.cfbd.quota import (
    QuotaError,
    fetch_account_status,
    require_capacity,
    warn_on_missing_features,
)
from worker.config import ConfigError, get_settings
from worker.db import count_rows, database_size_mb, get_config_value, pipeline_run, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_stats"

REPORTED_TABLES = ("player_game_stats", "plays", "play_player_stats")

# Supabase free tier. Refuse to start a season without room for it, since a
# disk-full failure mid-COPY is far messier than a refusal.
FREE_TIER_MB = 500.0
RESERVE_MB = 60.0
ESTIMATED_MB_PER_SEASON = 105.0


def resolve_seasons(explicit: list[int] | None) -> list[int]:
    if explicit:
        return sorted(explicit)
    configured = get_config_value("backfill_seasons")
    if not configured:
        raise ConfigError("app_config.backfill_seasons is empty and no --seasons given.")
    return sorted(int(s) for s in configured)


def check_headroom(seasons_remaining: int) -> bool:
    used = database_size_mb()
    needed = ESTIMATED_MB_PER_SEASON * seasons_remaining
    available = FREE_TIER_MB - used - RESERVE_MB

    log.info(
        "Database: %.1f MB used, %.1f MB usable (cap %.0f, reserve %.0f). "
        "This run needs roughly %.0f MB.",
        used, available, FREE_TIER_MB, RESERVE_MB, needed,
    )

    if needed > available:
        log.error(
            "Refusing to start: roughly %.0f MB needed but only %.1f MB usable. "
            "Load fewer seasons, or move off the free tier.",
            needed, available,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-headroom-check", action="store_true",
        help="Bypass the free-tier storage guard.",
    )
    parser.add_argument(
        "--box-scores-only", action="store_true",
        help="Load player_game_stats and stop — no play-by-play, no attribution. "
             "For PRIOR-SEASON seasons, whose only job is to supply prior-year "
             "features for the season after them. Costs ~17 calls and a few MB "
             "instead of ~950 calls and ~105 MB. Leaves targets NULL.",
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
        seasons = resolve_seasons(args.seasons)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    # Box scores are a rounding error against the play tables, so the
    # per-season storage estimate does not apply to them.
    if (
        not args.box_scores_only
        and not args.skip_headroom_check
        and not check_headroom(len(seasons))
    ):
        return 4

    before = {t: count_rows(t) for t in REPORTED_TABLES}
    size_before = database_size_mb()

    try:
        with CfbdClient() as client:
            status = fetch_account_status(client)
            log.info("CFBD account: %s", status.summary())
            warn_on_missing_features(status)

            estimated = sum(
                estimate_calls(s, box_scores_only=args.box_scores_only)
                for s in seasons
            )
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
                    JOB_NAME, metadata={"season": season}
                ) as run_id:
                    counts = run_stats_ingest(
                        client, season, box_scores_only=args.box_scores_only
                    )
                    set_rows_written(run_id, counts.total())

                log.info(
                    "Season %d done. Database now %.1f MB (+%.1f MB).",
                    season, database_size_mb(), database_size_mb() - size_before,
                )

            after = {t: count_rows(t) for t in REPORTED_TABLES}
            width = max(len(t) for t in REPORTED_TABLES)
            lines = [
                f"  {t:<{width}}  {after[t]:>9,}  ({after[t] - before[t]:+,})"
                for t in REPORTED_TABLES
            ]
            log.info(
                "Ingest complete. Row counts (delta):\n%s\n"
                "  database: %.1f MB (was %.1f MB)\n"
                "  live API calls: %d  |  %s",
                "\n".join(lines),
                database_size_mb(), size_before,
                client.call_count, client.cache.stats.summary(),
            )
    except Exception as exc:
        log.error("Ingest failed: %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
