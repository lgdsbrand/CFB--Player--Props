"""The game simulator (phase S1): the engine's rules, team strength, and its backtest helpers."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from worker.adapters.nflverse.sim_plays import _scrimmage_outcomes
from worker.core import play_sim as ps
from worker.core import sim_backtest as sb


def synthetic_events(seed: int = 0, n: int = 6000) -> ps.SimEvents:
    """A small invented league: plays gain yards, better plays carry more EPA."""
    rng = np.random.default_rng(seed)
    down = rng.integers(1, 5, n)
    togo = rng.integers(1, 16, n).astype(float)
    ytg = rng.integers(1, 100, n).astype(float)
    half = rng.uniform(0, 1800, n)
    game = half + rng.choice([0, 1800], n)
    qtr = np.clip(4 - (game // 900), 1, 4).astype(int)
    diff = rng.integers(-14, 15, n).astype(float)
    label = np.where(
        down == 4,
        np.where(ytg <= 35, "fg", "punt"),
        np.where(rng.random(n) < 0.55, "pass", "run"),
    )
    decisions = pl.DataFrame(
        {"down": down, "togo": togo, "ytg": ytg, "half_secs": half, "game_secs": game,
         "qtr": qtr, "diff": diff, "label": label}
    )
    dy = np.round(rng.normal(4, 6, n))
    dy[rng.random(n) < 0.02] = 60
    kind = np.where(dy >= ytg, ps.KIND_TD_OFF, ps.KIND_CONT)
    kind[rng.random(n) < 0.02] = ps.KIND_TURNOVER
    scrimmage = pl.DataFrame(
        {
            "cls": np.where(rng.random(n) < 0.55, "pass", "run"),
            "down": down.astype(np.int8), "togo": togo, "ytg": ytg,
            "clk": rng.integers(0, 3, n).astype(np.int8), "kind": kind.astype(np.int8), "dy": dy,
            "fd": dy >= togo, "delta": np.zeros(n), "secs": rng.uniform(5, 40, n),
            "epa": dy / 5 - 0.6,
        }
    )
    kneel = pl.DataFrame(
        {"cls": ["kneel"] * 20, "down": np.ones(20, np.int8), "togo": np.full(20, 10.0),
         "ytg": np.full(20, 70.0), "clk": np.full(20, 2, np.int8), "kind": np.zeros(20, np.int8),
         "dy": np.full(20, -1.0), "fd": np.zeros(20, bool), "delta": np.zeros(20),
         "secs": np.full(20, 40.0), "epa": np.zeros(20)}
    )
    m = 600
    punts = pl.DataFrame(
        {"ytg": rng.integers(40, 100, m).astype(float), "kind": np.full(m, ps.KIND_TURNOVER, np.int8),
         "dy": np.zeros(m), "fd": np.zeros(m, bool), "delta": np.full(m, 40.0), "secs": np.full(m, 8.0)}
    )
    dist = rng.uniform(20, 60, m)
    fgs = pl.DataFrame({"distance": dist, "made": (rng.random(m) < 1.3 - dist / 60).astype(np.int8)})
    kickoffs = pl.DataFrame(
        {"season": np.full(m, 2025), "week": rng.integers(1, 10, m),
         "kind": np.full(m, ps.KICK_RECEIVED, np.int8), "ytg": np.full(m, 70.0), "secs": np.full(m, 5.0)}
    )
    pats = pl.DataFrame({"season": [2025], "xp_att": [100], "xp_good": [95], "two_att": [20], "two_good": [10]})
    return ps.SimEvents(decisions, pl.concat([scrimmage, kneel]), punts, fgs, kickoffs, pats)


@pytest.fixture(scope="module")
def tables() -> ps.SimTables:
    return ps.SimTables.fit(synthetic_events(), max_iter=30)


def test_clock_state_hurries_at_the_end_of_a_half_and_milks_a_late_lead():
    st = ps.clock_state(
        np.array([1, 2, 4, 4, 4]), np.array([600, 100, 600, 200, 200]),
        np.array([2400, 1900, 600, 200, 200]), np.array([0, 7, 0, -3, 3]),
    )
    assert st.tolist() == [ps.CLK_NORMAL, ps.CLK_HURRY, ps.CLK_NORMAL, ps.CLK_HURRY, ps.CLK_MILK]


def test_bins_cover_the_field():
    assert ps.ytg_bin(np.array([1, 3, 4, 50, 99])).tolist() == [0, 0, 1, 4, 7]
    assert ps.togo_bin(np.array([1, 2, 5, 10, 25])).tolist() == [0, 1, 2, 3, 4]


def test_every_situation_has_a_pool_of_past_plays(tables):
    sizes = tables.rp_end - tables.rp_start
    assert (sizes[tables.fine_pool] > 0).all()


def test_games_finish_and_the_quarters_add_up_to_the_score(tables):
    rng = np.random.default_rng(1)
    kp = tables.kick_pool(2025, 5)
    res = ps.simulate(tables, ps.pregame(3000, rng, 0.0, 0.0, kp), rng)
    assert res.unfinished == 0
    assert (res.home == res.quarters[:, :, 0].sum(axis=1) + res.overtime[:, 0]).all()
    assert (res.away == res.quarters[:, :, 1].sum(axis=1) + res.overtime[:, 1]).all()
    assert (res.home >= 0).all() and (res.away >= 0).all()
    # A team's score is made of 2s, 3s, 6s, 7s and 8s: never exactly 1.
    assert not ((res.home == 1) | (res.away == 1)).any()


def test_a_stronger_offense_scores_more(tables):
    rng = np.random.default_rng(2)
    kp = tables.kick_pool(2025, 5)
    weak = ps.simulate(tables, ps.pregame(3000, rng, -0.4, 0.0, kp), rng)
    strong = ps.simulate(tables, ps.pregame(3000, rng, 0.4, 0.0, kp), rng)
    assert strong.home.mean() > weak.home.mean() + 2


def test_a_game_tied_at_the_end_of_regulation_goes_to_overtime(tables):
    rng = np.random.default_rng(3)
    kp = tables.kick_pool(2025, 5)
    start = ps.snap_start(
        2000, gsr=5.0, pos=1, down=4, togo=10.0, ytg=95.0, home=10, away=10, recv2=1,
        theta_home=np.zeros(2000), theta_away=np.zeros(2000), kick_pool=kp,
    )
    res = ps.simulate(tables, start, rng)
    assert (res.overtime.sum(axis=1) > 0).mean() > 0.3
    no_ties = ps.simulate(tables, start, rng, ps.Rules(ties_allowed=False))
    assert (no_ties.home != no_ties.away).all()


def test_a_game_already_over_keeps_its_score(tables):
    rng = np.random.default_rng(4)
    kp = tables.kick_pool(2025, 5)
    start = ps.snap_start(
        500, gsr=1.0, pos=1, down=1, togo=10.0, ytg=80.0, home=24, away=3, recv2=1,
        theta_home=np.zeros(500), theta_away=np.zeros(500), kick_pool=kp,
    )
    res = ps.simulate(tables, start, rng)
    assert (res.home - res.away > 0).mean() > 0.99


def test_kickoffs_are_point_in_time(tables):
    assert tables.kick_pool(2025, 5) == tables.kick_pool(2025, 5)
    _, arrays = tables._kick_pools[(2025, 5)]
    assert len(arrays["kind"]) == (tables.kickoffs.filter(pl.col("week") < 5).height)


def test_theta_rounding_is_unbiased():
    rng = np.random.default_rng(5)
    idx = ps._theta_index(np.full(200_000, 0.0125), rng)
    assert ps.THETA_GRID[idx].mean() == pytest.approx(0.0125, abs=1e-3)


def test_solve_thetas_hits_the_projected_points():
    curve = ps.StrengthCurve((22.0, 30.0, -10.0, 5.0, 3.0, -2.0))
    h, a = ps.solve_thetas(curve, 27.0, 20.0)
    assert curve.points(h, a) == pytest.approx(27.0, abs=1e-4)
    assert curve.points(a, h) == pytest.approx(20.0, abs=1e-4)
    assert curve.shifted(1.5).points(0.0, 0.0) == pytest.approx(23.5)


# ---- the adapter reads outcomes from the next snap ---------------------------------


def _rows(**cols) -> pl.DataFrame:
    base = {
        "cls": ["run"], "posteam": ["KC"], "defteam": ["BUF"], "yardline_100": [60.0], "n_pos": ["KC"],
        "n_ytg": [52.0], "n_type": ["pass"], "n_down": [1.0], "yards_gained": [8.0], "ydstogo": [5.0],
        "touchdown": [0.0], "td_team": [None], "safety": [0.0], "interception": [0.0],
        "fumble_lost": [0.0], "play_type": ["run"], "down": [2.0], "clk": [0], "secs": [30.0],
        "epa": [0.5],
    }
    base.update(cols)
    return pl.DataFrame(base)


def test_a_gain_is_read_from_the_next_snap():
    out = _scrimmage_outcomes(_rows())
    assert out["kind"][0] == ps.KIND_CONT and out["dy"][0] == 8 and out["fd"][0]


def test_a_failed_fourth_down_is_measured_into_the_other_frame():
    out = _scrimmage_outcomes(_rows(down=[4.0], n_pos=["BUF"], n_ytg=[42.0], n_down=[1.0], yards_gained=[2.0]))
    # KC snapped at 60 to go; BUF next snaps 42 from KC's end zone, i.e. KC's 58: two yards gained.
    assert out["kind"][0] == ps.KIND_CONT and out["dy"][0] == 2 and not out["fd"][0]


def test_a_punt_is_a_change_of_possession_with_its_net():
    out = _scrimmage_outcomes(
        _rows(cls=["punt"], play_type=["punt"], yardline_100=[70.0], n_pos=["BUF"], n_ytg=[70.0], down=[4.0])
    )
    assert out["kind"][0] == ps.KIND_TURNOVER and out["delta"][0] == 40


def test_touchdowns_are_credited_to_the_side_that_scored():
    off = _scrimmage_outcomes(_rows(touchdown=[1.0], td_team=["KC"]))
    pick6 = _scrimmage_outcomes(_rows(touchdown=[1.0], td_team=["BUF"], interception=[1.0]))
    assert off["kind"][0] == ps.KIND_TD_OFF and pick6["kind"][0] == ps.KIND_TD_DEF


# ---- backtest helpers ----------------------------------------------------------------


def test_smoothing_never_says_certain():
    p = sb.smooth(np.array([0.0, 1.0, 0.5]), 1000)
    assert 0 < p[0] < 0.001 and 0.999 < p[1] < 1 and p[2] == pytest.approx(0.5)


def test_a_book_style_line_is_the_half_point_at_or_below():
    assert sb.half_line(np.array([9.2, 9.9, 21.0])).tolist() == [9.5, 9.5, 21.5]


def test_scales_sharpen_an_underconfident_win_percentage():
    rng = np.random.default_rng(6)
    true = rng.uniform(0.05, 0.95, 4000)
    won = rng.random(4000) < true
    logit = np.log(true / (1 - true))
    timid = 1 / (1 + np.exp(-0.5 * logit))  # says 60% when it is 69%
    frame = pl.DataFrame(
        {"elapsed": np.zeros(4000, int), "p_home_win": timid, "final_margin": np.where(won, 3, -3)}
    )
    scales = sb.fit_scales(frame, 10_000)
    assert scales[0] == pytest.approx(2.0, rel=0.15)


def test_every_period_market_is_graded_for_both_methods(tables):
    rng = np.random.default_rng(16)
    res = ps.simulate(tables, ps.pregame(600, rng, np.zeros(600), np.zeros(600), tables.whole_kick_pool()), rng)
    s = sb.summarize(res, 200)
    pm, pt = np.array([3.0, -2.0, 0.5]), np.array([44.0, 40.0, 47.0])
    base = sb.PeriodBaseline.fit(pm, pt, pt * 0.2 + [1, 0, -1], pt * 0.5 + [2, -1, 0], pm * 0.6 + [1, 0, -1], pm * 0.3 + [1, -1, 0])
    assert sb.PeriodBaseline.from_dict(base.to_dict()).q1_margin_share == pytest.approx(
        base.q1_margin_share, abs=1e-5
    )
    actual = {
        "q1_total": np.array([10.0, 7.0, 3.0]),
        "h1_total": np.array([24.0, 17.0, 20.0]),
        "h1_margin": np.array([7.0, -3.0, 0.0]),
        "q1_margin": np.array([3.0, 0.0, -7.0]),
    }
    rows = sb.period_scores(sb.period_rows(s, base, pm, pt, actual))
    markets = {(r["market"], r["method"]) for r in rows}
    assert len(markets) == 12 and {"1st quarter spread", "1st quarter winner"} <= {
        m for m, _ in markets
    }
    winners = [r for r in rows if r["market"].endswith("winner")]
    assert all(r["n"] == 2 for r in winners), "a period tied at its end is a push, not graded"
