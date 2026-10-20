"""A play-by-play football game simulator (phase S1).

SPORT-AGNOSTIC CORE, pure and database-free (CLAUDE.md §3). The client asked
for a game simulation like the ones on social media, 2,000 games played out
play by play with one shown on a field, and for each quarter's and half's
score beside the odds (2026-10-20). This module plays the games. It learns
from plain tables (`SimEvents`) that an adapter fills; the NFL's come from
nflverse (`adapters/nflverse/sim_plays.py`). Rules that differ by league live
in `Rules`.

THE MARKET NEVER FEEDS IT (CLAUDE.md §11). Team strength comes from our own
pregame projection; nothing here reads a price.

HOW ONE PLAY IS PLAYED, thousands of games at once (numpy arrays, one row per
simulated game, every row taking its next play in the same step):

  1. The offense chooses: run, pass, punt, field goal, kneel or spike, from a
     gradient-boosted classifier fitted on what offenses actually chose by
     down, distance, field position, clock and score.
  2. A run or pass is a REAL past play drawn from the same situation (down,
     distance band, field-position band, and whether the clock is being
     hurried or run down), keeping its yards, first down, turnover, score and
     the seconds until the next snap together. Drawing whole plays keeps the
     fat tails (the 80-yard touchdown, the pick-six) that a fitted
     distribution of yards would smooth away.
  3. Team strength tilts the draw: each past play is weighted by
     exp(θ × its EPA), so a strong offense (θ > 0) draws good plays a little
     more often. One θ per offense per game, set so the simulated average
     score matches our projection (`fit_strength_curve`, `solve_thetas`).
  4. Punts are drawn the same way by field position; field goals succeed with
     a fitted chance by distance; kickoffs are drawn from the season's
     kickoffs before this week (the kickoff rule changed in 2024 and again in
     2025, so older seasons would misplace every drive start); tries after a
     touchdown go for two on the usual chart late in games.

WHAT IT DOES NOT MODEL, and why each is acceptable for a first version:
timeouts (their effect on the clock is inside the drawn plays' seconds),
player injuries, weather, and onside kicks as a decision (drawn at their
league rate). The backtest says whether the result is good enough.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

# ---- the tables' vocabulary --------------------------------------------------

#: How a run, pass, kneel, spike or punt ended.
KIND_CONT = 0  # the same team snaps next (dy yards gained, fd first down)
KIND_TD_OFF = 1  # the team with the ball scored a touchdown
KIND_TD_DEF = 2  # the other team scored one (pick-six, return)
KIND_TURNOVER = 3  # the other team has the ball (delta: where, see below)
KIND_SAFETY = 4

#: How a kickoff ended.
KICK_RECEIVED = 0
KICK_RETURN_TD = 1
KICK_TEAM_TD = 2
KICK_ONSIDE = 3

CLASSES = ("run", "pass", "punt", "fg", "kneel", "spike")
RUN, PASS, PUNT, FG, KNEEL, SPIKE = range(6)

#: Clock situations: normal, hurrying (end of a half, or behind late), and
#: running it down (ahead late). The drawn play keeps its own seconds, so
#: these are what make a two-minute drill fast and a lead burn the clock.
CLK_NORMAL, CLK_HURRY, CLK_MILK = 0, 1, 2

PHASE_SCRIM, PHASE_KICK, PHASE_PAT = 0, 1, 2

#: Fewer past plays than this in a situation and the draw widens (see _POOL_LEVELS).
MIN_POOL = 40
#: θ values the tilted draws are precomputed for; a game's θ is rounded to
#: one of them at random in proportion, so the rounding is unbiased.
THETA_GRID = np.round(np.arange(-0.8, 0.8001, 0.05), 3)
#: EPA is clipped before tilting so one freak play cannot dominate a pool.
EPA_CLIP = 6.0
#: The tilt is exp(θ × TILT_SCALE × EPA). Measured: unscaled, θ = 0.1 already
#: took a team from 22.5 to 29.4 points, so the grid's 0.05 steps were ~3
#: points apart. At 0.3, a step is about a point and θ ±0.8 spans roughly
#: 11 to 37 points against an average opponent, the range projections live in.
TILT_SCALE = 0.3
#: Steps after which a simulated game is stopped (a real one has ~180).
MAX_STEPS = 700


def clock_state(qtr, half_secs, game_secs, diff) -> np.ndarray:
    """CLK_* per play: hurry in a half's last two minutes or behind in the last five; milk ahead late."""
    qtr = np.asarray(qtr)
    half_secs = np.asarray(half_secs, dtype=float)
    game_secs = np.asarray(game_secs, dtype=float)
    diff = np.asarray(diff, dtype=float)
    late = (qtr >= 4) & (game_secs <= 300)
    hurry = ((half_secs <= 120) & ((qtr == 2) | (qtr >= 4) & (diff <= 0))) | (late & (diff < 0))
    milk = late & (diff > 0) & ~hurry
    return np.where(hurry, CLK_HURRY, np.where(milk, CLK_MILK, CLK_NORMAL)).astype(np.int8)


