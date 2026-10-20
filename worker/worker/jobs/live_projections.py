"""The live model, run on every game in progress (phase L2, migration 0098).

    python -m worker.jobs.live_projections            # what poll_live_scores runs
    python -m worker.jobs.live_projections --dry-run  # project and print, write nothing

Called by `poll_live_scores` straight after the scores, every two minutes
while a game is on, and costs no API call: it reads `live_scores` (CFBD's
scoreboard for college, ESPN's for the NFL) and the game's pregame
projection, and writes `live_projections` (one row per game, overwritten) and
`live_projection_history` (one row per distinct clock and score).

THE PRIOR IS OURS, NEVER A PRICE (CLAUDE.md §11). College: the game model's
projection (`game_projections`, the version the site shows). NFL: the
client's fantasy model (`fantasy_game_projections`, the shown version), a
point estimate, so it takes the spreads the NFL coefficients were fitted
with (core/live_params/nfl.json). A game with no pregame projection gets no
live one: projecting it from the score alone would be a different model from
the one the backtest measured.

THE FITTED MODEL IS A FILE (core/live_params/<sport>.json), written by
`run_live_backtest --write-params` and committed. Nothing is fitted here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from functools import cache
from typing import Any

from worker.adapters.nflverse.mapping import TEAM_ALIASES
from worker.config import ConfigError, get_settings
from worker.core.live_model import FittedModel, Prior, load_fitted, project
from worker.core.live_state import game_state
from worker.db import fetch_all, pipeline_run, set_rows_written, upsert
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "live_projections"

#: The college game model's version the games table shows
#: (jobs/run_game_model.py MODEL_VERSION).
CFB_PRIOR_VERSION = "ratings-v1"
#: The fantasy model's shown version (web/lib/core/fantasy-view.ts
#: SHOWN_FANTASY_VERSION; the client chose it 2026-10-16).
NFL_PRIOR_VERSION = "fixed"
#: p10 to p90 of a normal is 2 × 1.2816 sd.
P10_P90_SDS = 2 * 1.2815515655446004

_DUE_SQL = """
select l.game_id, l.status, l.period, l.clock, l.home_points, l.away_points,
       l.possession, l.situation, g.sport,
       h.abbreviation as h_abbr, h.school as h_school, h.nfl_abbr as h_nfl,
       a.abbreviation as a_abbr, a.school as a_school, a.nfl_abbr as a_nfl,
       gp.margin_mean, gp.margin_p10, gp.margin_p90,
       gp.total_mean, gp.total_p10, gp.total_p90,
       fp.home_margin as f_margin, fp.total as f_total,
       lp.status as projected_status
  from live_scores l
  join games g on g.id = l.game_id
  join teams h on h.id = g.home_team_id
  join teams a on a.id = g.away_team_id
  left join game_projections gp
         on gp.game_id = g.id and gp.model_version = %(cfb_version)s
  left join fantasy_game_projections fp
         on fp.game_id = g.id and fp.model_version = %(nfl_version)s
  left join live_projections lp on lp.game_id = g.id
 where l.status = 'in_progress'
    -- One last write at the final, so the row and the chart end on the result.
    or (l.status = 'completed' and lp.status = 'in_progress')
