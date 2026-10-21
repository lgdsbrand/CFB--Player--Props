"""Price the game simulator's quarters and halves against the book (phase S3).

    python -m worker.jobs.price_sim_edges             # what the hourly NFL odds chain runs
    python -m worker.jobs.price_sim_edges --dry-run   # price and print, write nothing

For every NFL game not yet started that has a pregame simulation (`game_sims`,
written by `run_game_sims --pregame`) and first-quarter or first-half lines
(`game_odds`, captured by `capture_game_periods --sport nfl` just before this in
the same chain): our probability of each side at the book's line against the
book's vig-free price, one row per game, period and market in
`game_sim_edges` (migration 0100). The numbers come from `core/sim_edges.py`.

No API calls, and no price feeds our number (CLAUDE.md §11). Rows are FROZEN
AT KICKOFF by trigger; each game is written on its own so one refusal (a game
kicking off mid-run) cannot sink the rest. Grading is the view
`v_game_sim_edge_results`, read off the scoreboard's line scores.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from worker.config import ConfigError, get_settings
from worker.core.game_picks import Quote
from worker.core.sim_backtest import PeriodBaseline
from worker.core.sim_edges import PERIODS, PeriodEdge, price_game
from worker.db import fetch_all, fetch_one, pipeline_run, set_rows_written, upsert
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "price_sim_edges"
SPORT = "nfl"

_GAMES_SQL = """
select s.game_id, s.summary, s.prior_home, s.prior_away, s.made_at
  from game_sims s
  join games g on g.id = s.game_id
 where g.sport = %(sport)s and s.stage = 'pregame'
   and g.start_date > now() + interval '1 minute'
 order by g.start_date, s.game_id
"""

_QUOTES_SQL = """
select distinct on (o.game_id, o.sportsbook_id, o.period, o.market)
       o.game_id, o.sportsbook_id, sb.key, o.period, o.market, o.line,
       o.home_price, o.away_price, o.over_price, o.under_price
  from game_odds o
  join sportsbooks sb on sb.id = o.sportsbook_id
 where o.game_id = any(%(games)s) and o.period = any(%(periods)s)
 order by o.game_id, o.sportsbook_id, o.period, o.market, o.captured_at desc
"""


def load_baseline() -> PeriodBaseline | None:
    row = fetch_one("select meta from sim_engines where sport = %s", (SPORT,))
    d = row["meta"].get("period_baseline") if row else None
    if not d:
        log.warning(
            "The saved %s engine has no period baseline (refit with fit_sim_engine); "
            "markets priced from it are skipped.",
            SPORT,
        )
        return None
    return PeriodBaseline.from_dict(d)


def load_quotes(game_ids: list[int]) -> dict[int, dict[str, list[Quote]]]:
    """The newest price per book, period and market, as the game table reads full-game lines."""
    out: dict[int, dict[str, list[Quote]]] = {}
    for r in fetch_all(_QUOTES_SQL, {"games": game_ids, "periods": list(PERIODS)}):
        out.setdefault(int(r["game_id"]), {}).setdefault(r["period"], []).append(
            Quote(
                sportsbook_id=int(r["sportsbook_id"]),
                sportsbook_key=r["key"],
                market=r["market"],
                line=None if r["line"] is None else float(r["line"]),
                home_price=r["home_price"],
                away_price=r["away_price"],
                over_price=r["over_price"],
                under_price=r["under_price"],
            )
        )
    return out


def row(e: PeriodEdge, sims_made_at: datetime) -> dict:
    return {
        "game_id": e.game_id,
        "period": e.period,
        "market": e.market,
        "source": e.source,
        "line": e.line,
        "sportsbook_id": e.sportsbook_id,
        "first_price": e.first_price,
        "second_price": e.second_price,
        "model_prob": round(e.model_prob, 5),
        "book_prob": round(e.book_prob, 5),
        "side": e.side,
        "edge": round(e.edge, 5),
        "model_mean": e.model_mean,
        "sims_made_at": sims_made_at,
    }


def run(*, dry_run: bool = False) -> int:
    games = fetch_all(_GAMES_SQL, {"sport": SPORT})
    if not games:
        log.info("No %s game ahead with a pregame simulation.", SPORT)
        return 0
    quotes = load_quotes([int(g["game_id"]) for g in games])
    if not quotes:
        log.info("%d games simulated, none with first-quarter or first-half lines yet.", len(games))
        return 0
    baseline = load_baseline()
    written = 0
    for g in games:
        gid = int(g["game_id"])
        if gid not in quotes:
            continue
        edges = price_game(
            gid, g["summary"], float(g["prior_home"]), float(g["prior_away"]), quotes[gid], baseline
        )
        for e in edges:
            log.info(
                "game %s %s %-7s %-9s line %5s %-10s ours %.3f book %.3f -> %s %+.3f",
                gid,
                e.period,
                e.market,
                e.source,
                e.line,
                e.sportsbook_key,
                e.model_prob,
                e.book_prob,
                e.side,
                e.edge,
            )
        if dry_run or not edges:
            continue
        try:
            written += upsert(
                "game_sim_edges",
                [row(e, g["made_at"]) for e in edges],
                conflict_columns=["game_id", "period", "market"],
            )
        except Exception as exc:  # one game's refusal (kicked off mid-run) never sinks the rest
            log.warning("game_sim_edges: game %s not written: %s", gid, exc)
    return written


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
    if args.dry_run:
        run(dry_run=True)
        return 0
    with pipeline_run(
        JOB_NAME, metadata={"sport": SPORT, "at": datetime.now(UTC).isoformat()}
    ) as run_id:
        set_rows_written(run_id, run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
