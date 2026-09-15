"""Capture DraftKings' first-quarter NFL lines just before each kickoff.

    python -m worker.jobs.capture_first_quarter             # what the cron runs
    python -m worker.jobs.capture_first_quarter --dry-run   # resolve, write nothing

WHY IT EXISTS. First-quarter lines not captured live before kickoff can only be
bought afterwards from the historical endpoint, at ten times the credits. Week 1
of 2026 was lost that way: the capture was a command someone had to run by hand
on a Sunday and a Monday night. This is that command on a schedule.

HOURLY, ASKING ONLY ABOUT THE NEXT HOUR. The cron fires at :40 every hour and
fetches props only for games kicking off in the following 60 minutes, so each
game is captured once, 20 to 80 minutes before it starts, whatever day or time
the league schedules it (London mornings, Thanksgiving, Christmas, Saturdays in
December). The event list it reads first costs nothing, so the ~150 runs a week
that find no game in their window bill nothing either. A game already under way
is never asked about: that would buy an in-play price, not a pre-game line.

A JOB NAME OF ITS OWN, on purpose. It runs `ingest_odds.run`, but logging under
`ingest_odds` with sport `nfl` would count every hourly run as a fresh NFL
capture and hide a dead six-hourly full-game cron from `monitor_pipeline`.

BILLS THE FREE KEY. At most three markets per game (DraftKings posts no others),
so a full NFL week is under ~50 credits. Falls back to the paid key, with a
warning, when `ODDS_API_KEY_FREE` is not set on the service.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.odds import OddsAdapterError
from worker.adapters.odds.markets import FIRST_QUARTER_KEY_TO_PROVIDER
from worker.adapters.odds.null import ADAPTER_NAME as NULL_ADAPTER_NAME
from worker.config import ConfigError, get_settings
from worker.core.schedule import resolve_slate_args
from worker.db import pipeline_run, set_rows_written
from worker.jobs.ingest_odds import resolve_adapter_name, run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "capture_first_quarter"
SPORT = "nfl"
MARKETS = sorted(FIRST_QUARTER_KEY_TO_PROVIDER)

# Equal to the cron period in render.yaml, so consecutive runs tile the clock:
# every kickoff falls in exactly one run's window.
WINDOW_MINUTES = 60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve events and report, write nothing. Still bills props for "
             "games in the window.",
    )
    parser.add_argument(
        "--window-minutes", type=int, default=WINDOW_MINUTES,
        help=f"Capture games kicking off within this many minutes. Default "
             f"{WINDOW_MINUTES}, the cron period.",
    )
    parser.add_argument(
        "--season", type=int, help="Defaults to the current NFL slate's season."
    )
    args = parser.parse_args(argv)

    if args.window_minutes <= 0:
        configure_logging("INFO")
        log.error("--window-minutes must be positive, got %s.", args.window_minutes)
        return 2

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    try:
        adapter_name = resolve_adapter_name(None)
    except Exception as exc:
        log.error("Could not read app_config.odds_adapter: %s", exc)
        return 2

    if adapter_name == NULL_ADAPTER_NAME:
        log.info(
            "app_config.odds_adapter is %r — no lines are ingested, so there is "
            "no first-quarter capture to run.",
            NULL_ADAPTER_NAME,
        )
        return 0

    try:
        season, _ = resolve_slate_args(args.season, None, sport=SPORT)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        with pipeline_run(
            JOB_NAME,
            metadata={
                "sport": SPORT,
                "season": season,
                "markets": MARKETS,
                "window_minutes": args.window_minutes,
                "dry_run": args.dry_run,
            },
        ) as run_id:
            # week=None: the window, not the week, picks the games. A Thursday
            # game and the following Sunday's share a week but not an hour, and
            # the 04:00 ET slate rollover has no bearing on what kicks off next.
            report = run(
                season=season,
                week=None,
                adapter_name=adapter_name,
                dry_run=args.dry_run,
                event_limit=None,
                prefer_free=True,
                sport=SPORT,
                markets=MARKETS,
                kickoff_within_minutes=args.window_minutes,
            )
            log.info(
                "First-quarter capture (%s, next %d min%s):\n%s",
                adapter_name, args.window_minutes,
                ", DRY RUN" if args.dry_run else "", report.render(),
            )
            set_rows_written(run_id, report.rows_written)
    except (ConfigError, OddsAdapterError) as exc:
        log.error("%s", exc)
        return 1
    except Exception as exc:
        log.error("First-quarter capture failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