@dataclass
class SimEvents:
    """What the simulator learns from. Column contracts:

    decisions:  down, togo, ytg, half_secs, game_secs, qtr, diff, label (one of CLASSES)
    scrimmage:  cls (run/pass/kneel/spike), down, togo, ytg, clk, kind, dy, fd, delta, secs, epa
    punts:      ytg, kind, dy, fd, delta, secs
    field_goals: distance, made
    kickoffs:   season, week, kind (KICK_*), ytg (the receiving or recovering team's), secs
    pats:       season, xp_att, xp_good, two_att, two_good

    `ytg` is yards to the goal the team with the ball attacks. `delta` (on a
    change of possession) is the new offense's yards to goal minus the old
    line of scrimmage seen from their end (100 − ytg).
    """

    decisions: pl.DataFrame
    scrimmage: pl.DataFrame
    punts: pl.DataFrame
    field_goals: pl.DataFrame
    kickoffs: pl.DataFrame
    pats: pl.DataFrame


@dataclass(frozen=True)
class Rules:
    """League rules the engine plays by. Defaults: the NFL regular season from 2025."""

    ot_seconds: float = 600.0
    ties_allowed: bool = True
    #: Two-point tries in the fourth quarter at these margins after the touchdown.
    go_for_two: tuple[int, ...] = (-2, -5, -10)


# =============================================================================
# Binning
# =============================================================================


def togo_bin(togo: np.ndarray) -> np.ndarray:
    return np.digitize(np.asarray(togo, dtype=float), [1.5, 3.5, 6.5, 10.5]).astype(np.int64)


def ytg_bin(ytg: np.ndarray) -> np.ndarray:
    return np.digitize(
        np.asarray(ytg, dtype=float), [3.5, 9.5, 19.5, 39.5, 59.5, 79.5, 89.5]
    ).astype(np.int64)


def punt_bin(ytg: np.ndarray) -> np.ndarray:
    return np.digitize(np.asarray(ytg, dtype=float), [39.5, 49.5, 59.5, 69.5, 79.5, 89.5]).astype(
        np.int64
    )


def _down_group(down: np.ndarray) -> np.ndarray:
    return np.where(down <= 2, 0, np.where(down == 3, 1, 2))


#: Situation keys from finest to coarsest: (cls, down, togo, ytg, clk) first,
#: then grouping downs, then dropping down and distance, then field position.
_POOL_LEVELS = (
    lambda c, d, t, y, k: (c, d, t, y, k),
    lambda c, d, t, y, k: (c, _down_group(d), t, y, k),
    lambda c, d, t, y, k: (c, 9, t, y, k),
    lambda c, d, t, y, k: (c, 9, 9, y, k),
    lambda c, d, t, y, k: (c, 9, 9, 9, k),
    lambda c, d, t, y, k: (c, 9, 9, 9, 9),
)


def _fine_key(c, d, t, y, k) -> np.ndarray:
    """One integer per (cls 0-1, down 1-4, togo bin 0-4, ytg bin 0-7, clk 0-2)."""
    return (((np.asarray(c) * 4 + (np.asarray(d) - 1)) * 5 + t) * 8 + y) * 3 + k


N_FINE = 2 * 4 * 5 * 8 * 3


# =============================================================================
# Fitted tables
# =============================================================================


