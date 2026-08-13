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
from worker.db import (
    count_rows,
    database_size_mb,
    fetch_one,
    get_config_value,
    pipeline_run,
    record_failed_run,
    resolve_seasons,
    set_rows_written,
)
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_stats"

REPORTED_TABLES = ("player_game_stats", "plays", "play_player_stats")

# Refuse to start without room, since a disk-full failure mid-COPY is far
# messier than a refusal — and on the Supabase free tier passing the cap makes
# the whole project READ-ONLY, which breaks every other job too.
#
# The cap is configuration (`app_config.db_size_cap_mb`) so moving to Pro is a
# row edit rather than a deploy. This constant is only the fallback for a
# database migrated before 20260813140000.
DEFAULT_SIZE_CAP_MB = 500.0
RESERVE_MB = 60.0

# Measured on production 2026-08-13: plays 94.2 MB + play_player_stats 78.5 MB +
# player_game_stats 14.3 MB = 187.0 MB across the 1,852 games that had
# play-by-play loaded. Used only until a database has games of its own to
# measure from, which `_measured_mb_per_game` then prefers.
FALLBACK_MB_PER_GAME = 0.101


def _measured_mb_per_game() -> float:
    """What a game of play-by-play actually costs here, including indexes.

    Measured rather than assumed. The previous guard charged a flat 105 MB per
    season — the cost of a FINISHED season — which is wrong twice over for the
    weekly in-season run: a season in progress has only played a fraction of its
    games, and a season already loaded is reloaded in place and so adds nothing.
    """
    row = fetch_one(
        """
        select coalesce(sum(pg_total_relation_size(c.oid)), 0) / 1024.0 / 1024.0 as mb,
               (select count(distinct game_id) from plays)                       as games
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public'
           and c.relname in ('plays', 'play_player_stats', 'player_game_stats')
        """
    )
    games = int(row["games"] or 0)
    if games < 100:
        return FALLBACK_MB_PER_GAME
    return float(row["mb"]) / games


def estimate_season_mb(season: int, mb_per_game: float) -> tuple[float, float]:
    """(new data, transient rewrite) this season will need, in MB.

    `run_stats_ingest` reloads a season IN PLACE: `ingest_plays` clears the
    season's existing rows before reinserting them. So the two costs are
    different in kind.

    NEW is the games played but not yet loaded — on the first in-season run
    that is the whole season to date, and on every run after it, one week.

    REWRITE is the season's existing rows being written again. Postgres keeps
    the old row versions until vacuum, so a reload transiently needs their space
    a second time. It is zero for a season being loaded for the first time and
    grows through the year, which is the honest reason the free tier stops being
    enough somewhere around midseason rather than at kickoff.
    """
    row = fetch_one(
        """
        select (select count(*) from games
                 where season = %(season)s and completed)              as played,
               (select count(distinct p.game_id) from plays p
                  join games g on g.id = p.game_id
                 where g.season = %(season)s)                          as loaded
        """,
        {"season": season},
    )
    played = int(row["played"] or 0)
    loaded = int(row["loaded"] or 0)
    return max(played - loaded, 0) * mb_per_game, loaded * mb_per_game


def check_headroom(seasons: list[int]) -> bool:
    used = database_size_mb()
    cap = float(get_config_value("db_size_cap_mb") or DEFAULT_SIZE_CAP_MB)
    available = cap - used - RESERVE_MB

    mb_per_game = _measured_mb_per_game()
    estimates = {s: estimate_season_mb(s, mb_per_game) for s in seasons}
    needed = sum(new + rewrite for new, rewrite in estimates.values())

    log.info(
        "Database: %.1f MB used, %.1f MB usable (cap %.0f, reserve %.0f). "
        "At %.3f MB/game this run needs roughly %.0f MB: %s.",
        used, available, cap, RESERVE_MB, mb_per_game, needed,
        ", ".join(
            f"{s} +{new:.0f} MB new / {rewrite:.0f} MB rewritten"
            for s, (new, rewrite) in sorted(estimates.items())
        ) or "nothing",
    )

    if needed > available:
        log.error(
            "Refusing to start: roughly %.0f MB needed but only %.1f MB usable "
            "against a %.0f MB cap. Load fewer seasons, or raise "
            "app_config.db_size_cap_mb once the project is off the free tier.",
            needed, available, cap,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+")
    parser.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only. What the weekly in-season "
             "cron passes; without it the job falls through to backfill_seasons, "
             "which scopes the historical backfill and lags a season behind.",
    )
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
        seasons = resolve_seasons(args.seasons, current=args.current)
    except ConfigError as exc:
        log.error("%s", exc)
        record_failed_run(JOB_NAME, f"ConfigError: {exc}")
        return 2

    # Box scores are a rounding error against the play tables, so the
    # storage estimate does not apply to them.
    if (
        not args.box_scores_only
        and not args.skip_headroom_check
        and not check_headroom(seasons)
    ):
        record_failed_run(
            JOB_NAME,
            "Refused to start: not enough storage headroom. See the run log for "
            "the measured figures, and app_config.db_size_cap_mb for the cap.",
            {"seasons": seasons},
        )
        return 4

    before = {t: count_rows(t) for t in REPORTED_TABLES}
    size_before = database_size_mb()

    # Whether a `pipeline_run` row was ever opened. Everything before the season
    # loop — building the client, the account read, the quota check — fails
    # outside one, and those failures need a row of their own. Once the loop has
    # started, `pipeline_run` writes the failure itself and a second row here
    # would report one broken run as two.
    run_opened = False

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
                record_failed_run(
                    JOB_NAME, f"QuotaError: {exc}", {"seasons": seasons}
                )
                return 3

            if args.dry_run:
                log.info("Dry run: preflights passed, stopping before ingest.")
                return 0

            for season in seasons:
                run_opened = True
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
