"""The live (in-game) model: a pregame projection updated by the game so far.

SPORT-AGNOSTIC CORE, pure and database-free (CLAUDE.md §3). The client asked
for "a mid game model reran without pulling more odds credits ... an updated
prediction based off how the game's going" (2026-10-19). This module turns a
pregame projection plus the game's state at one moment into a projected final
score, a win % and fair margin/total. Phase L1 measures it on past games
(core/live_backtest.py); L2 calls the same `project` every two minutes.

THE MARKET NEVER FEEDS IT (CLAUDE.md §11). The prior is our own pregame
projection (college: the game model; NFL: the client's fantasy model), and
the state is the scoreboard. No price is read anywhere here.

THE MODEL. The final margin is the current margin plus what the rest of the
game adds, and the rest follows a Student t around a mean built from
features of the moment:

    remaining margin ~ t_ν( Σ b_k · f_k ,  c · pregame margin sd )
    remaining total  ~ t_ν( Σ a_k · g_k ,  d · pregame total sd )

with the coefficients fitted separately at each checkpoint of the game clock
(every five minutes, plus three and one minute left) on past seasons, and
interpolated linearly between them. The features (`margin_features`,
`total_features`) are the pregame expectation scaled by the time left, the
expected points of the possession in progress from field position and down,
and, in candidate B only, how the game has gone against the pregame view.
Fitting per checkpoint lets the data say how fast the prior should fade
rather than assuming the clock runs at a constant scoring rate.

A T, NOT A NORMAL, measured in L1 (2026-10-19). With a normal, an NFL home
side given 2% won 7.7% of the time (2025, 573 checkpoints) and college 80%
ranges held 90-96% of finals late in games: what is left of a game is mostly
"nothing changes" or one score, with fatter tails than a normal has. The
tails' weight ν is fitted with the scale at each checkpoint, by maximum
likelihood, so the data says how fat they are.

THE WIN % IS FITTED TO WINS. A t fitted to everything left in a game is
shaped by the typical case, while the win % turns on one tail (does the
side behind come back). Measured in L1: even with the t, an NFL home side
given 2.6% won 8.2%. So the win % is Φ(k · projected margin / scale), with
k fitted at each checkpoint by maximum likelihood on who actually won; k
falls from about 1 at kickoff to 0.25-0.4 in the last minute, where the
scale alone is far too sure. The ranges still come from the t, whose 80%
ranges held 77-84% of finals at every checkpoint.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from scipy.stats import t as student_t

REGULATION_SECONDS = 3600
QUARTER_SECONDS = 900

#: Checkpoints of elapsed regulation time the coefficients are fitted at:
#: kickoff, every five minutes, then three and one minute left.
CHECKPOINTS: tuple[int, ...] = (*range(0, 3600, 300), 3420, 3540)

#: Feature names per candidate. "A" = score, clock, field position; "B" adds
#: how the game has played against the pregame view. "base" is the no-prior
#: baseline: the same as A with every game's pregame expectation set to 1, so
#: its coefficient learns the league's average instead of a team's.
MARGIN_FEATURES: Mapping[str, tuple[str, ...]] = {
    "A": ("prior", "ep"),
    "B": ("prior", "ep", "ppa", "excess"),
    "base": ("prior", "ep"),
}
TOTAL_FEATURES: Mapping[str, tuple[str, ...]] = {
    "A": ("prior", "ep"),
    "B": ("prior", "ep", "pace"),
    "base": ("prior", "ep"),
}

#: Yards-to-goal bins of the expected-points table: 1-10, 11-20, ..., 91-99.
EP_BIN_YARDS = 10


@dataclass(frozen=True)
class Prior:
    """The pregame projection, home-oriented: margin = home − away."""

    margin_mean: float
    margin_sd: float
    total_mean: float
    total_sd: float


@dataclass(frozen=True)
class GameState:
    """The game at one moment, as a scoreboard (or a play-by-play row) says it.

    `possession` is +1 when the home team has the ball, −1 the away team, 0
    unknown (between plays, kickoffs). `down`/`yards_to_goal` describe the
    snap coming up; None when unknown. `ppa_diff` is home minus away points
    added per play so far, shrunk toward zero (candidate B only).
    """

    elapsed: int
    home_points: int
    away_points: int
    possession: int = 0
    down: int | None = None
    yards_to_goal: int | None = None
    ppa_diff: float = 0.0


@dataclass(frozen=True)
class EpTable:
    """Expected points of the next score for the team with the ball, by down and field position.

    "Next score" within the half, signed: + when the offense scores next, −
    when its opponent does, 0 when nobody scores before the half ends. Fitted
    from past play-by-play (`core/live_backtest.fit_ep_table`).
    """

    values: Mapping[tuple[int, int], float]

    def value(self, down: int | None, yards_to_goal: int | None) -> float:
        if down is None or yards_to_goal is None or not 1 <= down <= 4:
            return 0.0
        return self.values.get((down, ep_bin(yards_to_goal)), 0.0)


def ep_bin(yards_to_goal: int) -> int:
    return min(max((int(yards_to_goal) - 1) // EP_BIN_YARDS, 0), 9)


def elapsed_seconds(period: int | None, clock_seconds: int | None) -> int | None:
    """Regulation time elapsed at a play; overtime counts past 3600."""
    if period is None or clock_seconds is None or period < 1:
        return None
    return (period - 1) * QUARTER_SECONDS + (
        QUARTER_SECONDS - min(max(clock_seconds, 0), QUARTER_SECONDS)
    )


def time_left_share(elapsed: int) -> float:
    return max(REGULATION_SECONDS - elapsed, 0) / REGULATION_SECONDS


def margin_features(prior: Prior, state: GameState, ep: EpTable) -> dict[str, float]:
    tau = time_left_share(state.elapsed)
    margin = state.home_points - state.away_points
    return {
        "prior": prior.margin_mean * tau,
        "ep": state.possession * ep.value(state.down, state.yards_to_goal),
        "ppa": state.ppa_diff * tau,
        # Points ahead of the pregame expectation so far, weighted by the time
        # left: a team beating the projection may be better than projected.
        "excess": (margin - prior.margin_mean * (1 - tau)) * tau,
    }


def total_features(prior: Prior, state: GameState, ep: EpTable) -> dict[str, float]:
    tau = time_left_share(state.elapsed)
    total = state.home_points + state.away_points
    return {
        "prior": prior.total_mean * tau,
        "ep": abs(state.possession) * ep.value(state.down, state.yards_to_goal),
        "pace": (total - prior.total_mean * (1 - tau)) * tau,
    }


@dataclass
class Coefficients:
    """Fitted at each checkpoint: feature weights, scale (in prior sds), the t's tail weight."""

    weights: dict[str, float]
    sd_scale: float
    df: float = math.inf  # inf = a normal
    win_k: float = 1.0  # margin only: the win % is Φ(win_k · mean / scale)