@dataclass
class SimTables:
    classifier: HistGradientBoostingClassifier
    class_order: np.ndarray  # column of predict_proba for each of CLASSES
    # run/pass draws: concatenated pools, one range per pool, a pool per fine key
    rp: dict[str, np.ndarray]
    rp_start: np.ndarray
    rp_end: np.ndarray
    fine_pool: np.ndarray
    tilt_flat: tuple[np.ndarray, int]  # cumulative tilted weights for all θ, and the pool length
    # kneels, spikes, punts
    kneel: dict[str, np.ndarray]
    spike: dict[str, np.ndarray]
    punt: dict[str, np.ndarray]
    punt_start: np.ndarray
    punt_end: np.ndarray
    fg_coef: tuple[float, float, float]
    xp_rate: float
    two_rate: float
    kickoffs: pl.DataFrame
    #: θ added to both offenses in each quarter (overtime plays as the 4th),
    #: fitted so each quarter scores what it did in the training seasons
    #: (`fit_quarter_offsets`): real first quarters are slower than the rest
    #: of the game and second quarters faster, which the drawn plays alone
    #: do not reproduce.
    quarter_theta: np.ndarray = field(default_factory=lambda: np.zeros(4))
    _kick_pools: dict = field(default_factory=dict)

    # ---- fitting -------------------------------------------------------------

    @classmethod
    def fit(cls, ev: SimEvents, *, max_iter: int = 150, seed: int = 0) -> SimTables:
        # Decisions.
        d = ev.decisions
        x = _decision_matrix(
            d["down"].to_numpy(),
            d["togo"].to_numpy(),
            d["ytg"].to_numpy(),
            d["half_secs"].to_numpy(),
            d["game_secs"].to_numpy(),
            d["qtr"].to_numpy(),
            d["diff"].to_numpy(),
        )
        y = d["label"].to_numpy().astype(str)
        clf = HistGradientBoostingClassifier(
            max_iter=max_iter, learning_rate=0.1, random_state=seed
        )
        clf.fit(x, y)
        order = np.array(
            [list(clf.classes_).index(c) if c in clf.classes_ else -1 for c in CLASSES]
        )

        # Run/pass pools.
        s = ev.scrimmage.filter(pl.col("cls").is_in(["run", "pass"]))
        c = (s["cls"].to_numpy() == "pass").astype(np.int64)
        dn = s["down"].to_numpy().astype(np.int64)
        tb = togo_bin(s["togo"].to_numpy())
        yb = ytg_bin(s["ytg"].to_numpy())
        kk = s["clk"].to_numpy().astype(np.int64)
        cols = {k: s[k].to_numpy() for k in ("kind", "dy", "fd", "delta", "secs", "epa")}
        level_keys = [
            np.stack(
                [np.broadcast_to(np.asarray(v), c.shape) for v in lv(c, dn, tb, yb, kk)], axis=1
            )
            for lv in _POOL_LEVELS
        ]
        counts = [_count_rows(k) for k in level_keys]

        pools: dict[tuple, int] = {}
        chunks: list[np.ndarray] = []
        fine_pool = np.zeros(N_FINE, dtype=np.int64)
        for ci in (0, 1):
            for di in (1, 2, 3, 4):
                for ti in range(5):
                    for yi in range(8):
                        for ki in range(3):
                            for li, lv in enumerate(_POOL_LEVELS):
                                key = tuple(
                                    int(v)
                                    for v in lv(
                                        np.int64(ci),
                                        np.int64(di),
                                        np.int64(ti),
                                        np.int64(yi),
                                        np.int64(ki),
                                    )
                                )
                                if (
                                    counts[li].get(key, 0) >= MIN_POOL
                                    or li == len(_POOL_LEVELS) - 1
                                ):
                                    break
                            pid = pools.get((li, key))
                            if pid is None:
                                mask = np.all(level_keys[li] == np.array(key), axis=1)
                                pid = len(chunks)
                                pools[(li, key)] = pid
                                chunks.append(np.flatnonzero(mask))
                            fine_pool[_fine_key(ci, di, ti, yi, ki)] = pid
        sizes = np.array([len(ch) for ch in chunks])
        rp_end = np.cumsum(sizes)
        rp_start = rp_end - sizes
        idx = np.concatenate(chunks)
        rp = {k: v[idx] for k, v in cols.items()}
        tilt = _tilt_flat(TILT_SCALE * np.clip(rp["epa"], -EPA_CLIP, EPA_CLIP))

        def pool(frame: pl.DataFrame) -> dict[str, np.ndarray]:
            return {k: frame[k].to_numpy() for k in ("kind", "dy", "fd", "delta", "secs")}

        kneel = pool(ev.scrimmage.filter(pl.col("cls") == "kneel"))
        spike = pool(ev.scrimmage.filter(pl.col("cls") == "spike"))

        p = ev.punts
        pb = punt_bin(p["ytg"].to_numpy())
        order_p = np.argsort(pb, kind="stable")
        punt = {k: p[k].to_numpy()[order_p] for k in ("kind", "dy", "fd", "delta", "secs")}
        pb_sorted = pb[order_p]
        punt_start = np.searchsorted(pb_sorted, np.arange(7), side="left")
        punt_end = np.searchsorted(pb_sorted, np.arange(7), side="right")
        # An empty band borrows its neighbour's punts.
        for b in range(7):
            if punt_end[b] == punt_start[b]:
                nb = b + 1 if b < 6 and punt_end[b + 1] > punt_start[b + 1] else b - 1
                punt_start[b], punt_end[b] = punt_start[nb], punt_end[nb]

        fg = ev.field_goals
        dist = fg["distance"].to_numpy().astype(float)
        lr = LogisticRegression(C=1e6, max_iter=1000)
        lr.fit(np.column_stack([dist, dist**2 / 100]), fg["made"].to_numpy())
        fg_coef = (float(lr.intercept_[0]), float(lr.coef_[0][0]), float(lr.coef_[0][1]))

        pats = ev.pats
        xp_rate = float(pats["xp_good"].sum() / max(pats["xp_att"].sum(), 1))
        two_rate = float(pats["two_good"].sum() / max(pats["two_att"].sum(), 1))

        return cls(
            classifier=clf,
            class_order=order,
            rp=rp,
            rp_start=rp_start,
            rp_end=rp_end,
            fine_pool=fine_pool,
            tilt_flat=tilt,
            kneel=kneel,
            spike=spike,
            punt=punt,
            punt_start=punt_start,
            punt_end=punt_end,
            fg_coef=fg_coef,
            xp_rate=xp_rate,
            two_rate=two_rate,
            kickoffs=ev.kickoffs,
        )

    # ---- kickoffs, point in time --------------------------------------------

    def kick_pool(self, season: int, week: int, minimum: int = 150) -> int:
        """Id of the kickoffs known before `week`: this season's, or last season's when too few."""
        key = (season, week)
        if key in self._kick_pools:
            return self._kick_pools[key][0]
        k = self.kickoffs
        now = k.filter((pl.col("season") == season) & (pl.col("week") < week))
        if now.height < minimum:
            prev = k.filter(pl.col("season") < season)
            last = prev["season"].max() if prev.height else None
            now = prev.filter(pl.col("season") == last) if last is not None else k
        pid = len(self._kick_pools)
        self._kick_pools[key] = (
            pid,
            {c: now[c].to_numpy() for c in ("kind", "ytg", "secs")},
        )
        return pid

    def _kick_arrays(self) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]:
        items = sorted(self._kick_pools.values(), key=lambda v: v[0])
        sizes = np.array([len(v[1]["kind"]) for v in items])
        end = np.cumsum(sizes)
        start = end - sizes
        arrays = {c: np.concatenate([v[1][c] for v in items]) for c in ("kind", "ytg", "secs")}
        return arrays, start, end

    def fg_prob(self, distance: np.ndarray) -> np.ndarray:
        a, b, c = self.fg_coef
        z = a + b * distance + c * distance**2 / 100
        return 1 / (1 + np.exp(-z))

    def choose(self, x: np.ndarray, u: np.ndarray) -> np.ndarray:
        proba = self.classifier.predict_proba(x)
        cols = np.column_stack(
            [proba[:, j] if j >= 0 else np.zeros(len(x)) for j in self.class_order]
        )
        cum = np.cumsum(cols, axis=1)
        cum /= cum[:, -1:]
        return (u[:, None] > cum).sum(axis=1).clip(0, len(CLASSES) - 1)


