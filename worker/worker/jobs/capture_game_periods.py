"""Capture first-half and first-quarter game lines, open and close.

    python -m worker.jobs.capture_game_periods             # what the cron runs
    python -m worker.jobs.capture_game_periods --dry-run   # resolve, write nothing

The client asked for 1Q and 1H lines beside the full game, from FanDuel and
DraftKings and likely Caesars (CLAUDE.md §11). The bulk endpoint that makes
full-game odds cost 9 credits a slate does not serve them: they are per-event,
billed per market returned for that game, like player props.

TWICE PER GAME. Run hourly, this asks about a game when its periods are first
posted and again in the hour before kickoff, and skips it in between. Up to 6
markets per call, so a 60-game college Saturday is at most ~720 credits a week
and less in practice, since books post periods for the bigger games.

THE PAID KEY. The free key's 500 a month is already spoken for by the
full-game captures and the NFL first-quarter props.

RUNS INSIDE `cfb-props-odds-refresh`, after the props capture, so it needs no
Render service (and no key pasted) of its own. It logs under its own job name
so its hourly runs can never stand in for the full-game capture in
`monitor_pipeline`.
"""

from __future__ import annotations

import argparse
import sys

from worker.adapters.odds import OddsQuotaError
from worker.adapters.odds.markets import sport_key_for
from worker.adapters.odds.theoddsapi import TheOddsApiAdapter
from worker.config import ConfigError, get_settings
from worker.db import pipeline_run, resolve_seasons, set_rows_written
from worker.jobs.ingest_game_odds import run_periods
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "capture_game_periods"
SPORT = "cfb"

# Equal to the cron period, so every kickoff falls in exactly one run's window.
WINDOW_MINUTES = 60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve and report, write nothing. Still bills every game asked about.",
    )
    parser.add_argument("--window-minutes", type=int, default=WINDOW_MINUTES)
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

    key = settings.odds_key(prefer_free=False)
    if not key:
        log.error("ODDS_API_KEY is not set on this service.")
        return 2

    try:
        season = resolve_seasons(None, current=True)[-1]
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    adapter = TheOddsApiAdapter(key, sport_key=sport_key_for(SPORT))
    try:
        with pipeline_run(
            JOB_NAME,
            metadata={
                "sport": SPORT,
                "season": season,
                "window_minutes": args.window_minutes,
                "dry_run": args.dry_run,
            },
        ) as run_id:
            report = run_periods(
                season=season,
                adapter=adapter,
                sport=SPORT,
                close_window_minutes=args.window_minutes,
                dry_run=args.dry_run,
            )
            set_rows_written(run_id, report.rows_written)
            log.info("Game period capture:\n%s", report.render())
    except OddsQuotaError as exc:
        log.error("Out of credits: %s", exc)
        return 3
    except Exception as exc:
        log.error("Game period capture failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
