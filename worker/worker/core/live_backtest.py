"""Backtest of the live model (phase L1): past games frozen at checkpoints.

SPORT-AGNOSTIC CORE, pure and database-free. The job
(`jobs/run_live_backtest.py`) loads plays, finals and pregame priors; this
module turns them into game states at each checkpoint of the clock, fits the
candidates on earlier seasons and scores them on a later one.

POINT IN TIME (CLAUDE.md §4) on two axes. The prior for a game is the pregame
projection made from games before its week, and the state at a checkpoint
uses only plays before that moment. The coefficients are fitted on earlier
SEASONS than the one they are scored on.

THE TWO SOURCES' SCORE COLUMNS DIFFER, and getting this wrong would leak the
next score into the state. CFBD's `offenseScore` is the score AFTER the play
(a touchdown row already carries the 7, extra point included; checked on
2025 game 1101); nflverse's `posteam_score` is the score BEFORE it. The
timeline converts both to "score before this play" (`scores_after_play`).
NFL plays are offensive snaps only (the adapter keeps plays with a skill
player), so a field goal shows up as the score before the next snap.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl
from scipy.optimize import minimize_scalar
from scipy.stats import norm
from scipy.stats import t as student_t

from worker.core.live_model import (
    CHECKPOINTS,
    MARGIN_FEATURES,
    MIN_SD,
    REGULATION_SECONDS,
    TOTAL_FEATURES,
    Coefficients,
    EpTable,
    GameState,
    LiveParams,
    Prior,
    ep_bin,
    margin_features,
    normal_cdf,
    project,
    total_features,
)

#: z of a normal's 80% central interval.
Z80 = 1.2815515655446004

#: Plays of shrinkage on each team's points-added-per-play so far (candidate
#: B): ten snaps in, the observed rate counts a third.
PPA_SHRINK_PLAYS = 20.0


@dataclass(frozen=True)
class Final:
    game_id: int
    season: int
    home_team_id: int
    away_team_id: int
    home_points: int
    away_points: int


@dataclass
class Timeline:
    """One game's plays in order, as arrays. Scores are BEFORE each play."""

    game_id: int
    seq: np.ndarray  # the source's play id, in order
    elapsed: np.ndarray  # seconds of regulation elapsed at the snap; NaN unknown
    half: np.ndarray  # 1 or 2 (overtime counts as 2)
    possession: np.ndarray  # +1 home offense, −1 away
    down: np.ndarray  # NaN when not a scrimmage down
    ytg: np.ndarray
    ppa: np.ndarray
    home_before: np.ndarray
    away_before: np.ndarray


def build_timelines(
    plays: pl.DataFrame, finals: Mapping[int, Final], *, scores_after_play: bool
) -> dict[int, Timeline]:
    """Per game, the play sequence with home/away scores before each play.

    `plays` columns: game_id, seq (order within the game), period,
    clock_seconds, offense_team_id, down, yards_to_goal, ppa, offense_score,
    defense_score. Plays with no offense team are dropped; a missing score is
    carried forward from the play before.
    """
    out: dict[int, Timeline] = {}
    frame = plays.sort("game_id", "seq")
    for (game_id,), g in frame.group_by("game_id", maintain_order=True):
        final = finals.get(int(game_id))
        if final is None:
            continue
        off = g["offense_team_id"].to_numpy()
        is_home = off == final.home_team_id
        is_away = off == final.away_team_id
        keep = is_home | is_away
        if keep.sum() < 20:
            continue
        g = g.filter(pl.Series(keep))
        is_home = is_home[keep]
        period = g["period"].to_numpy().astype(float)
        clock = g["clock_seconds"].to_numpy().astype(float)
        elapsed = (period - 1) * 900 + (900 - np.clip(clock, 0, 900))
        o_score = _ffill(g["offense_score"].to_numpy().astype(float))
        d_score = _ffill(g["defense_score"].to_numpy().astype(float))
        home = np.where(is_home, o_score, d_score)
        away = np.where(is_home, d_score, o_score)
        if scores_after_play:
            home = np.concatenate([[0.0], home[:-1]])
            away = np.concatenate([[0.0], away[:-1]])
        out[int(game_id)] = Timeline(
            game_id=int(game_id),
            seq=g["seq"].to_numpy(),
            elapsed=elapsed,
            half=np.where(period <= 2, 1, 2),
            possession=np.where(is_home, 1, -1),
            down=g["down"].to_numpy().astype(float),
            ytg=g["yards_to_goal"].to_numpy().astype(float),
            ppa=g["ppa"].to_numpy().astype(float),
            home_before=np.nan_to_num(home),
            away_before=np.nan_to_num(away),
        )
    return out