def _count_rows(keys: np.ndarray) -> dict[tuple, int]:
    uniq, counts = np.unique(keys, axis=0, return_counts=True)
    return {tuple(int(v) for v in row): int(n) for row, n in zip(uniq, counts, strict=True)}


def _tilt_flat(epa: np.ndarray) -> tuple[np.ndarray, int]:
    """Cumulative weights exp(θ·epa) for every θ in THETA_GRID, laid end to end with offsets."""
    m = len(epa)
    cums = []
    for th in THETA_GRID:
        w = np.exp(th * epa)
        cums.append(np.concatenate([[0.0], np.cumsum(w)]))
    offset = 2.0 * max(c[-1] for c in cums)
    return np.concatenate([c + i * offset for i, c in enumerate(cums)]), m


def _decision_matrix(down, togo, ytg, half_secs, game_secs, qtr, diff) -> np.ndarray:
    return np.column_stack(
        [
            np.asarray(down, dtype=float),
            np.clip(np.asarray(togo, dtype=float), 1, 30),
            np.asarray(ytg, dtype=float),
            np.asarray(half_secs, dtype=float),
            np.asarray(game_secs, dtype=float),
            np.asarray(qtr, dtype=float),
            np.clip(np.asarray(diff, dtype=float), -28, 28),
        ]
    )


# =============================================================================
# Simulation
# =============================================================================


@dataclass
class Start:
    """Where each simulated game starts, one row per simulation.

    A pregame row is gsr=3600, phase=PHASE_KICK with `kicker` the team kicking
    off and `recv2` the other one (+1 home). An in-game row is a snap:
    phase=PHASE_SCRIM with pos/down/togo/ytg.
    """

    gsr: np.ndarray
    pos: np.ndarray
    down: np.ndarray
    togo: np.ndarray
    ytg: np.ndarray
    home: np.ndarray
    away: np.ndarray
    phase: np.ndarray
    kicker: np.ndarray
    recv2: np.ndarray
    theta_home: np.ndarray  # continuous θ per row
    theta_away: np.ndarray
    kick_pool: np.ndarray  # SimTables.kick_pool id per row


@dataclass
class Result:
    home: np.ndarray
    away: np.ndarray
    quarters: np.ndarray  # (rows, 4, 2) points scored in each quarter, home then away
    overtime: np.ndarray  # (rows, 2)
    steps: int
    unfinished: int


