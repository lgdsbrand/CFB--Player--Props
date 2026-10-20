"""College live scores, every two minutes while a game is on (migration 0088).

    python -m worker.jobs.poll_live_scores
    python -m worker.jobs.poll_live_scores --dry-run   # call and match, write nothing

Scheduled every two minutes all week. Each run first asks OUR database whether
any college game is in its window (5 minutes before kickoff to 5 hours after,
not yet final). If none is, it exits at once: no CFBD call and no
`pipeline_runs` row, because 700 idle rows a day would bury the real ones.
When one is, it makes ONE `/scoreboard` call for the whole FBS week and
overwrites those games' `live_scores` rows. Details in
adapters/cfbd/live_scores.py.

THEN THE PLAYERS (migration 0092). Each college game in progress that has
book props has its play-by-play read at most every five minutes, and once
more just after it ends, into `live_player_stats`: one `/live/plays` call
per game. Unofficial; see adapters/cfbd/live_player_stats.py. Runs even when
no game is left in the scoreboard's window, so the night's last game still
gets its final read.

COST: about 360 CFBD calls on a Saturday and ~100 on a weeknight with games,
~3,500 a month, for the scoreboard; about 10,500 a month for the players
(~58 games a week x ~42 reads), which stop below 5,000 calls left in the
month. Against a 30,000 allowance. No odds credits. No account
preflight: it would be a second call on every poll.

NFL TOO, AND THE LIVE MODEL (phase L2, migration 0098). While one of our NFL
games is in its window, one call to ESPN's public scoreboard
(adapters/espn/scoreboard.py) writes NFL rows to the same `live_scores`,
recorded as its own run with sport 'nfl' so a failure on one feed never hides
behind a success on the other. Then `live_projections` runs the live model on
every game in progress, both sports, from those rows: no API call at all.
Each of the three is its own run, and a failure in one does not stop the next.

TWICE A RUN WHILE AN NFL GAME IS ON (2026-10-20). The client bets games live
off these numbers and asked for a way to recalculate on demand. The site
never calls a provider itself (CLAUDE.md §2), so the freshness has to come
from here: while one of our NFL games is in progress, the run waits a minute
and polls ESPN and reruns the model once more, so the NFL numbers are never
much more than a minute old against a two-minute schedule. ESPN costs
nothing; college stays at one CFBD scoreboard call a run, its budget is real.
A run that does this lasts a little over a minute, inside the two.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

from worker.adapters.cfbd import live_player_stats
from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.live_scores import games_in_window, poll
from worker.adapters.espn import scoreboard as espn
from worker.config import ConfigError, get_settings
from worker.db import fetch_all, pipeline_run, set_rows_written
from worker.jobs import live_projections, run_game_sims
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "poll_live_scores"
SECOND_NFL_POLL_SECONDS = 60


def nfl_in_progress() -> bool:
    """Whether one of our NFL games is in progress, by the row the last poll wrote."""
    return bool(
        fetch_all(
            """
            select 1
              from live_scores l
              join games g on g.id = l.game_id
             where g.sport = 'nfl' and l.status = 'in_progress'
             limit 1
            """
        )
    )


def poll_nfl(now: datetime, *, dry_run: bool = False) -> bool:
    """ESPN's scoreboard into `live_scores`, as its own run. True when it failed."""
    try:
        if dry_run:
            espn.poll(now, dry_run=True)
        else:
            with pipeline_run(JOB_NAME, metadata={"sport": "nfl"}) as run_id:
                set_rows_written(run_id, espn.poll(now) or 0)
    except Exception as exc:
        log.error("NFL live score poll failed: %s", exc, exc_info=True)
        return True
    return False


def project(now: datetime, *, dry_run: bool = False) -> bool:
    """The live model on every game in progress, as its own run. True when it failed."""
    try:
        if dry_run:
            live_projections.run(now, dry_run=True)
        elif live_projections.due_games():
            with pipeline_run(live_projections.JOB_NAME) as run_id:
                set_rows_written(run_id, live_projections.run(now))
    except Exception as exc:
        log.error("Live projections failed: %s", exc, exc_info=True)
        return True
    return False


def simulate_live(*, dry_run: bool = False) -> bool:
    """The game simulator from every NFL game in progress, as its own run. True when it failed.

    After the live model, and never in its way: the simulator supplies the
    field animation and the quarter and half numbers (phase S2), the live
    model the full game.
    """
    try:
        if dry_run:
            run_game_sims.run_live(dry_run=True)
        elif run_game_sims.due_live():
            with pipeline_run(run_game_sims.LIVE_JOB_NAME) as run_id:
                set_rows_written(run_id, run_game_sims.run_live())
    except Exception as exc:
        log.error("Live game simulations failed: %s", exc, exc_info=True)
        return True
    return False


def second_nfl_pass(
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    """A minute on, ESPN and the model once more, while an NFL game is on. True when it failed."""
    try:
        if not nfl_in_progress():
            return False
    except Exception as exc:
        log.error("Could not read whether an NFL game is in progress: %s", exc, exc_info=True)
        return True
    sleep(SECOND_NFL_POLL_SECONDS)
    now = clock()
    failed = poll_nfl(now)
    failed = project(now) or failed
    return simulate_live() or failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    now = datetime.now(UTC)
    failed = False
    try:
        # The cheap questions first, outside any pipeline_run: most runs end here.
        scores_due = bool(games_in_window(now))
        players_due = bool(live_player_stats.games_due(now))
        nfl_due = bool(espn.games_in_window(now))
    except Exception as exc:
        log.error("Live score poll could not read the schedule: %s", exc, exc_info=True)
        return 1
    if not (scores_due or players_due or nfl_due):
        log.info("No game in its window; nothing to poll.")
        return 0

    if scores_due or players_due:
        try:
            with CfbdClient() as client:
                if args.dry_run:
                    if scores_due:
                        poll(client, now, dry_run=True)
                    live_player_stats.poll(client, now, dry_run=True)
                else:
                    with pipeline_run(JOB_NAME, metadata={"sport": "cfb"}) as run_id:
                        written = (poll(client, now) or 0) if scores_due else 0
                        # After the scores, so a game the scoreboard just called
                        # final is read once more with its last plays.
                        written += live_player_stats.poll(client, now)
                        set_rows_written(run_id, written)
        except Exception as exc:
            log.error("College live score poll failed: %s", exc, exc_info=True)
            failed = True

    if nfl_due and poll_nfl(now, dry_run=args.dry_run):
        failed = True
    if project(now, dry_run=args.dry_run):
        failed = True
    if nfl_due and simulate_live(dry_run=args.dry_run):
        failed = True
    if nfl_due and not args.dry_run and second_nfl_pass():
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