def _ffill(values: np.ndarray) -> np.ndarray:
    out = values.copy()
    last = 0.0
    for i, v in enumerate(out):
        if np.isnan(v):
            out[i] = last
        else:
            last = v
    return out


def _scrimmage(t: Timeline) -> np.ndarray:
    return (
        (~np.isnan(t.down))
        & (t.down >= 1)
        & (t.down <= 4)
        & (~np.isnan(t.ytg))
        & (t.ytg >= 1)
        & (t.ytg <= 99)
    )


def fit_ep_table(timelines: Sequence[Timeline], finals: Mapping[int, Final]) -> EpTable:
    """Mean next-score points for the offense, by down and 10-yard bin."""
    sums: dict[tuple[int, int], float] = {}
    counts: dict[tuple[int, int], int] = {}
    for t in timelines:
        final = finals[t.game_id]
        n = len(t.elapsed)
        # The score at the end of each half: the first snap of the second half,
        # and the final for the second (overtime included; it is rare).
        second = np.flatnonzero(t.half == 2)
        end1 = (
            (t.home_before[second[0]], t.away_before[second[0]])
            if len(second)
            else (final.home_points, final.away_points)
        )
        end2 = (final.home_points, final.away_points)
        nxt_home = np.empty(n)
        nxt_away = np.empty(n)
        # Walk backwards carrying "the next different score in this half".
        carry = {1: end1, 2: end2}
        for i in range(n - 1, -1, -1):
            h = int(t.half[i])
            nxt_home[i], nxt_away[i] = carry[h]
            if (
                i > 0
                and int(t.half[i - 1]) == h
                and (
                    t.home_before[i] != t.home_before[i - 1]
                    or t.away_before[i] != t.away_before[i - 1]
                )
            ):
                carry[h] = (t.home_before[i], t.away_before[i])
        d_home = nxt_home - t.home_before
        d_away = nxt_away - t.away_before
        value = np.where(t.possession == 1, d_home - d_away, d_away - d_home)
        mask = _scrimmage(t)
        for down, ytg, v in zip(t.down[mask], t.ytg[mask], value[mask], strict=True):
            key = (int(down), ep_bin(int(ytg)))
            sums[key] = sums.get(key, 0.0) + float(v)
            counts[key] = counts.get(key, 0) + 1
    return EpTable({k: sums[k] / counts[k] for k in sums if counts[k] >= 30})


def next_snap(t: Timeline, elapsed: int) -> int | None:
    """Index of the first scrimmage snap at or after `elapsed`, in regulation."""
    after = np.flatnonzero(
        (t.elapsed >= elapsed) & (t.elapsed < REGULATION_SECONDS) & _scrimmage(t)
    )
    return int(after[0]) if len(after) else None


def state_at(t: Timeline, elapsed: int) -> GameState | None:
    """The game at `elapsed` seconds: the score before the next snap, and that snap."""
    i = next_snap(t, elapsed)
    if i is None:
        return None
    if elapsed <= 0:
        return GameState(0, 0, 0, int(t.possession[i]), _int(t.down[i]), _int(t.ytg[i]), 0.0)
    before = (t.elapsed < elapsed) & _scrimmage(t) & ~np.isnan(t.ppa)
    return GameState(
        elapsed=int(elapsed),
        home_points=int(t.home_before[i]),
        away_points=int(t.away_before[i]),
        possession=int(t.possession[i]),
        down=_int(t.down[i]),
        yards_to_goal=_int(t.ytg[i]),
        ppa_diff=_ppa_diff(t, before),
    )


def _int(v: float) -> int | None:
    return None if v is None or np.isnan(v) else int(v)


def _ppa_diff(t: Timeline, mask: np.ndarray) -> float:
    home = mask & (t.possession == 1)
    away = mask & (t.possession == -1)
    rate_h = t.ppa[home].sum() / (home.sum() + PPA_SHRINK_PLAYS)
    rate_a = t.ppa[away].sum() / (away.sum() + PPA_SHRINK_PLAYS)
    return float(rate_h - rate_a)


@dataclass
class Checkpoint:
    game_id: int
    season: int
    elapsed: int
    state: GameState
    prior: Prior
    final_margin: int
    final_total: int
    #: The source's id of the snap the state was read from (benchmark matching).
    play_seq: int
    #: An outside benchmark's home win probability at this moment, if any.
    benchmark: float | None = None


