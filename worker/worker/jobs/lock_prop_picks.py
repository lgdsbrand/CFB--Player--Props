"""Lock the board's prop calls before kickoff, for the props record (0096).

    python -m worker.jobs.lock_prop_picks --sport cfb
    python -m worker.jobs.lock_prop_picks --sport nfl --dry-run

Chained hourly after each sport's props odds capture (render.yaml,
cfb-props-odds-refresh and nfl-props-odds-refresh). For every game of the sport
kicking off inside the window, it copies each player-market's headline row on
`v_board_rows` into `prop_pick_locks`: the book the board shows, its line and
two-way price, the side, the published probability and the edge.

WHY A COPY. `run_projections` deletes and rewrites the whole week every day,
played games included, so `picks` cannot be a record (see the migration).

THE BOARD'S LAST STATE BEFORE KICKOFF. The window is wider than the hour
between runs, so each game is seen two or three times; a later run refreshes
the row (only when something differs), and the trigger refuses every write from
kickoff. Each game is written in its own savepoint, so a game that kicks off
mid-run is skipped instead of failing the others.

WHAT IS LOCKED: real two-way book lines on markets that publish a call. No
anytime TD (one-way, no edge), no first-quarter markets, no DEV book.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

from worker.config import ConfigError, get_settings
from worker.db import connect, pipeline_run, set_rows_written
from worker.jobs.run_projections import SYNTHETIC_BOOK_KEY
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "lock_prop_picks"
SPORTS = ("cfb", "nfl")
#: Wider than the hour between runs, so every game gets two or three chances
#: and the last one before kickoff stands.
DEFAULT_WINDOW_MINUTES = 150

# The board's headline row per player-market, as `v_board_rows` serves it. Read
# from the view rather than re-deriving the book choice, so the record and the
# board cannot drift (this repo has shipped that class of bug five times).
_BOARD_ROWS = """
    select b.game_id, b.player_id, b.team_id, b.opponent_team_id, b.market_key,
           b.season, b.week, sb.id as sportsbook_id, b.line, b.over_price,
           b.under_price, b.side, b.model_prob_over, b.book_prob_over, b.edge,
           b.projected_median
      from v_board_rows b
      join sportsbooks sb on sb.key = b.sportsbook_key
     where b.game_id = %(game_id)s
       and b.has_book_line
       and b.publishes_call
       and not b.is_binary
       and b.side is not null
       and b.sportsbook_key <> %(synthetic)s
"""

_UPSERT = f"""
    insert into prop_pick_locks
      (game_id, player_id, team_id, opponent_team_id, market_key, season, week,
       sportsbook_id, line, over_price, under_price, side, model_prob_over,
       book_prob_over, edge, projected_median)
    {_BOARD_ROWS}
    on conflict (game_id, player_id, market_key) do update set
      sportsbook_id    = excluded.sportsbook_id,
      line             = excluded.line,
      over_price       = excluded.over_price,
      under_price      = excluded.under_price,
      side             = excluded.side,
      model_prob_over  = excluded.model_prob_over,
      book_prob_over   = excluded.book_prob_over,
      edge             = excluded.edge,
      projected_median = excluded.projected_median
    where (prop_pick_locks.sportsbook_id, prop_pick_locks.line,
           prop_pick_locks.over_price, prop_pick_locks.under_price,
           prop_pick_locks.side, prop_pick_locks.model_prob_over,
           prop_pick_locks.book_prob_over, prop_pick_locks.edge,
           prop_pick_locks.projected_median)
          is distinct from
          (excluded.sportsbook_id, excluded.line, excluded.over_price,
           excluded.under_price, excluded.side, excluded.model_prob_over,
           excluded.book_prob_over, excluded.edge, excluded.projected_median)
    returning market_key, (xmax = 0) as inserted
"""


@dataclass
class LockReport:
    games: int = 0
    new: Counter = field(default_factory=Counter)
    refreshed: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)

    @property
    def written(self) -> int:
        return sum(self.new.values()) + sum(self.refreshed.values())

    def render(self) -> str:
        lines = [
            f"games in window  {self.games}",
            f"new              {sum(self.new.values())}",
            f"refreshed        {sum(self.refreshed.values())}",
        ]
        lines += [f"  {market:18} new {self.new[market]:4}  refreshed {self.refreshed[market]:4}"
                  for market in sorted(set(self.new) | set(self.refreshed))]
        lines += [f"skipped          {k}: {v}" for k, v in sorted(self.skipped.items())]
        return "\n".join(lines)


def lock_game(conn: psycopg.Connection, game_id: int, report: LockReport, *,
              dry_run: bool = False) -> None:
    """Lock one game's board rows. Caller owns the transaction."""
    params = {"game_id": game_id, "synthetic": SYNTHETIC_BOOK_KEY}
    if dry_run:
        for row in conn.execute(_BOARD_ROWS, params).fetchall():
            report.new[row["market_key"]] += 1
        return
    try:
        with conn.transaction():
            rows = conn.execute(_UPSERT, params).fetchall()
    except psycopg.errors.RaiseException as exc:
        # The freeze trigger: the game kicked off between the window query and
        # this write. Nothing was written for it; the others carry on.
        log.warning("Game %s not locked: %s", game_id, exc.diag.message_primary)
        report.skipped["kicked off during the run"] += 1
        return
    if not rows:
        report.skipped["no priced call on the board"] += 1
    for row in rows:
        (report.new if row["inserted"] else report.refreshed)[row["market_key"]] += 1


def run(*, sport: str, now: datetime | None = None,
        window_minutes: int = DEFAULT_WINDOW_MINUTES, dry_run: bool = False) -> LockReport:
    report = LockReport()
    now = now or datetime.now(UTC)
    with connect() as conn:
        games = conn.execute(
            "select id from games where sport = %s and start_date > %s "
            "and start_date <= %s order by start_date",
            (sport, now, now + timedelta(minutes=window_minutes)),
        ).fetchall()
        report.games = len(games)
        if not games:
            log.info("No %s game kicks off in the next %d minutes.", sport, window_minutes)
            return report
        for game in games:
            lock_game(conn, int(game["id"]), report, dry_run=dry_run)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=SPORTS, required=True)
    parser.add_argument(
        "--close-window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES,
        help="Lock games kicking off within this many minutes (default %(default)s).",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Count what would be locked; write nothing.")
    args = parser.parse_args(argv)
    if args.close_window_minutes <= 0:
        parser.error("--close-window-minutes must be positive")

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    if args.dry_run:
        report = run(sport=args.sport, window_minutes=args.close_window_minutes, dry_run=True)
        log.info("Prop pick lock dry run (%s):\n%s", args.sport, report.render())
        return 0
    try:
        with pipeline_run(JOB_NAME, metadata={"sport": args.sport}) as run_id:
            report = run(sport=args.sport, window_minutes=args.close_window_minutes)
            set_rows_written(run_id, report.written)
            log.info("Prop pick locks (%s):\n%s", args.sport, report.render())
    except Exception as exc:
        log.error("Prop pick lock failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
