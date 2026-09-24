"""Grade the game model's shadow picks — result and closing line value.

    python -m worker.jobs.grade_game_picks
    python -m worker.jobs.grade_game_picks --season 2026 --weeks 4 5

G4 of the game model (CLAUDE.md §11). On demand, read-only: prints a summary
and writes nothing. For each (game, market) the pick graded is the NEWEST row
made before kickoff (a switch of side replaces the earlier pick; see
core/game_picks.py), and its close is the SAME book's last `game_odds` row
captured before kickoff. Formulas in core/game_grading.py.

A CLOSE IS ONLY AS GOOD AS THE LAST CAPTURE. Captures run three times on a
Saturday; a game whose last capture was a day before kickoff has a stale
"close", and the summary counts how many closes were captured within
`CLOSE_WINDOW_HOURS` of kickoff so that is visible rather than assumed.

Costs no API calls.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict

from worker.config import ConfigError, get_settings
from worker.core.game_grading import Graded, grade
from worker.db import fetch_all, get_config_value
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

PERIOD = "full"
CLOSE_WINDOW_HOURS = 6.0

_SQL = """
with graded as (
  select distinct on (p.game_id, p.market)
         p.id, p.game_id, p.market, p.side, p.line, p.price, p.sportsbook_id,
         p.model_prob, p.made_at, g.start_date, g.week,
         g.home_points - g.away_points as margin,
         g.home_points + g.away_points as total
    from game_picks p
    join games g on g.id = p.game_id
   where g.sport = %(sport)s and g.season = %(season)s
     and (%(weeks)s::int[] is null or g.week = any(%(weeks)s))
     and g.completed and g.home_points is not null
     and p.period = %(period)s and p.made_at < g.start_date
   order by p.game_id, p.market, p.made_at desc, p.id desc
)
select gr.*,
       c.line as close_line, c.home_price as close_home, c.away_price as close_away,
       c.over_price as close_over, c.under_price as close_under,
       extract(epoch from gr.start_date - c.captured_at) / 3600.0 as close_hours_before
  from graded gr
  left join lateral (
    select o.line, o.home_price, o.away_price, o.over_price, o.under_price, o.captured_at
      from game_odds o
     where o.game_id = gr.game_id and o.sportsbook_id = gr.sportsbook_id
       and o.period = %(period)s and o.market = gr.market
       and o.captured_at < gr.start_date
     order by o.captured_at desc
     limit 1
  ) c on true
 order by gr.week, gr.game_id, gr.market
"""


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def summarise(graded: list[tuple[Graded, float | None]]) -> str:
    by_market: dict[str, list[tuple[Graded, float | None]]] = defaultdict(list)
    for g, hours in graded:
        by_market[g.market].append((g, hours))

    lines = []
    for market in ("spreads", "totals", "h2h"):
        rows = by_market.get(market, [])
        if not rows:
            continue
        gs = [g for g, _ in rows]
        w = sum(g.result == "win" for g in gs)
        lo = sum(g.result == "loss" for g in gs)
        pu = len(gs) - w - lo
        roi = _mean([g.profit for g in gs if g.result != "push"])
        pts = [g.clv_points for g in gs if g.clv_points is not None]
        prob = [g.clv_prob for g in gs if g.clv_prob is not None]
        fresh = sum(h is not None and h <= CLOSE_WINDOW_HOURS for _, h in rows)
        beat = sum(p > 0 for p in pts) if pts else 0
        lines.append(
            f"{market:8s} picks {len(gs):4d}  W-L-P {w}-{lo}-{pu}  ROI {100 * roi:+.1f}%"
        )
        if pts:
            se = (
                math.sqrt(sum((p - _mean(pts)) ** 2 for p in pts) / (len(pts) - 1))
                / math.sqrt(len(pts))
                if len(pts) > 1 else float("nan")
            )
            lines.append(
                f"         CLV points  mean {_mean(pts):+.2f} (se {se:.2f})  "
                f"line moved our way {beat}/{len(pts)}"
            )
        if prob:
            lines.append(
                f"         CLV prob    mean {100 * _mean(prob):+.2f} pts of probability "
                f"on {len(prob)} picks where the line did not move"
            )
        lines.append(
            f"         closes captured within {CLOSE_WINDOW_HOURS:.0f}h of kickoff: "
            f"{fresh}/{len(rows)}"
        )
    return "\n".join(lines) if lines else "no graded picks"


def run(*, season: int, sport: str = "cfb", weeks: list[int] | None = None) -> str:
    rows = fetch_all(
        _SQL, {"sport": sport, "season": season, "weeks": weeks, "period": PERIOD}
    )
    graded = []
    for r in rows:
        close = None
        if r["close_hours_before"] is not None:
            close = {
                "line": r["close_line"],
                "home_price": r["close_home"], "away_price": r["close_away"],
                "over_price": r["close_over"], "under_price": r["close_under"],
            }
        pick = {
            "market": r["market"], "side": r["side"],
            "line": None if r["line"] is None else float(r["line"]), "price": r["price"],
        }
        hours = None if r["close_hours_before"] is None else float(r["close_hours_before"])
        graded.append((grade(pick, float(r["margin"]), float(r["total"]), close), hours))
    return summarise(graded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", default="cfb", choices=("cfb",))
    parser.add_argument("--season", type=int, help="default: app_config.current_season")
    parser.add_argument("--weeks", type=int, nargs="+")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    season = args.season or get_config_value("current_season")
    if season is None:
        log.error("app_config.current_season is unset and no --season given.")
        return 2
    print(run(season=int(season), sport=args.sport, weeks=args.weeks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