@dataclass
class LiveParams:
    """One candidate's fitted coefficients for one sport, keyed by checkpoint."""

    candidate: str
    margin: dict[int, Coefficients] = field(default_factory=dict)
    total: dict[int, Coefficients] = field(default_factory=dict)

    def at(self, which: str, elapsed: int) -> Coefficients:
        """Linear interpolation between the two checkpoints around `elapsed`.

        After the last checkpoint the weights hold and the sd scale runs to
        zero at the final whistle, where nothing is left to happen.
        """
        table = self.margin if which == "margin" else self.total
        points = sorted(table)
        if elapsed <= points[0]:
            return table[points[0]]
        if elapsed >= points[-1]:
            last = table[points[-1]]
            span = REGULATION_SECONDS - points[-1]
            share = max(REGULATION_SECONDS - elapsed, 0) / span if span else 0.0
            return Coefficients(dict(last.weights), last.sd_scale * share, last.df, last.win_k)
        hi = next(i for i, p in enumerate(points) if p >= elapsed)
        lo_t, hi_t = points[hi - 1], points[hi]
        w = (elapsed - lo_t) / (hi_t - lo_t)
        lo_c, hi_c = table[lo_t], table[hi_t]
        return Coefficients(
            {k: (1 - w) * lo_c.weights[k] + w * hi_c.weights[k] for k in lo_c.weights},
            (1 - w) * lo_c.sd_scale + w * hi_c.sd_scale,
            _blend_df(lo_c.df, hi_c.df, w),
            (1 - w) * lo_c.win_k + w * hi_c.win_k,
        )


