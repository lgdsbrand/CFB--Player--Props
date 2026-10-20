"""The game simulator in production (phase S2): the saved engine, and what a run stores.

SPORT-AGNOSTIC CORE, pure and database-free. `jobs/fit_sim_engine.py` fits an
`Engine` once a week and saves it; `jobs/run_game_sims.py` loads it and plays
each game 2,000 times before kickoff and every minute while it is on.

WHAT IS STORED AND WHY (decided 2026-10-20 on the S1 backtest,
docs/nfl-sim-backtest.html). The simulator tied the live model on the full
game, so the live model keeps the full-game win %, margin and total; nothing
here stores a full-game win %. The simulator supplies the field animation (one
recorded game), each quarter's and half's points, and the distributions a
book's quarter or half line is priced from (S3). Every distribution is kept
whole (a count per score), so any line can be priced later without a rerun.
"""

from __future__ import annotations

import pickle
import zlib
from dataclasses import dataclass, field

import numpy as np

from worker.core.live_state import clock_seconds
from worker.core.play_sim import (
    Result,
    SimTables,
    Start,
    StrengthCurve,
    pregame,
    snap_start,
    solve_thetas,
)

#: Games per run (the client's reference shows 2,000).
SIMS = 2000
#: Games recorded play by play, of which the one nearest the typical result is kept.
REPLAY_CANDIDATES = 20


@dataclass
class Engine:
    """A fitted simulator plus what turns a projected score into team strength."""

    tables: SimTables
    curve: StrengthCurve
    sigma: float
    kick_pool: int
    meta: dict = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return zlib.compress(
            pickle.dumps(
                {
                    "tables": self.tables.to_bytes(),
                    "curve": self.curve.coef,
                    "sigma": self.sigma,
                    "meta": self.meta,
                },
                protocol=pickle.HIGHEST_PROTOCOL,
            ),
            1,
        )

    @classmethod
    def from_bytes(cls, blob: bytes) -> Engine:
        state = pickle.loads(zlib.decompress(blob))  # noqa: S301 -- our own artifact, from our database
        tables = SimTables.from_bytes(state["tables"])
        # The saved kickoffs are exactly the pool the curve was fitted with.
        kick_pool = tables.whole_kick_pool()
        return cls(
            tables, StrengthCurve(tuple(state["curve"])), state["sigma"], kick_pool, state["meta"]
        )

    def thetas(self, home_points: float, away_points: float) -> tuple[float, float]:
        return solve_thetas(self.curve, home_points, away_points)


# =============================================================================
# Start states
# =============================================================================


def pregame_start(
    engine: Engine, home_points: float, away_points: float, rng, n: int = SIMS
) -> Start:
    h, a = engine.thetas(home_points, away_points)
    noise = engine.sigma
    return pregame(
        n,
        rng,
        h + noise * rng.standard_normal(n),
        a + noise * rng.standard_normal(n),
        engine.kick_pool,
    )


@dataclass(frozen=True)
class LiveState:
    """One live_scores row, read for the simulator. possession +1 home, −1 away, 0 unknown."""

    period: int
    clock: str | None
    home_points: int
    away_points: int
    possession: int
    down: int | None
    distance: int | None
    yards_to_goal: int | None