def checkpoints(
    timelines: Mapping[int, Timeline],
    finals: Mapping[int, Final],
    priors: Mapping[int, Prior],
    points: Sequence[int] = CHECKPOINTS,
) -> list[Checkpoint]:
    out = []
    for gid, t in timelines.items():
        prior = priors.get(gid)
        if prior is None:
            continue
        f = finals[gid]
        for e in points:
            s = state_at(t, e)
            if s is None:
                continue
            out.append(
                Checkpoint(
                    gid,
                    f.season,
                    e,
                    s,
                    prior,
                    f.home_points - f.away_points,
                    f.home_points + f.away_points,
                    int(t.seq[next_snap(t, e)]),
                )
            )
    return out


# =============================================================================
# Fitting
# =============================================================================


def _constant_prior(p: Prior) -> Prior:
    """The no-prior baseline: every game's pregame expectation set to 1, sd to 1."""
    return Prior(1.0, 1.0, 1.0, 1.0)


def _design(rows: Sequence[Checkpoint], candidate: str, ep: EpTable, which: str):
    names = (MARGIN_FEATURES if which == "margin" else TOTAL_FEATURES)[candidate]
    feats = margin_features if which == "margin" else total_features
    x, y, sd = [], [], []
    for r in rows:
        prior = _constant_prior(r.prior) if candidate == "base" else r.prior
        f = feats(prior, r.state, ep)
        x.append([f[n] for n in names])
        if which == "margin":
            y.append(r.final_margin - (r.state.home_points - r.state.away_points))
            sd.append(prior.margin_sd)
        else:
            y.append(r.final_total - (r.state.home_points + r.state.away_points))
            sd.append(prior.total_sd)
    return names, np.asarray(x, float), np.asarray(y, float), np.asarray(sd, float)


#: Tail weights tried for the t (inf = a normal).
DF_GRID = (2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 30.0, math.inf)


def fit_t(z: np.ndarray) -> tuple[float, float]:
    """(scale, df) of a zero-centred t by maximum likelihood, df over DF_GRID.

    z is the residual in prior sds. For each df the scale is fitted by
    scipy with the location held at zero; the df with the highest likelihood
    wins.
    """
    best = (-math.inf, 1.0, math.inf)
    for df in DF_GRID:
        if math.isinf(df):
            scale = float(np.sqrt(np.mean(z**2)))
            ll = float(np.sum(-0.5 * (z / scale) ** 2 - math.log(scale * math.sqrt(2 * math.pi))))
        else:
            _, _, scale = student_t.fit(z, f0=df, floc=0)
            ll = float(np.sum(student_t.logpdf(z, df, 0, scale)))
        if ll > best[0]:
            best = (ll, float(scale), df)
    return best[1], best[2]


def fit_win_k(z: np.ndarray, home_won: np.ndarray) -> float:
    """k maximising the likelihood of who won under p = Φ(k · z). No intercept:
    the home edge is already in the projected margin."""

    def nll(k: float) -> float:
        return -float(np.sum(home_won * norm.logcdf(k * z) + (1 - home_won) * norm.logcdf(-k * z)))

    return float(minimize_scalar(nll, bounds=(0.05, 5.0), method="bounded").x)


def fit(rows: Sequence[Checkpoint], candidate: str, ep: EpTable) -> LiveParams:
    """Least squares per checkpoint (no intercept), then the t's scale and tails.

    The scale and tail weight are fitted by maximum likelihood (`fit_t`), and
    for the margin the win % factor k on who won (`fit_win_k`).
    """
    params = LiveParams(candidate)
    by_t: dict[int, list[Checkpoint]] = {}
    for r in rows:
        by_t.setdefault(r.elapsed, []).append(r)
    for e, group in sorted(by_t.items()):
        for which in ("margin", "total"):
            names, x, y, sd = _design(group, candidate, ep, which)
            beta, *_ = np.linalg.lstsq(x, y, rcond=None)
            resid = y - x @ beta
            scale, df = fit_t(resid / sd)
            win_k = 1.0
            if which == "margin":
                now = np.array([r.state.home_points - r.state.away_points for r in group], float)
                final = np.array([r.final_margin for r in group], float)
                decided = final != 0
                z = (now + x @ beta) / np.maximum(scale * sd, MIN_SD)
                win_k = fit_win_k(z[decided], (final[decided] > 0).astype(float))
            coef = Coefficients(
                {n: float(b) for n, b in zip(names, beta, strict=True)}, scale, df, win_k
            )
            (params.margin if which == "margin" else params.total)[e] = coef
    return params


# =============================================================================
# Scoring
# =============================================================================


