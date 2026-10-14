"""Picks from the client's NFL fantasy game model, made at the close (phase F3).

    python -m worker.jobs.nfl_fantasy_picks
    python -m worker.jobs.nfl_fantasy_picks --dry-run

Chained hourly after the NFL game-odds capture (render.yaml,
nfl-props-odds-refresh). For each NFL game kicking off within the window, it
takes the model's stored projection (`fantasy_game_projections`, both
versions) and the closing capture's prices, and writes a pick wherever his gap
rule fires (core/fantasy_picks.py): spread 4+ points, total 6+, against
Pinnacle, then DraftKings, then FanDuel.

ONCE PER GAME, ENGINE AND MARKET. A game already holding a pick is skipped,
so the record is one play per game and market at its closing price, never a
string of revisions. A game whose odds were not captured inside the window
gets no pick at all: a pick against a week-old opener would be graded at a
price nobody could still take. The trigger from migration 0074 refuses any
write once the game has started.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from worker.config import ConfigError, get_settings
from worker.core.fantasy_model import VERSIONS
from worker.core.fantasy_picks import ENGINES, MARKETS, evaluate
from worker.core.game_picks import Pick, choose_quote
from worker.db import connect, fetch_all, pipeline_run, resolve_seasons, set_rows_written
from worker.jobs.ingest_game_odds import capture_state
from worker.jobs.run_game_model import load_quotes
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "nfl_fantasy_picks"
PERIOD = "full"
#: The same window the NFL game-odds capture closes on (render.yaml).
DEFAULT_WINDOW_MINUTES = 90
#: game_picks.model_version: which version of the formula made the pick.
MODEL_VERSION = {engine: f"fantasy-{version}" for version, engine in ENGINES.items()}


@dataclass
class PicksReport:
    games: int = 0
    picks: list[Pick] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)

    def render(self) -> str:
        lines = [f"games in window  {self.games}", f"picks            {len(self.picks)}"]
        lines += [f"  {p.engine:13} game {p.game_id} {p.market} {p.side} {p.line} "
                  f"({p.gap} pts) {p.price} {p.sportsbook_key}" for p in self.picks]
        lines += [f"skipped          {k}: {v}" for k, v in sorted(self.skipped.items())]
        return "\n".join(lines)


def picks_for(
    games: list[dict],
    projections: dict[tuple[int, str], dict],
    quotes: dict[int, list],
    already: set[tuple[str, int, str]],
    report: PicksReport,
) -> list[Pick]:
    """Every pick the rule makes on these games, skipping what is already made."""
    out: list[Pick] = []
    for game in games:
        game_id = int(game["id"])
        for version in VERSIONS:
            engine = ENGINES[version]
            proj = projections.get((game_id, version))
            if proj is None:
                report.skipped[f"{engine}: no projection"] += 1
                continue
            for market in MARKETS:
                if (engine, game_id, market) in already:
                    report.skipped["already picked"] += 1
                    continue
                quote = choose_quote(quotes.get(game_id, []), market)
                if quote is None:
                    report.skipped[f"{market}: no Pinnacle/DraftKings/FanDuel price"] += 1
                    continue
                pick = evaluate(game_id, quote, float(proj["home_margin"]),
                                float(proj["total"]), version)
                if pick is not None:
                    out.append(pick)
    return out


def run(*, season: int, now: datetime | None = None, window_minutes: int = DEFAULT_WINDOW_MINUTES,
        dry_run: bool = False) -> PicksReport:
    report = PicksReport()
    now = now or datetime.now(UTC)
    window = timedelta(minutes=window_minutes)

    games = fetch_all(
        "select id, start_date from games where sport = 'nfl' and season = %s "
        "and start_date > %s and start_date <= %s order by start_date",
        (season, now, now + window),
    )
    report.games = len(games)
    if not games:
        log.info("No NFL game kicks off in the next %d minutes.", window_minutes)
        return report
    ids = [int(g["id"]) for g in games]

    with connect() as conn:
        last_capture, _ = capture_state(conn, ids)
    closing = []
    for g in games:
        seen = last_capture.get(int(g["id"]))
        if seen is None or seen < g["start_date"] - window:
            report.skipped["no closing capture"] += 1
        else:
            closing.append(g)

    projections = {
        (int(r["game_id"]), r["model_version"]): r
        for r in fetch_all(
            "select game_id, model_version, home_margin, total "
            "from fantasy_game_projections where game_id = any(%s)",
            (ids,),
        )
    }
    already = {
        (r["engine"], int(r["game_id"]), r["market"])
        for r in fetch_all(
            "select distinct engine, game_id, market from game_picks "
            "where game_id = any(%s) and period = %s and engine = any(%s)",
            (ids, PERIOD, list(ENGINES.values())),
        )
    }
    report.picks = picks_for(closing, projections, load_quotes(ids), already, report)

    if dry_run or not report.picks:
        return report
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            insert into game_picks
              (game_id, period, market, side, line, price, sportsbook_id,
               model_prob, model_version, engine, edge, model_line, gap)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (p.game_id, PERIOD, p.market, p.side, p.line, p.price, p.sportsbook_id,
                 round(p.model_prob, 5), MODEL_VERSION[p.engine], p.engine,
                 round(p.edge, 5), p.model_line, p.gap)
                for p in report.picks
            ],
        )
        conn.commit()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--close-window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES,
        help="Pick games kicking off within this many minutes (default %(default)s).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute; write nothing.")
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
    try:
        season = resolve_seasons(None, current=True)[-1]
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    if args.dry_run:
        report = run(season=season, window_minutes=args.close_window_minutes, dry_run=True)
        log.info("Fantasy picks dry run:\n%s", report.render())
        return 0
    try:
        with pipeline_run(JOB_NAME, metadata={"season": season, "sport": "nfl"}) as run_id:
            report = run(season=season, window_minutes=args.close_window_minutes)
            set_rows_written(run_id, len(report.picks))
            log.info("Fantasy picks:\n%s", report.render())
    except Exception as exc:
        log.error("Fantasy picks failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
