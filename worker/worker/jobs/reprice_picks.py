"""Re-price the board's calls against the lines a capture just wrote.

    python -m worker.jobs.reprice_picks --sport cfb
    python -m worker.jobs.reprice_picks --sport nfl --dry-run

Chained hourly after each sport's props odds capture and before
`lock_prop_picks` (render.yaml, cfb-props-odds-refresh and
nfl-props-odds-refresh).

WHY. `v_board_rows` reads the line, price, side and edge off `picks`, and only
`run_projections` writes `picks`, once a day at 12:30 UTC. The close capture
an hour before kickoff therefore reached neither the board nor the props
record before the game: on 2026-10-17 the 15:00 UTC capture wrote 1,363 lines
for the twelve noon games, and the lock that ran a minute later found the
board's calls for one of them. Over the weekend the lock caught 9 of 61
college games and 5 of 13 NFL games.

NO RE-PROJECTION. The distribution does not change when a book moves its line;
only the question asked of it does (run_projections' docstring). `projections`
stores each distribution's family and parameters, so a new line is priced from
the stored row with the same `_insert_picks` the daily run uses: same
calibration, same de-vig in SQL, same book rows. Seconds, not minutes.

WHICH GAMES. Not yet started, projected, and holding a real book line captured
after the game's newest pick (or a line and no pick at all). A game whose lines
have not moved is left alone, so an hour with no capture writes nothing.

A game's picks are replaced in one transaction with the rest of the run, so
the board never shows a game half re-priced.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from typing import Any

import psycopg

from worker.adapters.odds import SYNTHETIC_ADAPTER
from worker.config import ConfigError, get_settings
from worker.core.models import Projection
from worker.core.projections import ProjectedRow
from worker.db import connect, pipeline_run, set_rows_written
from worker.jobs.run_projections import MarketMeta, _insert_picks
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "reprice_picks"
SPORTS = ("cfb", "nfl")

_STALE_GAMES = """
    select g.id
      from games g
     where g.sport = %(sport)s
       and g.start_date > now()
       and exists (select 1 from projections p where p.game_id = g.id)
       and exists (
             select 1 from player_prop_lines l
              where l.game_id = g.id
                and l.source_adapter <> %(synthetic)s
                and l.captured_at > coalesce(
                      (select max(pk.created_at) from picks pk where pk.game_id = g.id),
                      '-infinity'::timestamptz))
"""

_PROJECTIONS = """
    select p.id, p.player_id, p.game_id, p.team_id, p.opponent_team_id,
           p.market_key, p.season, p.week, p.as_of_week,
           p.distribution::text as distribution, p.params, p.mean
      from projections p
     where p.game_id = any(%s)
"""


def stored_row(record: dict[str, Any]) -> ProjectedRow:
    """A ProjectedRow rebuilt from a `projections` row, for pricing only.

    `position_group` is not stored on `projections` and the pick path never
    reads it, so it is left empty rather than looked up.
    """
    return ProjectedRow(
        player_id=int(record["player_id"]),
        game_id=int(record["game_id"]),
        team_id=int(record["team_id"]),
        opponent_team_id=int(record["opponent_team_id"]),
        market_key=str(record["market_key"]),
        position_group="",
        season=int(record["season"]),
        week=int(record["week"]),
        as_of_week=int(record["as_of_week"]),
        projection=Projection(
            market_key=str(record["market_key"]),
            distribution=str(record["distribution"]),
            params={k: float(v) for k, v in record["params"].items()},
            mean=float(record["mean"]) if record["mean"] is not None else 0.0,
        ),
    )


def _markets(conn: psycopg.Connection) -> dict[str, MarketMeta]:
    with conn.cursor() as cur:
        cur.execute("select key, default_line, stat_column from markets")
        return {
            str(r["key"]): MarketMeta(
                default_line=float(r["default_line"]) if r["default_line"] is not None else None,
                stat_column=str(r["stat_column"]),
            )
            for r in cur.fetchall()
        }


def reprice(conn: psycopg.Connection, sport: str) -> dict[str, int]:
    """Replace the picks of every stale game. Caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute(_STALE_GAMES, {"sport": sport, "synthetic": SYNTHETIC_ADAPTER})
        game_ids = [int(r["id"]) for r in cur.fetchall()]
    if not game_ids:
        return {"games": 0, "picks": 0, "picks_with_book_line": 0}

    with conn.cursor() as cur:
        cur.execute(_PROJECTIONS, (game_ids,))
        records = cur.fetchall()
        cur.execute("delete from picks where game_id = any(%s)", (game_ids,))

    by_week: dict[tuple[int, int], list[ProjectedRow]] = defaultdict(list)
    ids: dict[tuple[int, int, str], int] = {}
    for record in records:
        row = stored_row(record)
        by_week[(row.season, row.week)].append(row)
        ids[(row.player_id, row.game_id, row.market_key)] = int(record["id"])

    markets = _markets(conn)
    total = with_book_line = 0
    for (season, week), rows in sorted(by_week.items()):
        written = _insert_picks(conn, season, week, rows, ids, markets, sport)
        total += written["total"]
        with_book_line += written["with_book_line"]
    return {"games": len(game_ids), "picks": total, "picks_with_book_line": with_book_line}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=SPORTS, required=True)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Re-price inside a transaction that is rolled back; report, write nothing.",
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
        if args.dry_run:
            with connect() as conn:
                counts = reprice(conn, args.sport)
                conn.rollback()
            log.info("DRY RUN, rolled back: %s", counts)
            return 0
        with pipeline_run(JOB_NAME, metadata={"sport": args.sport}) as run_id:
            with connect() as conn:
                counts = reprice(conn, args.sport)
            set_rows_written(run_id, counts["picks"])
        log.info("%s re-priced: %s", args.sport, counts)
    except Exception as exc:
        log.error("Re-pricing failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
