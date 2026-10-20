"""Play each NFL game 2,000 times: before kickoff, and every minute while it is on (phase S2).

    python -m worker.jobs.run_game_sims --pregame             # what the daily chain runs
    python -m worker.jobs.run_game_sims --pregame --dry-run   # simulate and print, write nothing
    python -m worker.jobs.run_game_sims --live --dry-run      # the live pass, by hand

The live pass also runs inside `poll_live_scores` (`run_live`), straight after
the live model, as its own pipeline run: a failure here never stops the
scores or the live model.

TEAM STRENGTH IS OURS (CLAUDE.md §11): the client's fantasy model's projected
points (`fantasy_game_projections`, the shown version), the same prior the
live model uses for the NFL. A game with no projection is not simulated.

PREGAME RUNS ARE FROZEN AT KICKOFF by a trigger on `game_sims` (migration
0099); a game is only offered here while its kickoff is more than a minute
away, and each game is written on its own so one refusal cannot sink the rest.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from functools import cache
from typing import Any

import numpy as np
import sklearn

from worker.config import ConfigError, get_settings
from worker.core.live_state import distance, situation
from worker.core.play_sim import Result, concat, simulate
from worker.core.sim_runtime import (
    REPLAY_CANDIDATES,
    SIMS,
    Engine,
    LiveState,
    live_start,
    pick_replay,
    pregame_start,
    summarize_game,
)
from worker.db import fetch_all, fetch_one, pipeline_run, set_rows_written, upsert
from worker.jobs.live_projections import NFL_PRIOR_VERSION, _nfl_names
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "run_game_sims"
LIVE_JOB_NAME = "game_sims_live"
SPORT = "nfl"
#: How far ahead pregame runs reach: the coming week's games.
HORIZON_DAYS = 8

_PREGAME_SQL = """
select g.id as game_id, fp.home_score, fp.away_score
  from games g
  join fantasy_game_projections fp on fp.game_id = g.id and fp.model_version = %(version)s
 where g.sport = 'nfl'
   and g.start_date > now() + interval '1 minute'
   and g.start_date < now() + make_interval(days => %(days)s)
 order by g.start_date, g.id
"""

_LIVE_SQL = """
select l.game_id, l.period, l.clock, l.home_points, l.away_points, l.possession, l.situation,
       l.home_line_scores, l.away_line_scores, h.nfl_abbr as h_nfl, a.nfl_abbr as a_nfl,
       fp.home_score, fp.away_score
  from live_scores l
  join games g on g.id = l.game_id
  join teams h on h.id = g.home_team_id
  join teams a on a.id = g.away_team_id
  join fantasy_game_projections fp on fp.game_id = g.id and fp.model_version = %(version)s
 where g.sport = 'nfl' and l.status = 'in_progress'
 order by l.game_id