def pregame(n: int, rng: np.random.Generator, theta_home, theta_away, kick_pool) -> Start:
    """n games from the opening kickoff, the coin toss at random."""
    recv = np.where(rng.random(n) < 0.5, 1, -1).astype(np.int8)
    z = np.zeros(n)
    return Start(
        gsr=np.full(n, 3600.0),
        pos=recv.copy(),
        down=np.ones(n, np.int8),
        togo=np.full(n, 10.0),
        ytg=np.full(n, 70.0),
        home=z.astype(np.int64),
        away=z.astype(np.int64),
        phase=np.full(n, PHASE_KICK, np.int8),
        kicker=(-recv).astype(np.int8),
        recv2=(-recv).astype(np.int8),
        theta_home=np.broadcast_to(np.asarray(theta_home, float), (n,)).copy(),
        theta_away=np.broadcast_to(np.asarray(theta_away, float), (n,)).copy(),
        kick_pool=np.broadcast_to(np.asarray(kick_pool, np.int64), (n,)).copy(),
    )


def concat(starts: Sequence[Start]) -> Start:
    """Several Start blocks as one, in order, so many games share a simulate() call."""
    return Start(
        **{f: np.concatenate([getattr(s, f) for s in starts]) for f in Start.__dataclass_fields__}
    )


def snap_start(
    k: int, *, gsr, pos, down, togo, ytg, home, away, recv2, theta_home, theta_away, kick_pool
) -> Start:
    """k games from one snap of a game in progress (θ given per row, so it can carry noise)."""

    def full(v, dtype=float):
        return np.full(k, v, dtype=dtype)

    return Start(
        gsr=full(gsr),
        pos=full(pos, np.int8),
        down=full(down, np.int8),
        togo=full(togo),
        ytg=full(ytg),
        home=full(home, np.int64),
        away=full(away, np.int64),
        phase=full(PHASE_SCRIM, np.int8),
        kicker=full(-pos, np.int8),
        recv2=full(recv2, np.int8),
        theta_home=np.asarray(theta_home, float),
        theta_away=np.asarray(theta_away, float),
        kick_pool=full(kick_pool, np.int64),
    )


