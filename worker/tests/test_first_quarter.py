"""Tests for the first-quarter markets: the hurdle family, the projector, and the
backtest plumbing that grades them.

No network and no database. The failures worth pinning are the plausible ones:
a hurdle whose widening moves its mean, a zero mass that ignores volume, a
usage floor that silently rejects every first-quarter row, and a line anchored
somewhere a book never would.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats as st

from worker.core import features, models
from worker.core.backtest import line_centre, walk_forward
from worker.core.features import AsOf
from worker.core.models import (
    FIRST_QUARTER_PARENTS,
    Projection,
    count_zero_probability,
    finalize,
    project,
    project_first_quarter,
    rescale,
    shift_mean,
)
from worker.core.probability import distribution_quantile, distribution_sd, prob_over
from worker.core.projections import (
    MIN_USAGE_FRACTION_OF_BASELINE,
    first_quarter_catalogue,
    usage_floor,
)

HURDLE = {"p_zero": 0.45, "shape": 1.6, "scale": 12.0, "loc": 0.0}


def _hurdle_mean(params: dict[str, float]) -> float:
    return (1.0 - params["p_zero"]) * (params.get("loc", 0.0) + params["shape"] * params["scale"])


# -----------------------------------------------------------------------------
# The family
# -----------------------------------------------------------------------------
class TestHurdleGamma:
    def test_over_a_real_line_is_the_positive_part_alone(self):
        expected = 0.55 * st.gamma.sf(9.5, a=1.6, scale=12.0)
        assert prob_over("hurdle_gamma", HURDLE, 9.5) == pytest.approx(expected)

    def test_a_blank_quarter_is_never_over_zero(self):
        assert prob_over("hurdle_gamma", HURDLE, 0.0) == pytest.approx(0.55)

    def test_a_negative_line_counts_the_blank_as_over(self):
        assert prob_over("hurdle_gamma", HURDLE, -1.0) == pytest.approx(1.0)

    def test_quantiles_inside_the_zero_mass_are_zero(self):
        assert distribution_quantile("hurdle_gamma", HURDLE, 0.25) == 0.0
        assert distribution_quantile("hurdle_gamma", HURDLE, 0.45) == 0.0
        assert distribution_quantile("hurdle_gamma", HURDLE, 0.75) == pytest.approx(
            st.gamma.ppf((0.75 - 0.45) / 0.55, a=1.6, scale=12.0)
        )

    def test_the_sd_matches_a_simulation(self):
        rng = np.random.default_rng(7)
        n = 400_000
        draws = np.where(rng.random(n) < 0.45, 0.0, rng.gamma(1.6, 12.0, n))
        assert distribution_sd("hurdle_gamma", HURDLE) == pytest.approx(
            float(draws.std()), rel=0.01
        )


class TestHurdleUnderCalibration:
    @staticmethod
    def _projection() -> Projection:
        return finalize("q1_rec_yards", "hurdle_gamma", dict(HURDLE), _hurdle_mean(HURDLE))

    @pytest.mark.parametrize("scale", [0.8, 1.4, 2.0])
    def test_a_rescale_moves_the_whole_sd_by_exactly_the_scale(self, scale):
        # The calibration layer measured the OVERALL width. Widening only the
        # positive part by `scale` would under-correct every single week.
        base = self._projection()
        moved = rescale(base, scale)
        assert distribution_sd("hurdle_gamma", moved.params) == pytest.approx(
            scale * distribution_sd("hurdle_gamma", base.params), rel=1e-6
        )

    def test_a_rescale_holds_the_mean_and_the_blank_quarter(self):
        base = self._projection()
        moved = rescale(base, 1.6)
        assert _hurdle_mean(moved.params) == pytest.approx(_hurdle_mean(base.params))
        assert moved.params["p_zero"] == base.params["p_zero"]
        assert moved.mean == base.mean

    def test_a_mean_shift_moves_the_positive_part_and_not_the_zero(self):
        base = self._projection()
        moved = shift_mean(base, 1.1)
        assert moved.mean == pytest.approx(base.mean * 1.1)
        assert _hurdle_mean(moved.params) == pytest.approx(moved.mean)
        assert moved.params["p_zero"] == base.params["p_zero"]


class TestCountZero:
    def test_unit_dispersion_is_the_poisson_zero(self):
        assert count_zero_probability(0.8, 1.0) == pytest.approx(math.exp(-0.8))

    def test_dispersion_two_is_the_geometric_zero(self):
        assert count_zero_probability(0.8, 2.0) == pytest.approx(1 / 1.8)

    def test_overdispersion_adds_blanks_at_the_same_mean(self):
        assert count_zero_probability(0.8, 1.3) > count_zero_probability(0.8, 1.0)

    def test_more_volume_means_fewer_blanks(self):
        assert count_zero_probability(1.4, 1.3) < count_zero_probability(0.4, 1.3)


# -----------------------------------------------------------------------------
# The projector
# -----------------------------------------------------------------------------
def _wr_row(**overrides):
    row = {
        "position_group": "WR",
        "games_played": 7,
        "prior_games_played": 0,
        "prior_weight": 0.0,
        "targets_pg": 8.0,
        "receptions_pg": 5.0,
        "rec_yards_pg": 70.0,
        "receptions_sd": 2.0,
        "rec_yards_sd": 30.0,
    }
    row.update(overrides)
    return row


BASELINES = {
    "targets_pg": 6.0,
    "receptions_pg": 4.0,
    "rec_yards_pg": 50.0,
    "q1_share_rec_yards": 0.21,
    "q1_share_receptions": 0.22,
    "q1_cv_positive_rec_yards": 0.85,
}


class TestFirstQuarterProjector:
    def test_the_mean_is_the_full_game_mean_times_the_share(self):
        full = project(_wr_row(), "rec_yards", "gamma", BASELINES, {"WR": {}})
        q1 = project(_wr_row(), "q1_rec_yards", "hurdle_gamma", BASELINES, {"WR": {}})
        assert full is not None and q1 is not None
        assert q1.distribution == "hurdle_gamma"
        assert q1.mean == pytest.approx(full.mean * 0.21)

    def test_the_hurdle_keeps_that_mean_exactly(self):
        q1 = project(_wr_row(), "q1_rec_yards", "hurdle_gamma", BASELINES, {"WR": {}})
        assert q1 is not None
        assert _hurdle_mean(q1.params) == pytest.approx(q1.mean)

    def test_the_blank_quarter_comes_from_first_quarter_receptions(self):
        full = project(_wr_row(), "rec_yards", "gamma", BASELINES, {"WR": {}})
        q1 = project(_wr_row(), "q1_rec_yards", "hurdle_gamma", BASELINES, {"WR": {}})
        assert full is not None and q1 is not None
        expected = count_zero_probability(
            full.volume * 0.22, models.FIRST_QUARTER_ZERO_DISPERSION[("rec_yards", "WR")]
        )
        assert q1.params["p_zero"] == pytest.approx(expected)

    def test_the_same_yards_on_more_catches_is_blanked_less_often(self):
        # The telescoping identity holds the mean fixed while volume moves, so
        # this isolates the one thing volume should change: the blank quarter.
        few = project(
            _wr_row(targets_pg=4.0, receptions_pg=2.5), "q1_rec_yards",
            "hurdle_gamma", BASELINES, {"WR": {}},
        )
        many = project(
            _wr_row(targets_pg=12.0, receptions_pg=7.5), "q1_rec_yards",
            "hurdle_gamma", BASELINES, {"WR": {}},
        )
        assert few is not None and many is not None
        assert few.mean == pytest.approx(many.mean)
        assert many.params["p_zero"] < few.params["p_zero"]

    def test_without_last_seasons_share_there_is_no_projection(self):
        # Better no row than a first quarter scaled by nothing.
        baselines = {k: v for k, v in BASELINES.items() if k != "q1_share_rec_yards"}
        assert project(_wr_row(), "q1_rec_yards", "hurdle_gamma", baselines, {"WR": {}}) is None

    def test_first_quarter_anytime_td_is_a_poisson_on_the_scaled_expectation(self, monkeypatch):
        def full_game(row, market_key, distribution, baselines, league):
            assert market_key == "anytime_td"
            return Projection(
                market_key="anytime_td", distribution="bernoulli",
                params={"p": 0.3}, mean=0.4,
            )

        monkeypatch.setattr(models, "project", full_game)
        q1 = project_first_quarter(
            {"position_group": "RB"}, "q1_anytime_td", {"q1_share_offensive_tds": 0.2}, {}
        )
        assert q1 is not None
        assert q1.distribution == "bernoulli"
        assert q1.params["p"] == pytest.approx(1.0 - math.exp(-0.4 * 0.2))

    def test_every_first_quarter_market_scales_a_market_with_a_projector(self):
        for parent in FIRST_QUARTER_PARENTS.values():
            assert parent in {"pass_yards", "rush_yards", "rec_yards", "anytime_td"}


# -----------------------------------------------------------------------------
# Catalogue, usage floor, lines
# -----------------------------------------------------------------------------
PARENTS = [
    {
        "market_key": "rec_yards", "position_group": "RB", "stat_column": "rec_yards",
        "is_binary": False, "default_line": None, "ladder_step": 10.0,
        "distribution_family": "lognormal",
    },
    {
        "market_key": "anytime_td", "position_group": "QB", "stat_column": "offensive_tds",
        "is_binary": True, "default_line": 0.5, "ladder_step": None,
        "distribution_family": "bernoulli",
    },
    {
        "market_key": "pass_attempts", "position_group": "QB", "stat_column": "pass_attempts",
        "is_binary": False, "default_line": None, "ladder_step": 5.0,
        "distribution_family": "negative_binomial",
    },
]


class TestCatalogue:
    def test_each_first_quarter_market_mirrors_its_parents_positions_and_no_more(self):
        derived = first_quarter_catalogue(PARENTS)
        assert {(m["market_key"], m["position_group"]) for m in derived} == {
            ("q1_rec_yards", "RB"),
            ("q1_anytime_td", "QB"),
        }

    def test_it_grades_against_the_q1_column_with_its_own_family(self):
        by_key = {m["market_key"]: m for m in first_quarter_catalogue(PARENTS)}
        receiving = by_key["q1_rec_yards"]
        assert receiving["stat_column"] == "q1_rec_yards"
        # The RB parent's lognormal override must not leak through.
        assert receiving["distribution_family"] == "hurdle_gamma"
        assert receiving["ladder_step"] is None
        touchdown = by_key["q1_anytime_td"]
        assert touchdown["stat_column"] == "q1_offensive_tds"
        assert touchdown["is_binary"] is True and touchdown["default_line"] == 0.5


class TestUsageFloor:
    def test_a_first_quarter_floor_is_the_parents_scaled_by_the_share(self):
        market = first_quarter_catalogue(PARENTS)[0]
        floor = usage_floor(market, {"rec_yards_pg": 40.0, "q1_share_rec_yards": 0.2})
        assert floor == pytest.approx(MIN_USAGE_FRACTION_OF_BASELINE * 40.0 * 0.2)

    def test_a_full_game_floor_is_unchanged(self):
        assert usage_floor(PARENTS[0], {"rec_yards_pg": 40.0}) == pytest.approx(
            MIN_USAGE_FRACTION_OF_BASELINE * 40.0
        )

    def test_no_share_means_no_floor_rather_than_a_zero_one(self):
        market = first_quarter_catalogue(PARENTS)[0]
        assert usage_floor(market, {"rec_yards_pg": 40.0}) is None


class TestLineCentre:
    def test_a_first_quarter_line_is_the_trailing_full_game_average_times_the_share(self):
        market = first_quarter_catalogue(PARENTS)[0]
        assert line_centre({"rec_yards_pg": 60.0}, market, {"q1_share_rec_yards": 0.2}) == pytest.approx(12.0)

    def test_it_falls_back_to_last_season_like_every_anchor(self):
        market = first_quarter_catalogue(PARENTS)[0]
        assert line_centre(
            {"prior_rec_yards_pg": 50.0}, market, {"q1_share_rec_yards": 0.2}
        ) == pytest.approx(10.0)

    def test_without_a_share_there_is_no_anchor(self):
        market = first_quarter_catalogue(PARENTS)[0]
        assert line_centre({"rec_yards_pg": 60.0}, market, {}) is None

    def test_a_full_game_market_is_anchored_as_before(self):
        assert line_centre({"rec_yards_pg": 60.0}, PARENTS[0], {}) == pytest.approx(60.0)


# -----------------------------------------------------------------------------
# Scope
# -----------------------------------------------------------------------------
def test_a_first_quarter_walk_refuses_college_before_touching_the_database():
    with pytest.raises(ValueError, match="NFL"):
        walk_forward([2025], first_quarter=True, sport="cfb")


def test_a_first_quarter_walk_writes_its_own_report_never_the_college_one():
    from worker.jobs import run_backtest

    assert run_backtest._report_path(False) == run_backtest.REPORT_PATH
    assert run_backtest._report_path(True) != run_backtest.REPORT_PATH


def test_a_first_quarter_report_carries_its_own_caveats():
    from worker.jobs import run_backtest

    plain = run_backtest._caveats(3.0, [2024, 2025])
    with_quarter = run_backtest._caveats(3.0, [2024, 2025], first_quarter=True)
    assert with_quarter == plain + run_backtest.FIRST_QUARTER_CAVEATS


def test_the_backtest_job_refuses_a_college_first_quarter_walk():
    from worker.jobs import run_backtest

    assert run_backtest.main(["--sport", "cfb", "--first-quarter", "--seasons", "2025"]) == 2


def test_the_profile_reads_last_season_for_one_sport(monkeypatch):
    captured: dict = {}

    def fake_fetch_all(sql, params):
        captured["sql"], captured["params"] = sql, params
        return []

    monkeypatch.setattr(features, "fetch_all", fake_fetch_all)
    features.first_quarter_profile(AsOf(2026, 3, "nfl"))

    assert captured["params"]["prior_season"] == 2025
    assert captured["params"]["sport"] == "nfl"
    assert "g.sport = %(sport)s" in captured["sql"]
    # Only the prior season: a current-season predicate would be lookahead the
    # moment the week filter was forgotten.
    assert "%(season)s" not in captured["sql"]