"""


@cache
def load_engine() -> tuple[Engine, str]:
    row = fetch_one("select version, payload from sim_engines where sport = %s", (SPORT,))
    if row is None:
        raise RuntimeError("No NFL simulator saved yet: run worker.jobs.fit_sim_engine first.")
    engine = Engine.from_bytes(bytes(row["payload"]))
    # scikit-learn is not pinned; a pickled classifier from another version
    # usually loads, but say so when it is not the one that fitted it.
    fitted_with = engine.meta.get("sklearn")
    if fitted_with and fitted_with != sklearn.__version__:
        log.warning(
            "Simulator fitted with scikit-learn %s, running on %s.",
            fitted_with,
            sklearn.__version__,
        )
    return engine, row["version"]


def _run(starts, replay_starts, rng, engine) -> tuple[list[dict], list[list[dict]]]:
    """Simulate every game in one batch; summaries and replays per game, in order."""
    res = simulate(engine.tables, concat(starts), rng)
    rep = simulate(
        engine.tables, concat(replay_starts), rng, record=REPLAY_CANDIDATES * len(starts)
    )
    out_s, out_r = [], []
    for i in range(len(starts)):
        sl = slice(i * SIMS, (i + 1) * SIMS)
        out_s.append(_slice(res, sl))
        rs = slice(i * REPLAY_CANDIDATES, (i + 1) * REPLAY_CANDIDATES)
        out_r.append(_slice(rep, rs, replays=rep.replays[rs]))
    return out_s, out_r


def _slice(res: Result, sl: slice, replays: list | None = None) -> Result:
    return Result(
        res.home[sl], res.away[sl], res.quarters[sl], res.overtime[sl], res.steps, 0, replays or []
    )


def _row(
    game_id, stage, prior_home, prior_away, summary, replay, version, **state
) -> dict[str, Any]:
    return {
        "game_id": game_id,
        "stage": stage,
        "sims": summary["sims"],
        "prior_home": round(prior_home, 2),
        "prior_away": round(prior_away, 2),
        "summary": json.dumps(summary),
        "replay": json.dumps(replay),
        "engine_version": version,
        **state,
    }


def _write(rows: list[dict]) -> int:
    written = 0
    for row in rows:
        try:
            written += upsert("game_sims", [row], conflict_columns=["game_id", "stage"])
        except (
            Exception
        ) as exc:  # one game's refusal (e.g. kicked off mid-run) never sinks the rest
            log.warning("game_sims: game %s %s not written: %s", row["game_id"], row["stage"], exc)
    return written


def run_pregame(*, dry_run: bool = False, rng: np.random.Generator | None = None) -> int:
    rng = rng or np.random.default_rng()
    games = fetch_all(_PREGAME_SQL, {"version": NFL_PRIOR_VERSION, "days": HORIZON_DAYS})
    if not games:
        log.info("No NFL game with a projection in the next %d days.", HORIZON_DAYS)
        return 0
    engine, version = load_engine()
    proj = [(float(g["home_score"]), float(g["away_score"])) for g in games]
    starts = [pregame_start(engine, h, a, rng) for h, a in proj]
    reps = [pregame_start(engine, h, a, rng, n=REPLAY_CANDIDATES) for h, a in proj]
    sums, reps_res = _run(starts, reps, rng, engine)
    rows = []
    for g, (h, a), full, r in zip(games, proj, sums, reps_res, strict=True):
        s = summarize_game(full)
        replay = pick_replay(r, float(full.home.mean()), float(full.away.mean()))
        rows.append(_row(g["game_id"], "pregame", h, a, s, replay, version))
        log.info(
            "pregame game %s: projected %.1f-%.1f, simulated %.1f-%.1f; Q1 %.1f-%.1f; replay %d plays",
            g["game_id"],
            h,
            a,
            s["game"]["home"],
            s["game"]["away"],
            s["quarters"][0]["home"],
            s["quarters"][0]["away"],
            len(replay),
        )
    if dry_run:
        return 0
    return _write(rows)


def live_state(row: dict[str, Any]) -> LiveState:
    side = {"home": 1, "away": -1}.get(row["possession"] or "", 0)
    down = ytg = dist = None
    if side:
        home, away = _nfl_names(row["h_nfl"]), _nfl_names(row["a_nfl"])
        offense, defense = (home, away) if side == 1 else (away, home)
        down, ytg = situation(row["situation"], offense, defense)
        dist = distance(row["situation"], ytg)
    return LiveState(
        period=row["period"],
        clock=row["clock"],
        home_points=int(row["home_points"] or 0),
        away_points=int(row["away_points"] or 0),
        possession=side,
        down=down,
        distance=dist,
        yards_to_goal=ytg,
    )


def due_live() -> list[dict[str, Any]]:
    return fetch_all(_LIVE_SQL, {"version": NFL_PRIOR_VERSION})


def run_live(*, dry_run: bool = False, rng: np.random.Generator | None = None) -> int:
    rng = rng or np.random.default_rng()
    games = due_live()
    if not games:
        return 0
    engine, version = load_engine()
    kept, starts, reps = [], [], []
    for g in games:
        st = live_state(g)
        h, a = float(g["home_score"]), float(g["away_score"])
        start = live_start(engine, st, h, a, rng)
        if start is None:
            continue
        kept.append((g, st, h, a))
        starts.append(start)
        reps.append(live_start(engine, st, h, a, rng, n=REPLAY_CANDIDATES))
    if not kept:
        return 0
    sums, reps_res = _run(starts, reps, rng, engine)
    rows = []
    for (g, st, h, a), full, r in zip(kept, sums, reps_res, strict=True):
        s = summarize_game(full, g["home_line_scores"], g["away_line_scores"])
        replay = pick_replay(r, float(full.home.mean()), float(full.away.mean()))
        rows.append(
            _row(
                g["game_id"],
                "live",
                h,
                a,
                s,
                replay,
                version,
                period=st.period,
                clock=st.clock,
                home_points=st.home_points,
                away_points=st.away_points,
            )
        )
        log.info(
            "live game %s at Q%s %s %d-%d: simulated final %.1f-%.1f",
            g["game_id"],
            st.period,
            st.clock,
            st.home_points,
            st.away_points,
            s["game"]["home"],
            s["game"]["away"],
        )
    if dry_run:
        return 0
    return _write(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pregame", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    run = run_pregame if args.pregame else run_live
    name = JOB_NAME if args.pregame else LIVE_JOB_NAME
    if args.dry_run:
        run(dry_run=True)
        return 0
    with pipeline_run(
        name, metadata={"sport": SPORT, "at": datetime.now(UTC).isoformat()}
    ) as run_id:
        set_rows_written(run_id, run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