def _theta_index(theta: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Round θ to the grid, up or down at random in proportion: unbiased on average."""
    step = THETA_GRID[1] - THETA_GRID[0]
    pos = (np.clip(theta, THETA_GRID[0], THETA_GRID[-1]) - THETA_GRID[0]) / step
    lo = np.floor(pos)
    up = rng.random(len(pos)) < (pos - lo)
    return np.clip(lo + up, 0, len(THETA_GRID) - 1).astype(np.int64)


NFL = Rules()


def simulate(
    tables: SimTables, start: Start, rng: np.random.Generator, rules: Rules = NFL
) -> Result:
    n = len(start.gsr)
    gsr = start.gsr.astype(float).copy()
    pos = start.pos.astype(np.int64).copy()
    down = start.down.astype(np.int64).copy()
    togo = start.togo.astype(float).copy()
    ytg = start.ytg.astype(float).copy()
    hp = start.home.astype(np.int64).copy()
    ap = start.away.astype(np.int64).copy()
    phase = start.phase.astype(np.int64).copy()
    kicker = start.kicker.astype(np.int64).copy()
    recv2 = start.recv2.astype(np.int64).copy()
    th_h = start.theta_home.astype(float)
    th_a = start.theta_away.astype(float)
    kpool = start.kick_pool.astype(np.int64)
    quarters = np.zeros((n, 4, 2), dtype=np.int64)
    overtime = np.zeros((n, 2), dtype=np.int64)
    pat_team = np.zeros(n, np.int64)
    score_q = np.zeros(n, np.int64)  # 0-3 quarter, 4 overtime
    pending = np.zeros(n, np.int64)  # 1 half over, 2 regulation over, 3 overtime period over
    in_ot = np.zeros(n, bool)
    ots = np.zeros(n)
    ot_first = np.zeros(n, np.int64)
    ot_n = np.zeros(n, np.int64)
    last_pos = np.zeros(n, np.int64)
    done = np.zeros(n, bool)

    k_arr, k_start, k_end = tables._kick_arrays()
    tilt_flat, m = tables.tilt_flat
    stride = m + 1

    def quarter_of(i):
        return np.where(in_ot[i], 4, np.clip((3600 - gsr[i]) // 900, 0, 3).astype(np.int64))

    def add(i, team, pts, q):
        h = team == 1
        hp[i] += np.where(h, pts, 0)
        ap[i] += np.where(h, 0, pts)
        col = np.where(h, 0, 1)
        reg = q < 4
        np.add.at(quarters, (i[reg], q[reg], col[reg]), pts[reg])
        np.add.at(overtime, (i[~reg], col[~reg]), pts[~reg])

    def ot_score_check(i):
        """After a score in overtime: is the game over? (2025 rules: both teams get the ball.)"""
        i = i[in_ot[i]]
        if not len(i):
            return
        lead = np.sign(hp[i] - ap[i])
        over = ((ot_n[i] >= 3) & (lead != 0)) | ((ot_n[i] == 2) & (lead == -ot_first[i]))
        done[i[over]] = True

    def advance_clock(i, secs):
        if not len(i):
            return
        ot = in_ot[i]
        r = i[~ot]
        if len(r):
            q = np.clip((3600 - gsr[r]) // 900, 0, 3)
            bound = 3600 - 900 * (q + 1)
            new = gsr[r] - secs[~ot]
            crossed = new <= bound
            gsr[r] = np.maximum(new, bound)
            pending[r[crossed & (bound == 1800)]] = 1
            pending[r[crossed & (bound == 0)]] = 2
        o = i[ot]
        if len(o):
            ots[o] -= secs[ot]
            pending[o[ots[o] <= 0]] = 3

    def touchdown(i, team):
        q = quarter_of(i)
        add(i, team, np.full(len(i), 6), q)
        phase[i] = PHASE_PAT
        pat_team[i] = team
        score_q[i] = q
        ot_score_check(i)

    def give(i, team, new_ytg):
        pos[i] = team
        ytg[i] = np.clip(new_ytg, 1, 99)
        down[i] = 1
        togo[i] = np.minimum(10, ytg[i])
        phase[i] = PHASE_SCRIM

    steps = 0
    while steps < MAX_STEPS:
        live = ~done
        if not live.any():
            break
        steps += 1
        kick = np.flatnonzero(live & (phase == PHASE_KICK))
        pat = np.flatnonzero(live & (phase == PHASE_PAT))
        scrim = np.flatnonzero(live & (phase == PHASE_SCRIM))

        # ---- kickoffs ------------------------------------------------------
        if len(kick):
            pool = kpool[kick]
            j = k_start[pool] + (rng.random(len(kick)) * (k_end[pool] - k_start[pool])).astype(
                np.int64
            )
            kind = k_arr["kind"][j]
            ky = k_arr["ytg"][j].astype(float)
            kt = kicker[kick]
            rec = kind == KICK_RECEIVED
            give(kick[rec], -kt[rec], ky[rec])
            ons = kind == KICK_ONSIDE
            give(kick[ons], kt[ons], ky[ons])
            rtd = kind == KICK_RETURN_TD
            if rtd.any():
                touchdown(kick[rtd], -kt[rtd])
            ktd = kind == KICK_TEAM_TD
            if ktd.any():
                touchdown(kick[ktd], kt[ktd])
            advance_clock(kick, k_arr["secs"][j].astype(float))

        # ---- tries after a touchdown ----------------------------------------
        if len(pat):
            team = pat_team[pat]
            margin = (hp[pat] - ap[pat]) * team
            late = score_q[pat] >= 3
            two = late & np.isin(margin, rules.go_for_two)
            p = np.where(two, tables.two_rate, tables.xp_rate)
            good = rng.random(len(pat)) < p
            add(pat[good], team[good], np.where(two[good], 2, 1), score_q[pat[good]])
            phase[pat] = PHASE_KICK
            kicker[pat] = team
            ot_score_check(pat[good])

        # ---- snaps ------------------------------------------------------------
        if len(scrim):
            s = scrim
            in_o = in_ot[s]
            reg_gsr = gsr[s]
            half_secs = np.where(in_o, ots[s], np.where(reg_gsr > 1800, reg_gsr - 1800, reg_gsr))
            game_secs = np.where(in_o, ots[s], reg_gsr)
            qtr = np.where(in_o, 4, np.clip((3600 - reg_gsr) // 900, 0, 3) + 1)
            diff = (hp[s] - ap[s]) * pos[s]
            x = _decision_matrix(down[s], togo[s], ytg[s], half_secs, game_secs, qtr, diff)
            choice = tables.choose(x, rng.random(len(s)))
            # A kick from beyond 65 yards is a punt.
            choice = np.where((choice == FG) & (ytg[s] > 47), PUNT, choice)
            secs = np.zeros(len(s))
            kind = np.full(len(s), -1, np.int64)
            dy = np.zeros(len(s))
            fd = np.zeros(len(s), bool)
            delta = np.zeros(len(s))

            rp = (choice == RUN) | (choice == PASS)
            if rp.any():
                r = np.flatnonzero(rp)
                # Overtime is played at the pace of a two-minute drill.
                clk = np.where(
                    in_o[r], CLK_HURRY, clock_state(qtr[r], half_secs[r], game_secs[r], diff[r])
                )
                fk = _fine_key(
                    (choice[r] == PASS).astype(np.int64),
                    np.clip(down[s[r]], 1, 4),
                    togo_bin(togo[s[r]]),
                    ytg_bin(ytg[s[r]]),
                    clk,
                )
                pid = tables.fine_pool[fk]
                st, en = tables.rp_start[pid], tables.rp_end[pid]
                theta = (
                    np.where(pos[s[r]] == 1, th_h[s[r]], th_a[s[r]])
                    + tables.quarter_theta[qtr[r].astype(np.int64) - 1]
                )
                t = _theta_index(theta, rng)
                base = t * stride
                lo = tilt_flat[base + st]
                hi = tilt_flat[base + en]
                target = lo + rng.random(len(r)) * (hi - lo)
                j = np.searchsorted(tilt_flat, target, side="right") - 1 - base
                j = np.clip(j, st, en - 1)
                for name, arr in (
                    ("kind", kind),
                    ("dy", dy),
                    ("fd", fd),
                    ("delta", delta),
                    ("secs", secs),
                ):
                    arr[r] = tables.rp[name][j]

            for code, src in ((KNEEL, tables.kneel), (SPIKE, tables.spike)):
                c = np.flatnonzero(choice == code)
                if len(c) and len(src["kind"]):
                    j = (rng.random(len(c)) * len(src["kind"])).astype(np.int64)
                    for name, arr in (
                        ("kind", kind),
                        ("dy", dy),
                        ("fd", fd),
                        ("delta", delta),
                        ("secs", secs),
                    ):
                        arr[c] = src[name][j]

            pu = np.flatnonzero(choice == PUNT)
            if len(pu):
                b = punt_bin(ytg[s[pu]])
                st, en = tables.punt_start[b], tables.punt_end[b]
                j = st + (rng.random(len(pu)) * (en - st)).astype(np.int64)
                for name, arr in (
                    ("kind", kind),
                    ("dy", dy),
                    ("fd", fd),
                    ("delta", delta),
                    ("secs", secs),
                ):
                    arr[pu] = tables.punt[name][j]

            fgr = np.flatnonzero(choice == FG)
            secs[fgr] = 5.0

            # Field goals.
            if len(fgr):
                i = s[fgr]
                made = rng.random(len(fgr)) < tables.fg_prob(ytg[i] + 18)
                mi = i[made]
                if len(mi):
                    add(mi, pos[mi], np.full(len(mi), 3), quarter_of(mi))
                    phase[mi] = PHASE_KICK
                    kicker[mi] = pos[mi]
                    ot_score_check(mi)
                xi = i[~made]
                if len(xi):
                    give(xi, -pos[xi], np.minimum(80, 92 - ytg[xi]))

            # Everything drawn from a pool.
            drawn = kind >= 0
            i_all = s[drawn]
            k_all = kind[drawn]
            # Continuing plays may still score, concede a safety or fail on downs.
            cont = k_all == KIND_CONT
            ci = i_all[cont]
            new = ytg[ci] - dy[drawn][cont]
            td = new <= 0
            sf = new >= 100
            ok = ~td & ~sf
            if td.any():
                touchdown(ci[td], pos[ci[td]])
            if sf.any():
                si = ci[sf]
                add(si, -pos[si], np.full(len(si), 2), quarter_of(si))
                ot_score_check(si)
                give(si, -pos[si], np.full(len(si), 60.0))
            oi = ci[ok]
            if len(oi):
                g = dy[drawn][cont][ok]
                first = fd[drawn][cont][ok] | (g >= togo[oi])
                ny = new[ok]
                down[oi] = np.where(first, 1, down[oi] + 1)
                togo[oi] = np.where(first, np.minimum(10, ny), np.maximum(1, togo[oi] - g))
                ytg[oi] = ny
                fail = down[oi] > 4
                if fail.any():
                    fi = oi[fail]
                    give(fi, -pos[fi], 100 - ytg[fi])
            tdo = i_all[k_all == KIND_TD_OFF]
            if len(tdo):
                touchdown(tdo, pos[tdo])
            tdd = i_all[k_all == KIND_TD_DEF]
            if len(tdd):
                touchdown(tdd, -pos[tdd])
            sfi = i_all[k_all == KIND_SAFETY]
            if len(sfi):
                add(sfi, -pos[sfi], np.full(len(sfi), 2), quarter_of(sfi))
                ot_score_check(sfi)
                give(sfi, -pos[sfi], np.full(len(sfi), 60.0))
            to = k_all == KIND_TURNOVER
            ti = i_all[to]
            if len(ti):
                give(ti, -pos[ti], 100 - ytg[ti] + delta[drawn][to])

            advance_clock(s, secs)

        # ---- overtime possessions --------------------------------------------
        o = np.flatnonzero(~done & in_ot & (phase == PHASE_SCRIM) & (pos != last_pos))
        if len(o):
            ot_n[o] += 1
            last_pos[o] = pos[o]
            over = (ot_n[o] >= 3) & (hp[o] != ap[o])
            done[o[over]] = True

        # ---- ends of halves, regulation and overtime periods --------------------
        e = np.flatnonzero(~done & (pending > 0) & (phase != PHASE_PAT))
        if len(e):
            half = e[pending[e] == 1]
            phase[half] = PHASE_KICK
            kicker[half] = -recv2[half]
            reg = e[pending[e] == 2]
            tied = hp[reg] == ap[reg]
            done[reg[~tied]] = True
            t = reg[tied]
            if len(t):
                in_ot[t] = True
                ots[t] = rules.ot_seconds
                first = np.where(rng.random(len(t)) < 0.5, 1, -1)
                ot_first[t] = first
                ot_n[t] = 0
                last_pos[t] = 0
                phase[t] = PHASE_KICK
                kicker[t] = -first
            oe = e[pending[e] == 3]
            if len(oe):
                if rules.ties_allowed:
                    done[oe] = True
                else:
                    lead = hp[oe] != ap[oe]
                    done[oe[lead]] = True
                    ots[oe[~lead]] = 900.0
            pending[e] = 0

    return Result(hp, ap, quarters, overtime, steps, int((~done).sum()))


# =============================================================================
# Team strength: θ from a projected score
# =============================================================================


@dataclass(frozen=True)
class StrengthCurve:
    """Points a team scores as a smooth function of its θ and its opponent's."""

    coef: tuple[float, ...]  # 1, s, o, s², o², s·o

    def shifted(self, points: float) -> StrengthCurve:
        """The same curve, every team scoring `points` more (a different kickoff environment)."""
        return StrengthCurve((self.coef[0] + points, *self.coef[1:]))

    def points(self, s: np.ndarray, o: np.ndarray) -> np.ndarray:
        c = self.coef
        return c[0] + c[1] * s + c[2] * o + c[3] * s * s + c[4] * o * o + c[5] * s * o


def fit_strength_curve(
    tables: SimTables,
    kick_pool: int,
    rng: np.random.Generator,
    grid: Sequence[float] = (-0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75),
    sims: int = 1500,
) -> StrengthCurve:
    """Simulate every pair of θ on a grid from kickoff and fit points ~ quadratic in (θ, θ_opp)."""
    s_list, o_list, pts = [], [], []
    th_h = np.repeat(np.array(grid), len(grid) * sims)
    th_a = np.tile(np.repeat(np.array(grid), sims), len(grid))
    res = simulate(tables, pregame(len(th_h), rng, th_h, th_a, kick_pool), rng)
    for hi, h in enumerate(grid):
        for ai, a in enumerate(grid):
            sl = slice((hi * len(grid) + ai) * sims, (hi * len(grid) + ai + 1) * sims)
            s_list += [h, a]
            o_list += [a, h]
            pts += [res.home[sl].mean(), res.away[sl].mean()]
    s = np.array(s_list)
    o = np.array(o_list)
    x = np.column_stack([np.ones_like(s), s, o, s * s, o * o, s * o])
    coef, *_ = np.linalg.lstsq(x, np.array(pts), rcond=None)
    return StrengthCurve(tuple(float(c) for c in coef))


def fit_quarter_offsets(
    tables: SimTables,
    kick_pool: int,
    real_quarters: Sequence[float],
    rng: np.random.Generator,
    sims: int = 20000,
    rounds: int = 3,
    step: float = 0.1,
) -> np.ndarray:
    """θ per quarter so even teams' simulated points per quarter match the training seasons.

    `real_quarters`: average points (both teams) scored in each quarter of a
    training-season game. One finite-difference slope per quarter, then a few
    Newton rounds. Sets and returns `tables.quarter_theta`.
    """
    target = np.asarray(real_quarters, dtype=float)

    def per_quarter() -> np.ndarray:
        res = simulate(tables, pregame(sims, rng, 0.0, 0.0, kick_pool), rng)
        return res.quarters.sum(axis=2).mean(axis=0)

    base = tables.quarter_theta.copy()
    now = per_quarter()
    tables.quarter_theta = base + step
    slope = np.maximum((per_quarter() - now) / step, 1e-3)
    tables.quarter_theta = base
    for _ in range(rounds):
        tables.quarter_theta = np.clip(
            tables.quarter_theta + (target - now) / slope, THETA_GRID[0] / 2, THETA_GRID[-1] / 2
        )
        now = per_quarter()
    return tables.quarter_theta


def solve_thetas(
    curve: StrengthCurve, home_points: float, away_points: float
) -> tuple[float, float]:
    """(θ_home, θ_away) whose simulated means are the projected points (Newton, a few steps)."""
    th = np.zeros(2)
    lo, hi = THETA_GRID[0], THETA_GRID[-1]
    target = np.array([home_points, away_points])
    c = curve.coef
    for _ in range(25):
        h, a = th
        f = np.array([curve.points(h, a), curve.points(a, h)]) - target
        j = np.array(
            [
                [c[1] + 2 * c[3] * h + c[5] * a, c[2] + 2 * c[4] * a + c[5] * h],
                [c[2] + 2 * c[4] * h + c[5] * a, c[1] + 2 * c[3] * a + c[5] * h],
            ]
        )
        try:
            step = np.linalg.solve(j, f)
        except np.linalg.LinAlgError:
            break
        th = np.clip(th - step, lo, hi)
        if np.abs(step).max() < 1e-6:
            break
    return float(th[0]), float(th[1])
