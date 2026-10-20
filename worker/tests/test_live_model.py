"""The live model (phase L1): clock, scores, expected points, fitting, projection."""

from __future__ import annotations

import math

import numpy as np
import polars as pl
import pytest

from worker.core import live_backtest as lb
from worker.core.live_model import (
    Coefficients,
    EpTable,
    GameState,
    LiveParams,
    Prior,
    elapsed_seconds,
    interval,
    project,
)

HOME, AWAY = 1, 2
FINAL = {10: lb.Final(10, 2025, HOME, AWAY, 14, 3)}


def flat_params(margin_k: float = 1.0, total_k: float = 1.0, scale: float = 0.5) -> LiveParams:
    params = LiveParams("A")
    for e in (0, 1800, 3540):
        params.margin[e] = Coefficients({"prior": margin_k, "ep": 0.0}, scale)
        params.total[e] = Coefficients({"prior": total_k, "ep": 0.0}, scale)
    return params


def test_elapsed_counts_regulation_from_quarter_clocks():
    assert elapsed_seconds(1, 900) == 0
    assert elapsed_seconds(2, 0) == 1800
    assert elapsed_seconds(4, 60) == 3540
    assert elapsed_seconds(5, 600) == 3900  # overtime runs past regulation
    assert elapsed_seconds(None, 600) is None


def test_projection_at_kickoff_is_the_prior():
    prior = Prior(margin_mean=7.0, margin_sd=14.0, total_mean=48.0, total_sd=13.0)
    p = project(prior, GameState(0, 0, 0), flat_params(), EpTable({}))
    assert p.margin_mean == pytest.approx(7.0)
    assert p.total_mean == pytest.approx(48.0)
    assert (p.home_score, p.away_score) == pytest.approx((27.5, 20.5))
    assert 0.5 < p.p_home_win < 1


def test_the_prior_fades_with_the_clock_and_the_score_takes_over():
    prior = Prior(7.0, 14.0, 48.0, 13.0)
    half = project(prior, GameState(1800, 10, 17), flat_params(), EpTable({}))
    assert half.margin_mean == pytest.approx(-7 + 3.5)  # half the prior left to play
    assert half.total_mean == pytest.approx(27 + 24)
    assert half.p_home_win < 0.5


def test_a_bigger_lead_is_a_bigger_win_chance():
    prior = Prior(0.0, 14.0, 48.0, 13.0)
    chances = [
        project(prior, GameState(2700, 10 + lead, 10), flat_params(), EpTable({})).p_home_win
        for lead in (-7, 0, 3, 7, 14)
    ]
    assert chances == sorted(chances)
    assert chances[1] == pytest.approx(0.5)


def test_after_regulation_it_reports_the_score_and_claims_nothing():
    p = project(Prior(0, 14, 48, 13), GameState(3600, 20, 17), flat_params(), EpTable({}))
    assert (p.margin_mean, p.total_mean, p.p_home_win, p.margin_sd) == (3, 37, 1.0, 0.0)


def test_never_projects_fewer_points_than_are_on_the_board():
    params = flat_params(total_k=-5.0)
    p = project(Prior(0, 14, 48, 13), GameState(3000, 21, 21), params, EpTable({}))
    assert p.total_mean == 42


def test_possession_near_the_goal_line_moves_the_projection():
    params = flat_params()
    for e in params.margin:
        params.margin[e].weights["ep"] = 1.0
    ep = EpTable({(1, 0): 5.0})
    base = project(Prior(0, 14, 48, 13), GameState(3000, 14, 14), params, ep)
    home_ball = project(Prior(0, 14, 48, 13), GameState(3000, 14, 14, 1, 1, 3), params, ep)
    away_ball = project(Prior(0, 14, 48, 13), GameState(3000, 14, 14, -1, 1, 3), params, ep)
    assert away_ball.margin_mean < base.margin_mean < home_ball.margin_mean
    assert home_ball.margin_mean - base.margin_mean == pytest.approx(5.0)


def test_coefficients_interpolate_between_checkpoints_and_the_scale_runs_out_at_the_end():
    params = LiveParams("A")
    params.margin[0] = Coefficients({"prior": 1.0}, 1.0, math.inf, 1.0)
    params.margin[1800] = Coefficients({"prior": 0.0}, 0.5, 4.0, 0.5)
    params.margin[3540] = Coefficients({"prior": 0.0}, 0.2, 2.0, 0.4)
    mid = params.at("margin", 900)
    assert mid.weights["prior"] == pytest.approx(0.5)
    assert mid.sd_scale == pytest.approx(0.75)
    assert mid.win_k == pytest.approx(0.75)
    assert mid.df == pytest.approx(8.0)  # blended on 1/ν: normal (0) and 1/4 -> 1/8
    assert params.at("margin", 3570).sd_scale == pytest.approx(0.1)
    assert params.at("margin", 3600).sd_scale == 0.0


def test_interval_of_a_normal_and_of_a_t():
    lo, hi = interval(10.0, 2.0)
    assert (lo, hi) == pytest.approx((10 - 2 * 1.28155, 10 + 2 * 1.28155), abs=1e-3)
    t_lo, t_hi = interval(10.0, 2.0, 3.0)
    assert t_hi - t_lo > hi - lo  # fatter tails, wider central range