def live_start(
    engine: Engine, state: LiveState, home_proj: float, away_proj: float, rng, n: int = SIMS
) -> Start | None:
    """n games from where a game in progress is, or None when the moment cannot be placed.

    With a down, distance and spot, the next snap. Without (between plays,
    after a score, at a quarter's or the half's break), the next kickoff, by
    the team without the ball when possession is known, else either at
    random. Who receives the second-half kickoff is not on the scoreboard: in
    the first half it is drawn at random per game.
    """
    if state.period is None or state.period < 1:
        return None
    if state.period > 4:
        return None  # overtime: the scoreboard's clock is not regulation time; the score stands
    left = clock_seconds(state.clock)
    gsr = (4 - state.period) * 900 + (left if left is not None else 0)
    if gsr <= 0:
        return None
    h, a = engine.thetas(home_proj, away_proj)
    th_h = h + engine.sigma * rng.standard_normal(n)
    th_a = a + engine.sigma * rng.standard_normal(n)
    # Past the half the second-half kickoff has happened and recv2 is never read.
    recv2 = np.where(rng.random(n) < 0.5, 1, -1) if state.period <= 2 else np.ones(n)
    snap_ready = (
        state.possession != 0 and state.down is not None and state.yards_to_goal is not None
    )
    if snap_ready:
        togo = state.distance if state.distance is not None else min(10, state.yards_to_goal)
        start = snap_start(
            n,
            gsr=gsr,
            pos=state.possession,
            down=state.down,
            togo=float(togo),
            ytg=float(state.yards_to_goal),
            home=state.home_points,
            away=state.away_points,
            recv2=1,
            theta_home=th_h,
            theta_away=th_a,
            kick_pool=engine.kick_pool,
        )
    else:
        start = pregame(n, rng, th_h, th_a, engine.kick_pool)
        start.gsr[:] = gsr
        start.home[:] = state.home_points
        start.away[:] = state.away_points
        if state.possession != 0:
            start.kicker[:] = -state.possession
    start.recv2[:] = recv2
    return start


# =============================================================================
# What a run stores
# =============================================================================


def histogram(values: np.ndarray) -> dict:
    """Counts per integer value: {"lo": smallest, "n": [count at lo, lo+1, ...]}."""
    v = np.asarray(values, dtype=np.int64)
    lo = int(v.min())
    return {"lo": lo, "n": np.bincount(v - lo).astype(int).tolist()}


def p_over(hist: dict, line: float) -> float:
    """P(value > line) from a stored histogram (a half-point line never pushes)."""
    counts = np.asarray(hist["n"], dtype=float)
    values = hist["lo"] + np.arange(len(counts))
    return float(counts[values > line].sum() / counts.sum())


def summarize_game(
    res: Result, done_home: list[int] | None = None, done_away: list[int] | None = None
) -> dict:
    """One game's k simulations as stored in game_sims.

    `done_home`/`done_away`: points already scored in each quarter (the
    scoreboard's line scores) for a game in progress, added to what the
    simulations score from now, so a quarter or half is always its whole.
    """
    k = len(res.home)
    q = res.quarters.astype(np.int64).copy()  # (k, 4, 2)
    for side, done in ((0, done_home), (1, done_away)):
        for i, pts in enumerate((done or [])[:4]):
            q[:, i, side] += int(pts or 0)
    # res.home/res.away already start from the current score; quarters count new points only.
    home_final = res.home.astype(np.int64)
    away_final = res.away.astype(np.int64)
    out: dict = {"sims": k, "quarters": [], "halves": {}}
    for i in range(4):
        h, a = q[:, i, 0], q[:, i, 1]
        out["quarters"].append(_segment(h, a))
    h1h, h1a = q[:, :2, 0].sum(axis=1), q[:, :2, 1].sum(axis=1)
    h2h = q[:, 2:, 0].sum(axis=1) + res.overtime[:, 0]
    h2a = q[:, 2:, 1].sum(axis=1) + res.overtime[:, 1]
    out["halves"] = {"first": _segment(h1h, h1a), "second": _segment(h2h, h2a)}
    out["game"] = _segment(home_final, away_final)
    out["overtime_share"] = float((res.overtime.sum(axis=1) > 0).mean())
    return out


def _segment(home: np.ndarray, away: np.ndarray) -> dict:
    total = home + away
    margin = home - away
    return {
        "home": round(float(home.mean()), 2),
        "away": round(float(away.mean()), 2),
        "home_range": [int(np.quantile(home, 0.1)), int(np.quantile(home, 0.9))],
        "away_range": [int(np.quantile(away, 0.1)), int(np.quantile(away, 0.9))],
        "p_home_lead": round(float((margin > 0).mean()), 4),
        "p_tie": round(float((margin == 0).mean()), 4),
        "total": histogram(total),
        "margin": histogram(margin),
    }


def pick_replay(res: Result, target_home: float, target_away: float) -> list[dict]:
    """The recorded game whose final score is nearest the typical one (closest to the means)."""
    if not res.replays:
        return []
    n = len(res.replays)
    d = (res.home[:n] - target_home) ** 2 + (res.away[:n] - target_away) ** 2
    return res.replays[int(np.argmin(d))]