def predictions(rows: Sequence[Checkpoint], params: LiveParams, ep: EpTable) -> pl.DataFrame:
    out = []
    for r in rows:
        prior = _constant_prior(r.prior) if params.candidate == "base" else r.prior
        p = project(prior, r.state, params, ep)
        out.append(
            {
                "game_id": r.game_id,
                "season": r.season,
                "elapsed": r.elapsed,
                "p_home_win": p.p_home_win,
                "margin_mean": p.margin_mean,
                "margin_sd": p.margin_sd,
                "total_mean": p.total_mean,
                "total_sd": p.total_sd,
                "margin_hw": p.margin_interval()[1] - p.margin_mean,
                "total_hw": p.total_interval()[1] - p.total_mean,
                "final_margin": r.final_margin,
                "final_total": r.final_total,
            }
        )
    return pl.DataFrame(out)


def pregame_only(rows: Sequence[Checkpoint]) -> pl.DataFrame:
    """The pregame projection held all game, ignoring the score: the floor to beat."""
    return pl.DataFrame(
        [
            {
                "game_id": r.game_id,
                "season": r.season,
                "elapsed": r.elapsed,
                "p_home_win": normal_cdf(r.prior.margin_mean / r.prior.margin_sd),
                "margin_mean": r.prior.margin_mean,
                "margin_sd": r.prior.margin_sd,
                "total_mean": r.prior.total_mean,
                "total_sd": r.prior.total_sd,
                "margin_hw": Z80 * r.prior.margin_sd,
                "total_hw": Z80 * r.prior.total_sd,
                "final_margin": r.final_margin,
                "final_total": r.final_total,
            }
            for r in rows
        ]
    )


def benchmark_frame(rows: Sequence[Checkpoint]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "game_id": r.game_id,
                "season": r.season,
                "elapsed": r.elapsed,
                "p_home_win": r.benchmark,
                "final_margin": r.final_margin,
                "final_total": r.final_total,
            }
            for r in rows
            if r.benchmark is not None
        ]
    )


def score(frame: pl.DataFrame) -> dict:
    """Win % (Brier, log loss), projected final margin/total (MAE) and 80% coverage."""
    decided = frame.filter(pl.col("final_margin") != 0)
    p = np.clip(decided["p_home_win"].to_numpy(), 1e-6, 1 - 1e-6)
    won = (decided["final_margin"].to_numpy() > 0).astype(float)
    out = {
        "n": frame.height,
        "brier": float(np.mean((p - won) ** 2)),
        "log_loss": float(-np.mean(won * np.log(p) + (1 - won) * np.log(1 - p))),
    }
    if "margin_mean" in frame.columns:
        m, ms = frame["margin_mean"].to_numpy(), frame["margin_hw"].to_numpy()
        t, ts = frame["total_mean"].to_numpy(), frame["total_hw"].to_numpy()
        fm, ft = frame["final_margin"].to_numpy(), frame["final_total"].to_numpy()
        out |= {
            "margin_mae": float(np.mean(np.abs(m - fm))),
            "total_mae": float(np.mean(np.abs(t - ft))),
            "margin_cover80": float(np.mean(np.abs(fm - m) <= ms)),
            "total_cover80": float(np.mean(np.abs(ft - t) <= ts)),
            "total_over_median": float(np.mean(ft > t)),
        }
    return out


CAL_EDGES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def calibration(frame: pl.DataFrame) -> list[dict]:
    """Win % in ten bins: what it said against how often the home team won."""
    decided = frame.filter(pl.col("final_margin") != 0)
    p = decided["p_home_win"].to_numpy()
    won = (decided["final_margin"].to_numpy() > 0).astype(float)
    rows = []
    for lo, hi in zip(CAL_EDGES[:-1], CAL_EDGES[1:], strict=True):
        mask = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if mask.sum():
            rows.append(
                {
                    "bin": f"{lo:.0%}–{hi:.0%}",
                    "n": int(mask.sum()),
                    "said": float(p[mask].mean()),
                    "won": float(won[mask].mean()),
                }
            )
    return rows


def by_checkpoint(frame: pl.DataFrame) -> dict[int, dict]:
    return {
        int(e): score(g)
        for (e,), g in frame.sort("elapsed").group_by("elapsed", maintain_order=True)
    }


def minutes_left(elapsed: int) -> str:
    left = (REGULATION_SECONDS - elapsed) / 60
    q = min(int(elapsed // 900) + 1, 4)
    clock = (900 - elapsed % 900) / 60
    if elapsed == 0:
        return "Kickoff"
    if elapsed % 900 == 0:
        return {900: "End Q1", 1800: "Halftime", 2700: "End Q3"}[elapsed]
    return f"Q{q} {int(clock)}:{int(round((clock % 1) * 60)):02d} ({left:.0f} min left)"


def isfinite(x: float) -> bool:
    return x is not None and math.isfinite(x)