def plays_frame(rows: list[tuple]) -> pl.DataFrame:
    cols = (
        "game_id",
        "seq",
        "period",
        "clock_seconds",
        "offense_team_id",
        "down",
        "yards_to_goal",
        "ppa",
        "offense_score",
        "defense_score",
    )
    return pl.DataFrame([dict(zip(cols, r, strict=True)) for r in rows])


def college_game() -> pl.DataFrame:
    """CFBD style: scores AFTER the play. Home drives and scores a TD, away kicks a FG late."""
    rows = []
    seq = 0
    for i in range(12):  # home drive, first quarter
        seq += 1
        scored = i == 11
        rows.append((10, seq, 1, 900 - 30 * i, HOME, 1, 60 - 5 * i, 0.2, 7 if scored else 0, 0))
    for i in range(12):  # away possession, second quarter, no score
        seq += 1
        rows.append((10, seq, 2, 900 - 30 * i, AWAY, 2, 70 - 2 * i, -0.1, 0, 7))
    for i in range(12):  # home, third quarter: second TD
        seq += 1
        rows.append((10, seq, 3, 900 - 30 * i, HOME, 1, 40 - 3 * i, 0.3, 14 if i == 11 else 7, 0))
    for i in range(12):  # away, fourth quarter: field goal on the last play
        seq += 1
        rows.append((10, seq, 4, 900 - 30 * i, AWAY, 3, 30 - i, 0.0, 3 if i == 11 else 0, 14))
    return plays_frame(rows)


def test_college_scores_are_moved_to_before_the_play():
    tl = lb.build_timelines(college_game(), FINAL, scores_after_play=True)[10]
    assert (tl.home_before[11], tl.away_before[11]) == (0, 0)  # the TD play itself starts 0-0
    assert (tl.home_before[12], tl.away_before[12]) == (7, 0)
    assert (tl.home_before[-1], tl.away_before[-1]) == (14, 0)


def test_nfl_scores_are_already_before_the_play():
    """The same game written nflverse's way (scores before each play) gives the same timeline."""
    college = college_game()
    cfb = lb.build_timelines(college, FINAL, scores_after_play=True)[10]
    home_off = college["offense_team_id"].to_numpy() == HOME
    nfl_style = college.with_columns(
        pl.Series("offense_score", np.where(home_off, cfb.home_before, cfb.away_before)),
        pl.Series("defense_score", np.where(home_off, cfb.away_before, cfb.home_before)),
    )
    nfl = lb.build_timelines(nfl_style, FINAL, scores_after_play=False)[10]
    assert np.array_equal(nfl.home_before, cfb.home_before)
    assert np.array_equal(nfl.away_before, cfb.away_before)


def test_state_at_reads_the_next_snap_and_the_score_before_it():
    tl = lb.build_timelines(college_game(), FINAL, scores_after_play=True)[10]
    s = lb.state_at(tl, 1800)  # halftime: the first third-quarter snap, home ball
    assert (s.home_points, s.away_points, s.possession, s.down, s.yards_to_goal) == (7, 0, 1, 1, 40)
    assert s.ppa_diff > 0  # home has played better so far
    assert lb.state_at(tl, 3599) is None  # no snap left in regulation


def test_expected_points_credit_the_next_score_to_the_offense():
    tl = lb.build_timelines(college_game(), FINAL, scores_after_play=True)
    rows = [tl[10]] * 40  # bins need 30 snaps to be kept
    ep = lb.fit_ep_table(rows, FINAL)
    # Home's first-down snaps in the first half all lead to its TD: +7.
    assert ep.values[(1, 5)] == pytest.approx(7.0)
    # Away's second-down snaps in the second quarter: nothing more in the half.
    assert ep.values[(2, 6)] == pytest.approx(0.0)


def test_fit_recovers_known_coefficients_and_win_k():
    rng = np.random.default_rng(7)
    rows = []
    for g in range(3000):
        prior = Prior(float(rng.normal(0, 7)), 14.0, 48.0, 13.0)
        e = int(rng.choice([0, 1800]))
        tau = 1 - e / 3600
        now = int(rng.integers(-10, 11)) if e else 0
        rest = 0.8 * prior.margin_mean * tau + rng.normal(0, 14 * math.sqrt(tau))
        final = int(round(now + rest)) or 1
        state = GameState(e, max(now, 0) + 10, max(-now, 0) + 10)
        rows.append(lb.Checkpoint(g, 2024, e, state, prior, final, 40, g))
    params = lb.fit(rows, "A", EpTable({}))
    assert params.margin[0].weights["prior"] == pytest.approx(0.8, abs=0.08)
    assert params.margin[1800].weights["prior"] == pytest.approx(0.8, abs=0.12)
    assert params.margin[0].win_k == pytest.approx(1.0, abs=0.15)


def test_t_fit_finds_fat_tails_and_a_normal_when_there_are_none():
    rng = np.random.default_rng(3)
    _, df_normal = lb.fit_t(rng.normal(0, 1, 20000))
    _, df_fat = lb.fit_t(rng.standard_t(3, 20000))
    assert df_normal >= 15
    assert df_fat <= 4