"""


@cache
def fitted(sport: str) -> FittedModel:
    return load_fitted(sport)


def due_games() -> list[dict[str, Any]]:
    return fetch_all(_DUE_SQL, {"cfb_version": CFB_PRIOR_VERSION, "nfl_version": NFL_PRIOR_VERSION})


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def prior_for(row: dict[str, Any], model: FittedModel) -> tuple[Prior, str] | None:
    if row["sport"] == "cfb":
        values = [
            _f(row[k])
            for k in (
                "margin_mean",
                "margin_p10",
                "margin_p90",
                "total_mean",
                "total_p10",
                "total_p90",
            )
        ]
        if any(v is None for v in values):
            return None
        m, m10, m90, t, t10, t90 = values
        return Prior(m, (m90 - m10) / P10_P90_SDS, t, (t90 - t10) / P10_P90_SDS), "game_model"
    margin, total = _f(row["f_margin"]), _f(row["f_total"])
    if margin is None or total is None or model.prior_margin_sd is None:
        return None
    return Prior(margin, model.prior_margin_sd, total, model.prior_total_sd), "fantasy"


def _nfl_names(abbr: str | None) -> list[str | None]:
    """Our abbreviation and every spelling that maps to it (ESPN writes WSH for WAS)."""
    return [abbr, *(alias for alias, target in TEAM_ALIASES.items() if target == abbr)]


def project_row(row: dict[str, Any], now: datetime) -> tuple[dict, dict] | None:
    """(live_projections row, history row) for one game, or None when it cannot be projected."""
    model = fitted(row["sport"])
    prior = prior_for(row, model)
    if prior is None:
        return None
    prior, source = prior
    final = row["status"] == "completed"
    if row["sport"] == "nfl":
        home_names, away_names = _nfl_names(row["h_nfl"]), _nfl_names(row["a_nfl"])
    else:
        home_names = [row["h_abbr"], row["h_school"]]
        away_names = [row["a_abbr"], row["a_school"]]
    state = game_state(
        period=max(row["period"] or 4, 4) if final else row["period"],
        clock="0:00" if final else row["clock"],
        home_points=row["home_points"],
        away_points=row["away_points"],
        possession=None if final else row["possession"],
        situation_text=None if final else row["situation"],
        home_names=home_names,
        away_names=away_names,
    )
    if state is None:
        return None
    p = project(prior, state, model.params, model.ep)
    m_lo, m_hi = p.margin_interval()
    t_lo, t_hi = p.total_interval()
    seasons = model.fitted_on
    live = {
        "game_id": int(row["game_id"]),
        "status": "completed" if final else "in_progress",
        "period": row["period"],
        "clock": row["clock"],
        "elapsed": state.elapsed,
        "home_points": state.home_points,
        "away_points": state.away_points,
        "possession": {1: "home", -1: "away"}.get(state.possession),
        "down": state.down,
        "yards_to_goal": state.yards_to_goal,
        "p_home_win": round(p.p_home_win, 5),
        "margin_mean": round(p.margin_mean, 1),
        "margin_lo": round(m_lo, 1),
        "margin_hi": round(m_hi, 1),
        "total_mean": round(p.total_mean, 1),
        "total_lo": round(max(t_lo, state.home_points + state.away_points), 1),
        "total_hi": round(t_hi, 1),
        "prior_margin": round(prior.margin_mean, 1),
        "prior_total": round(prior.total_mean, 1),
        "prior_source": source,
        "model": f"{model.params.candidate} {min(seasons)}-{max(seasons)}",
        "updated_at": now,
    }
    history = {
        k: live[k]
        for k in (
            "game_id",
            "elapsed",
            "period",
            "clock",
            "home_points",
            "away_points",
            "p_home_win",
            "margin_mean",
            "total_mean",
        )
    } | {"made_at": now}
    return live, history


def run(now: datetime, *, dry_run: bool = False) -> int:
    """Rows written to live_projections. Per game failures are logged, not fatal."""
    rows = due_games()
    live, history = [], []
    for row in rows:
        try:
            out = project_row(row, now)
        except Exception as exc:  # one bad row must not stop the rest
            log.warning("live projection failed for game %s: %s", row["game_id"], exc)
            continue
        if out is None:
            log.info("game %s: no pregame projection or no clock yet; skipped", row["game_id"])
            continue
        live.append(out[0])
        history.append(out[1])
    log.info("live projections: %d game(s) live, %d projected", len(rows), len(live))
    if dry_run:
        for r in live:
            log.info(
                "  %s",
                {
                    k: r[k]
                    for k in (
                        "game_id",
                        "elapsed",
                        "home_points",
                        "away_points",
                        "p_home_win",
                        "margin_mean",
                        "total_mean",
                    )
                },
            )
        return 0
    if not live:
        return 0
    written = upsert("live_projections", live, conflict_columns=["game_id"])
    upsert(
        "live_projection_history",
        history,
        conflict_columns=["game_id", "elapsed", "home_points", "away_points"],
        update_columns=[],
    )
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
    now = datetime.now(UTC)
    try:
        if not due_games():
            log.info("No game in progress; nothing to project.")
            return 0
        if args.dry_run:
            run(now, dry_run=True)
            return 0
        with pipeline_run(JOB_NAME) as run_id:
            set_rows_written(run_id, run(now))
    except Exception as exc:
        log.error("Live projections failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