def _blend_df(lo: float, hi: float, w: float) -> float:
    if math.isinf(lo) and math.isinf(hi):
        return math.inf
    # Blend tail weights on 1/ν, where a normal is 0, so inf mixes smoothly.
    inv = (1 - w) * (0.0 if math.isinf(lo) else 1 / lo) + w * (0.0 if math.isinf(hi) else 1 / hi)
    return math.inf if inv == 0 else 1 / inv


@dataclass(frozen=True)
class LiveProjection:
    margin_mean: float  # projected FINAL home margin
    margin_sd: float  # the t's scale, in points
    total_mean: float  # projected FINAL total
    total_sd: float
    p_home_win: float
    margin_df: float = math.inf
    total_df: float = math.inf

    def margin_interval(self, coverage: float = 0.8) -> tuple[float, float]:
        return interval(self.margin_mean, self.margin_sd, self.margin_df, coverage)

    def total_interval(self, coverage: float = 0.8) -> tuple[float, float]:
        return interval(self.total_mean, self.total_sd, self.total_df, coverage)

    @property
    def home_score(self) -> float:
        return (self.total_mean + self.margin_mean) / 2

    @property
    def away_score(self) -> float:
        return (self.total_mean - self.margin_mean) / 2


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


#: Floor on the remaining sd, in points, so a probability never reaches 0 or 1
#: while a play is still to be run.
MIN_SD = 0.5


def project(prior: Prior, state: GameState, params: LiveParams, ep: EpTable) -> LiveProjection:
    """The projected final, given the pregame view and the game so far."""
    margin_now = state.home_points - state.away_points
    total_now = state.home_points + state.away_points
    if state.elapsed >= REGULATION_SECONDS:
        # Overtime or over: the live model makes no claim past regulation.
        p = 1.0 if margin_now > 0 else 0.0 if margin_now < 0 else 0.5
        return LiveProjection(margin_now, 0.0, total_now, 0.0, p)

    mc = params.at("margin", state.elapsed)
    tc = params.at("total", state.elapsed)
    mf = margin_features(prior, state, ep)
    tf = total_features(prior, state, ep)
    rem_margin = sum(mc.weights[k] * mf[k] for k in mc.weights)
    rem_total = sum(tc.weights[k] * tf[k] for k in tc.weights)
    margin_sd = max(mc.sd_scale * prior.margin_sd, MIN_SD)
    total_sd = max(tc.sd_scale * prior.total_sd, MIN_SD)
    margin_mean = margin_now + rem_margin
    # Never project fewer points than are already on the board.
    total_mean = max(total_now + rem_total, total_now)
    return LiveProjection(
        margin_mean=margin_mean,
        margin_sd=margin_sd,
        total_mean=total_mean,
        total_sd=total_sd,
        p_home_win=normal_cdf(mc.win_k * margin_mean / margin_sd),
        margin_df=mc.df,
        total_df=tc.df,
    )


def interval(
    mean: float, scale: float, df: float = math.inf, coverage: float = 0.8
) -> tuple[float, float]:
    """Central interval of a t (a normal when df is inf)."""
    q = 0.5 + coverage / 2
    z = float(student_t.ppf(q, df if not math.isinf(df) else 1e9))
    return mean - z * scale, mean + z * scale


def checkpoint_list(points: Sequence[int] = CHECKPOINTS) -> list[int]:
    return sorted(set(points))
