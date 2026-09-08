"""N2 job: NFL conferences, teams, schedule and rosters.

    python -m worker.jobs.nfl_ingest_reference --seasons 2026
    python -m worker.jobs.nfl_ingest_reference --seasons 2026 --dry-run

A separate job from `ingest_reference` rather than a `--sport` flag on it. The
two share no provider, no client, no preflight and no failure modes: the CFBD
one opens with a quota check because a metered key can be suspended, and this
one has nothing to check because nflverse is static files on a CDN. Threading a
sport through would mean a job whose every step branches on it, which is the
shape CLAUDE.md §3 asks the adapter layer to absorb instead.

NO QUOTA PREFLIGHT: nflverse is static files on a CDN and there is no meter to
check.

`--seasons` AND `--current` ARE MUTUALLY EXCLUSIVE AND ONE IS REQUIRED. The
college jobs accept neither and fall through to `app_config.backfill_seasons`,
which scopes the HISTORICAL backfill -- a bug that shipped, ran green for days,
and refreshed last season every morning while reporting success. This job has no
such fallthrough: give it an explicit year or tell it to read
`app_config.current_season`, and omitting both is an argparse error rather than a
successful run against the wrong one.

`--current` was added when the daily results cron was, because a scheduled job
that hardcodes its year in `render.yaml` reproduces exactly the failure the flag
exists to prevent -- it just moves it into a file no test reads.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.ingest_reference import run_nfl_reference_ingest
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

JOB_NAME = "nfl_ingest_reference"

REPORTED_TABLES = (
    "conferences",
    "teams",
    "team_seasons",
    "games",
    "players",
    "player_team_seasons",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--seasons", type=int, nargs="+",
        help="Seasons to ingest. Deliberately not defaulted -- see the module "
             "docstring on why omitting both this and --current is an error.",
    )
    scope.add_argument(
        "--current", action="store_true",
        help="Work on app_config.current_season only. What the daily in-season "
             "cron passes, so the schedule carries no hardcoded year.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve the sources and report what would be written, then stop.",
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
    log.info("NFL reference ingest for season(s): %s", seasons)

    before = {t: count_rows(t) for t in REPORTED_TABLES}
    client = NflverseClient()

    if args.dry_run:
        # A dry run still READS, because reading is free here and the whole
        # question a preview answers is "does the source have what we expect".
        # The college equivalent stops at a quota check because there the read
        # is the cost; here it is not.
        try:
            for season in seasons:
                schedule = client.fetch("schedule", season, max_age=900.0)
                roster = client.fetch("roster", season, max_age=900.0)
                games = schedule.filter(schedule["season"] == season)
                log.info(
                    "%d: %d scheduled game(s), %d roster row(s), %d team(s)",
                    season, games.height, roster.height,
                    roster["team"].n_unique(),
                )
        except NflverseError as exc:
            log.error("%s", exc)
            return 3
        log.info("Dry run: sources resolved, stopping before any write.")
        return 0

    if not check_storage_headroom(JOB_NAME):
        return 4

    try:
        with pipeline_run(JOB_NAME, metadata={"seasons": seasons}) as run_id:
            counts = run_nfl_reference_ingest(client, seasons)
            set_rows_written(run_id, counts.total())
    except NflverseError as exc:
        log.error("%s", exc)
        record_failed_run(JOB_NAME, f"NflverseError: {exc}", {"seasons": seasons})
        return 3

    after = {t: count_rows(t) for t in REPORTED_TABLES}
    log.info("Ingest complete. Row counts (delta vs before):")
    for table in REPORTED_TABLES:
        delta = after[table] - before[table]
        log.info("  %-22s %8s  (%+d)", table, f"{after[table]:,}", delta)
    log.info(
        "  downloads=%d cache_hits=%d", client.downloads, client.cache_hits
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
