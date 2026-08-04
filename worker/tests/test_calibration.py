"""Tests for the point-in-time variance calibration.

The correction exists because the first full backtest was overconfident at the
extremes — it said 0.96 and hit 0.77. What matters here is not only that the
scale is arithmetically right, but that it stays POINT-IN-TIME: a width learned
from a week it then grades would flatter the report in exactly the way
CLAUDE.md §4 calls disqualifying.
"""

from __future__ import annotations

import math

import pytest
from scipy import stats as st

from worker.core.calibration import (
    MAX_MEAN_MULTIPLIER,
    MAX_SCALE,
    MIN_CELL_RESIDUALS,
    MIN_MEAN_MULTIPLIER,
    MIN_RESIDUALS,
    MIN_SCALE,
    Calibration,
    MeanCalibration,
    VarianceCalibration,
    history_bucket,
    shrink_toward_one,
)
from worker.core.models import Projection, rescale, shift_mean
from worker.core.probability import distribution_sd


def _feed(calibration, market, position, count, *, spread, sd=1.0, games=10.0):
    """Feed `count` residuals whose true spread is `spread` times the model's."""
    rng = [st.norm.ppf((i + 0.5) / count) for i in range(count)]
    for z in rng:
        calibration.observe(market, position, z * spread * sd, 0.0, sd, games)


class TestScaleEstimate:
    def test_matched_width_needs_no_correction(self):
        c = VarianceCalibration()
        _feed(c, "rec_yards", "WR", 2000, spread=1.0)
        assert c.scale("rec_yards", "WR", 10.0) == pytest.approx(1.0, abs=0.05)

    def test_too_narrow_a_projection_is_widened(self):
        # Outcomes are 1.4x more spread than the model claimed.
        c = VarianceCalibration()
        _feed(c, "rec_yards", "WR", 2000, spread=1.4)
        assert c.scale("rec_yards", "WR", 10.0) == pytest.approx(1.4, abs=0.07)

    def test_too_wide_a_projection_is_narrowed(self):
        """receptions and pass_tds measured BELOW 1.0, so the correction has to
        run in both directions or it is just a fudge factor."""
        c = VarianceCalibration()
        _feed(c, "receptions", "WR", 2000, spread=0.85)
        assert c.scale("receptions", "WR", 10.0) < 0.95

    def test_scale_is_independent_of_the_units(self):
        wide = VarianceCalibration()
        _feed(wide, "pass_yards", "QB", 2000, spread=1.3, sd=60.0)
        assert wide.scale("pass_yards", "QB", 10.0) == pytest.approx(1.3, abs=0.07)


class TestLearningGuards:
    def test_no_correction_before_enough_evidence(self):
        c = VarianceCalibration()
        _feed(c, "rec_yards", "WR", MIN_RESIDUALS - 1, spread=2.0)
        assert c.scale("rec_yards", "WR", 10.0) == 1.0

    def test_correction_applies_once_the_threshold_is_crossed(self):
        c = VarianceCalibration()
        _feed(c, "rec_yards", "WR", MIN_RESIDUALS + 50, spread=1.5)
        assert c.scale("rec_yards", "WR", 10.0) > 1.1

    def test_a_thin_position_falls_back_to_its_market(self):
        """rush_yards for a QB is a different shape from an RB's, but a split
        measured on eighty games is worse than the pooled estimate."""
        c = VarianceCalibration()
        _feed(c, "rush_yards", "RB", MIN_CELL_RESIDUALS + 100, spread=1.2)
        _feed(c, "rush_yards", "QB", 80, spread=3.0)
        # QB has too few of its own, so it inherits the broader estimate rather
        # than the wild one its own 80 games imply.
        assert c.scale("rush_yards", "QB", 10.0) < 1.5

    def test_a_well_measured_position_overrides_its_market(self):
        c = VarianceCalibration()
        _feed(c, "rush_yards", "RB", MIN_CELL_RESIDUALS + 100, spread=1.1)
        _feed(c, "rush_yards", "QB", MIN_CELL_RESIDUALS + 100, spread=1.9)
        assert c.scale("rush_yards", "QB", 10.0) > c.scale("rush_yards", "RB", 10.0)

    def test_one_absurd_game_cannot_set_the_width(self):
        c = VarianceCalibration()
        _feed(c, "rec_yards", "WR", MIN_RESIDUALS + 50, spread=1.0)
        for _ in range(3):
            c.observe("rec_yards", "WR", 10_000.0, 0.0, 1.0, 10.0)
        assert c.scale("rec_yards", "WR", 10.0) < 1.4

    def test_scale_is_clamped_both_ways(self):
        wide = VarianceCalibration()
        _feed(wide, "m", "WR", 2000, spread=40.0)
        narrow = VarianceCalibration()
        _feed(narrow, "m", "WR", 2000, spread=0.001)
        assert wide.scale("m", "WR", 10.0) == pytest.approx(MAX_SCALE)
        assert narrow.scale("m", "WR", 10.0) == pytest.approx(MIN_SCALE)

    def test_degenerate_inputs_are_ignored_not_propagated(self):
        c = VarianceCalibration()
        c.observe("m", "WR", 5.0, 0.0, 0.0, 10.0)
        c.observe("m", "WR", float("nan"), 0.0, 1.0, 10.0)
        c.observe("m", "WR", 5.0, 0.0, float("inf"), 10.0)
        assert c.scale("m", "WR", 10.0) == 1.0

    def test_unknown_market_is_left_alone(self):
        assert VarianceCalibration().scale("never_seen", "WR", 10.0) == 1.0


class TestHistoryConditioning:
    """The correction is not constant across a season.

    The first calibrated walk measured x2.04 for rec_yards from week 3 and x1.48
    from weeks 6, 10 and 13. That is the mechanism showing through: the variance
    being corrected for is error in the projected MEAN, and early in the season
    that mean is mostly shrinkage toward a position baseline rather than the
    player's own production. One pooled number over-corrects late and
    under-corrects early.
    """

    def test_a_thin_sample_earns_a_bigger_correction_than_an_established_one(self):
        c = VarianceCalibration()
        _feed(c, "rec_yards", "WR", MIN_CELL_RESIDUALS + 100, spread=2.0, games=2.0)
        _feed(c, "rec_yards", "WR", MIN_CELL_RESIDUALS + 100, spread=1.3, games=11.0)
        assert c.scale("rec_yards", "WR", 2.0) > c.scale("rec_yards", "WR", 11.0)
        assert c.scale("rec_yards", "WR", 2.0) == pytest.approx(2.0, abs=0.1)
        assert c.scale("rec_yards", "WR", 11.0) == pytest.approx(1.3, abs=0.1)

    def test_buckets_on_games_not_on_calendar_week(self):
        """A week-12 player back from injury with two games has the same thin
        evidence as anyone in week 3, and deserves the same widening."""
        assert history_bucket(2) == history_bucket(3)
        assert history_bucket(2) != history_bucket(11)
        assert history_bucket(5) not in (history_bucket(2), history_bucket(11))

    def test_no_games_played_is_not_a_thin_sample_but_no_sample(self):
        """The opening weekends must not share a cell with the 2-3 game rows.

        Every `thin` scale was fitted on rows with a current-season record; a
        week-1 row has none at all. One cell would hand each regime the other's
        correction, which is the mixing this bucketing exists to prevent.
        """
        assert history_bucket(0) == history_bucket(1)
        assert history_bucket(0) != history_bucket(2)
        assert history_bucket(0) != history_bucket(11)

    def test_a_bucket_with_no_measurement_of_its_own_falls_back_to_the_market(self):
        """Which is what the opening weeks get on the first walk that grades
        them: nothing has been measured at 0 games, so they take the market
        estimate rather than 1.0."""
        c = VarianceCalibration()
        _feed(c, "rec_yards", "WR", MIN_RESIDUALS + 10, spread=1.3, games=11.0)
        assert c.scale("rec_yards", "WR", 0.0) == pytest.approx(1.3, abs=0.1)

    def test_an_unmeasured_bucket_falls_back_rather_than_guessing(self):
        c = VarianceCalibration()
        # Everything measured on established players only.
        _feed(c, "rec_yards", "WR", MIN_CELL_RESIDUALS + 100, spread=1.3, games=11.0)
        # A thin-sample player still gets the broader market estimate, not 1.0.
        assert c.scale("rec_yards", "WR", 2.0) == pytest.approx(1.3, abs=0.1)


class TestSnapshot:
    def test_records_scale_sample_size_and_whether_it_applied(self):
        c = VarianceCalibration()
        _feed(c, "rec_yards", "WR", MIN_RESIDUALS + 10, spread=1.3)
        _feed(c, "pass_tds", "QB", 20, spread=1.3)
        snap = c.snapshot()
        assert snap["rec_yards"]["applied"] is True
        assert snap["rec_yards"]["n"] == MIN_RESIDUALS + 10
        # Reported even when unused, so a thin market is visible rather than absent.
        assert snap["pass_tds"]["applied"] is False


class TestShrinkTowardOne:
    """Damping a correction by how well it is measured.

    The first version applied every measurement at full strength, and it showed:
    `receptions` (measured x0.96) and `rush_yards` both came out slightly WORSE
    after correction, while large well-measured deviations like `rec_yards`
    (x1.66) improved substantially. A 4% correction from a sample whose
    uncertainty spans 1.0 is noise being applied as signal.
    """

    def test_a_deviation_smaller_than_its_error_is_dropped_entirely(self):
        assert shrink_toward_one(0.96, standard_error=0.08) == 1.0
        assert shrink_toward_one(1.05, standard_error=0.10) == 1.0

    def test_a_deviation_far_larger_than_its_error_survives_almost_intact(self):
        out = shrink_toward_one(1.66, standard_error=0.02)
        assert out == pytest.approx(1.66, abs=0.01)

    def test_damping_is_partial_in_between(self):
        out = shrink_toward_one(1.30, standard_error=0.15)
        assert 1.0 < out < 1.30

    def test_the_same_deviation_survives_or_dies_on_its_error_alone(self):
        """The whole point: identical measured effects, different certainty."""
        certain = shrink_toward_one(1.10, standard_error=0.01)
        uncertain = shrink_toward_one(1.10, standard_error=0.30)
        assert certain > 1.09
        assert uncertain == 1.0

    def test_direction_is_never_flipped(self):
        for raw in (0.7, 0.9, 1.1, 1.4):
            out = shrink_toward_one(raw, standard_error=0.05)
            assert (out - 1.0) * (raw - 1.0) >= 0

    def test_an_unmeasurable_error_leaves_the_estimate_alone(self):
        assert shrink_toward_one(1.4, standard_error=0.0) == 1.4
        assert shrink_toward_one(1.4, standard_error=float("nan")) == 1.4


class TestStandardErrors:
    def test_the_width_error_falls_as_the_sample_grows(self):
        small, large = VarianceCalibration(), VarianceCalibration()
        _feed(small, "m", "WR", 500, spread=1.3)
        _feed(large, "m", "WR", 20000, spread=1.3)
        # Both measure the same effect; only the larger one should keep it whole.
        assert large.scale("m", "WR", 10.0) > small.scale("m", "WR", 10.0)
        assert large.scale("m", "WR", 10.0) == pytest.approx(1.3, abs=0.05)

    def test_a_tiny_real_effect_needs_a_big_sample_to_survive(self):
        small, large = VarianceCalibration(), VarianceCalibration()
        _feed(small, "m", "WR", 600, spread=1.03)
        _feed(large, "m", "WR", 60000, spread=1.03)
        assert small.scale("m", "WR", 10.0) == 1.0
        assert large.scale("m", "WR", 10.0) > 1.0

    def test_the_ratio_error_reflects_how_tightly_outcomes_track(self):
        """A market whose outcomes hug their projections has a well-determined
        ratio from a modest sample; a scattered one does not."""
        tight, loose = MeanCalibration(), MeanCalibration()
        for i in range(3000):
            wobble = 1.0 if i % 2 else -1.0
            tight.observe("m", 33.0 + 0.5 * wobble, 30.0, 10.0)
            loose.observe("m", 33.0 + 25.0 * wobble, 30.0, 10.0)
        assert tight.multiplier("m", 10.0) > loose.multiplier("m", 10.0)
        assert tight.multiplier("m", 10.0) == pytest.approx(1.1, abs=0.02)

    def test_snapshot_reports_raw_alongside_the_damped_value(self):
        c = VarianceCalibration()
        _feed(c, "m", "WR", 600, spread=1.02)
        entry = c.snapshot()["m"]
        # Raw is preserved so a dropped correction is visible rather than absent.
        assert entry["raw"] == pytest.approx(1.02, abs=0.03)
        assert entry["scale"] == 1.0


class TestMeanCalibration:
    """The bias correction.

    pass_attempts ran low across almost its whole reliability curve — it said
    0.44 where 0.54 happened. The residuals said it directly: a quarterback's
    actual attempts exceed his own trailing average by +0.5 to +2.3 at every
    level of history, because a player still holding a projectable role in week
    10 is one who kept it, while his season-to-date average is dragged down by
    the weeks he was splitting time.
    """

    @staticmethod
    def _feed(cal, market, count, *, actual, projected, games=10.0):
        for _ in range(count):
            cal.observe(market, actual, projected, games)

    def test_an_unbiased_market_is_left_alone(self):
        c = MeanCalibration()
        self._feed(c, "rec_yards", 2000, actual=50.0, projected=50.0)
        assert c.multiplier("rec_yards", 10.0) == pytest.approx(1.0)

    def test_a_market_running_low_is_scaled_up(self):
        c = MeanCalibration()
        self._feed(c, "pass_attempts", 2000, actual=32.0, projected=30.0)
        assert c.multiplier("pass_attempts", 10.0) == pytest.approx(32 / 30, abs=0.01)

    def test_a_market_running_high_is_scaled_down(self):
        c = MeanCalibration()
        self._feed(c, "rush_attempts", 2000, actual=12.0, projected=14.0)
        assert c.multiplier("rush_attempts", 10.0) < 1.0

    def test_it_is_a_ratio_of_sums_not_a_mean_of_ratios(self):
        """Per-row ratios are dominated by players with tiny projections, where
        actual/projected is enormous and means nothing. One 0.1-yard projection
        that returned 20 yards must not move a whole market."""
        c = MeanCalibration()
        self._feed(c, "rec_yards", MIN_RESIDUALS + 50, actual=60.0, projected=60.0)
        c.observe("rec_yards", 20.0, 0.1, 10.0)
        assert c.multiplier("rec_yards", 10.0) == pytest.approx(1.0, abs=0.02)

    def test_no_correction_before_enough_evidence(self):
        c = MeanCalibration()
        self._feed(c, "rec_yards", MIN_RESIDUALS - 1, actual=70.0, projected=50.0)
        assert c.multiplier("rec_yards", 10.0) == 1.0

    def test_the_multiplier_is_clamped_tightly(self):
        """A projection wrong by more than a fifth is a modelling failure to fix
        at source, not something to paper over with a multiplier."""
        high = MeanCalibration()
        self._feed(high, "m", 2000, actual=500.0, projected=50.0)
        low = MeanCalibration()
        self._feed(low, "m", 2000, actual=5.0, projected=50.0)
        assert high.multiplier("m", 10.0) == pytest.approx(MAX_MEAN_MULTIPLIER)
        assert low.multiplier("m", 10.0) == pytest.approx(MIN_MEAN_MULTIPLIER)

    def test_bias_can_differ_by_history(self):
        c = MeanCalibration()
        self._feed(c, "pass_attempts", MIN_CELL_RESIDUALS + 100,
                   actual=34.0, projected=30.0, games=2.0)
        self._feed(c, "pass_attempts", MIN_CELL_RESIDUALS + 100,
                   actual=31.0, projected=30.0, games=11.0)
        assert c.multiplier("pass_attempts", 2.0) > c.multiplier("pass_attempts", 11.0)

    def test_degenerate_inputs_are_ignored(self):
        c = MeanCalibration()
        c.observe("m", 5.0, 0.0, 10.0)
        c.observe("m", float("nan"), 10.0, 10.0)
        c.observe("m", -3.0, 10.0, 10.0)
        assert c.multiplier("m", 10.0) == 1.0


class TestShiftMean:
    @pytest.mark.parametrize("multiplier", [0.88, 1.15])
    @pytest.mark.parametrize(
        "distribution,params",
        [
            ("normal", {"mu": 250.0, "sigma": 60.0}),
            ("gamma", {"shape": 4.0, "scale": 15.0, "loc": 0.0}),
            ("lognormal", {"mu": 3.6, "sigma": 0.7, "loc": 0.0}),
            ("poisson", {"lam": 1.8}),
            ("negative_binomial", {"r": 12.0, "p": 0.4}),
        ],
    )
    def test_the_mean_moves_by_the_multiplier(self, distribution, params, multiplier):
        mean = _mean_of(distribution, params)
        out = shift_mean(
            Projection(market_key="m", distribution=distribution,
                       params=params, mean=mean),
            multiplier,
        )
        assert _mean_of(out.distribution, out.params) == pytest.approx(
            mean * multiplier, rel=0.02
        )

    def test_the_relative_width_is_preserved(self):
        """A quarterback who throws 10% more than projected is not thereby 10%
        more predictable, so the coefficient of variation must not change."""
        params = {"mu": 250.0, "sigma": 60.0}
        projection = Projection(
            market_key="m", distribution="normal", params=params, mean=250.0
        )
        before = distribution_sd("normal", params) / 250.0
        out = shift_mean(projection, 1.15)
        after = distribution_sd(out.distribution, out.params) / out.mean
        assert after == pytest.approx(before, rel=1e-6)

    def test_families_with_no_free_width_can_still_be_moved(self):
        """Unlike rescale: scaling a Poisson's lambda or a Bernoulli's p moves
        the mean, which is exactly what a bias correction wants."""
        poisson = shift_mean(
            Projection(market_key="m", distribution="poisson",
                       params={"lam": 2.0}, mean=2.0),
            1.15,
        )
        bernoulli = shift_mean(
            Projection(market_key="m", distribution="bernoulli",
                       params={"p": 0.20}, mean=0.20),
            1.15,
        )
        assert poisson.params["lam"] == pytest.approx(2.3)
        assert bernoulli.params["p"] == pytest.approx(0.23)

    def test_a_bernoulli_cannot_be_pushed_past_certainty(self):
        out = shift_mean(
            Projection(market_key="m", distribution="bernoulli",
                       params={"p": 0.95}, mean=0.95),
            1.20,
        )
        assert out.params["p"] < 1.0

    def test_a_multiplier_of_one_is_a_no_op(self):
        projection = Projection(
            market_key="m", distribution="normal",
            params={"mu": 1.0, "sigma": 1.0}, mean=1.0,
        )
        assert shift_mean(projection, 1.0) is projection

    def test_beta_binomial_keeps_its_headroom_above_the_mean(self):
        """n is a CEILING covering target uncertainty, so it has to scale with
        the mean — pinning it would reintroduce the impossible-outcome ceiling
        that the beta-binomial fix removed."""
        params = {"n": 10.0, "a": 3.0, "b": 7.0}
        mean = _mean_of("beta_binomial", params)
        out = shift_mean(
            Projection(market_key="receptions", distribution="beta_binomial",
                       params=params, mean=mean),
            1.2,
        )
        assert out.params["n"] > params["n"]
        assert _mean_of("beta_binomial", out.params) == pytest.approx(
            mean * 1.2, rel=0.06
        )


class TestCombinedCalibration:
    def test_bias_is_estimated_against_raw_and_width_against_shifted(self):
        """Order matters: E[(actual-mean)^2] is variance plus squared bias, so
        measuring width against a misplaced centre would widen a distribution
        that only needed moving."""
        c = Calibration()
        # Raw projection 30, graded at 33 after the bias correction. Outcomes
        # are centred on 33 with a spread of exactly the projected SD of 5.
        n = 2000
        for i in range(n):
            actual = 33.0 + 5.0 * st.norm.ppf((i + 0.5) / n)
            c.observe_many(
                [("pass_attempts", "QB", max(actual, 0.0), 33.0, 30.0, 5.0, 10.0)]
            )

        # The bias sees the full 33/30, because it is measured against raw.
        assert c.mean.multiplier("pass_attempts", 10.0) == pytest.approx(1.1, abs=0.01)

        # The width sees only the genuine spread. Measured against the RAW mean
        # of 30 it would have been sqrt(1 + (3/5)^2) = 1.17, and the
        # distribution would have been widened for being misplaced.
        assert c.variance.scale("pass_attempts", "QB", 10.0) == pytest.approx(
            1.0, abs=0.05
        )

    def test_snapshot_reports_both_corrections(self):
        c = Calibration()
        c.observe_many(
            [("rec_yards", "WR", 60.0, 55.0, 55.0, 30.0, 10.0)] * 2000
        )
        snap = c.snapshot()
        assert "mean" in snap and "width" in snap
        assert snap["mean"]["rec_yards"]["applied"] is True


class TestRescale:
    def _normal(self, sigma=10.0):
        return Projection(
            market_key="pass_yards", distribution="normal",
            params={"mu": 250.0, "sigma": sigma}, mean=250.0,
        )

    def test_widening_a_normal_moves_only_the_width(self):
        out = rescale(self._normal(), 1.4)
        assert out.params["sigma"] == pytest.approx(14.0)
        assert out.mean == pytest.approx(250.0)

    def test_a_scale_of_one_is_a_no_op(self):
        original = self._normal()
        assert rescale(original, 1.0) is original

    @pytest.mark.parametrize("scale", [0.8, 1.35])
    @pytest.mark.parametrize(
        "distribution,params,mean",
        [
            ("normal", {"mu": 250.0, "sigma": 60.0}, 250.0),
            ("gamma", {"shape": 4.0, "scale": 15.0, "loc": 0.0}, 60.0),
            ("lognormal", {"mu": 3.6, "sigma": 0.7, "loc": 0.0}, None),
            ("negative_binomial", {"r": 12.0, "p": 0.4}, None),
            ("beta_binomial", {"n": 12.0, "a": 3.0, "b": 5.0}, None),
        ],
    )
    def test_the_mean_survives_and_the_width_moves(
        self, distribution, params, mean, scale
    ):
        """The whole point: the over/under call is mass either side of a line,
        so moving the centre while claiming to fix the width would swap a
        calibration error for a bias."""
        before_sd = distribution_sd(distribution, params)
        if mean is None:
            mean = _mean_of(distribution, params)
        projection = Projection(
            market_key="m", distribution=distribution, params=params, mean=mean
        )
        out = rescale(projection, scale)

        assert _mean_of(out.distribution, out.params) == pytest.approx(mean, rel=0.02)
        after_sd = distribution_sd(out.distribution, out.params)
        assert after_sd > before_sd if scale > 1 else after_sd < before_sd

    def test_poisson_and_bernoulli_are_returned_untouched(self):
        """Their variance IS their mean. Silently 'correcting' them would move
        the mean, so they are left alone and the report says which they are."""
        for distribution, params in (
            ("poisson", {"lam": 1.8}),
            ("bernoulli", {"p": 0.4}),
        ):
            projection = Projection(
                market_key="m", distribution=distribution, params=params, mean=1.8
            )
            assert rescale(projection, 1.5).params == params

    @pytest.mark.parametrize("scale", [1.3, 1.65])
    @pytest.mark.parametrize(
        "distribution,params",
        [
            ("gamma", {"shape": 2.2, "scale": 22.0, "loc": 0.0}),
            ("lognormal", {"mu": 3.7, "sigma": 0.85, "loc": 0.0}),
        ],
    )
    def test_widening_a_skewed_family_does_not_drag_the_median_down(
        self, distribution, params, scale
    ):
        """The defect the second backtest exposed.

        Re-solving a right-skewed family at a larger SD holds the mean but pulls
        the MEDIAN down, and P(over) at a line near the centre is governed by
        the median. rec_yards is gamma and lognormal; its middle bins went from
        +0.002 to +0.060 -- a calibration error traded for a bias. A
        location-scale transform preserves the shape, so the median keeps its
        position relative to the mean.
        """
        mean = _mean_of(distribution, params)
        projection = Projection(
            market_key="rec_yards", distribution=distribution,
            params=params, mean=mean,
        )
        out = rescale(projection, scale)

        # Mean exact, width scaled, and the median's offset from the mean grows
        # in proportion rather than collapsing toward zero.
        assert _mean_of(out.distribution, out.params) == pytest.approx(mean, rel=1e-6)
        assert distribution_sd(out.distribution, out.params) == pytest.approx(
            distribution_sd(distribution, params) * scale, rel=1e-6
        )
        gap_before = _median_of(distribution, params) - mean
        gap_after = _median_of(out.distribution, out.params) - mean
        assert gap_after == pytest.approx(gap_before * scale, rel=1e-6)

    def test_a_line_at_the_median_keeps_its_probability_after_widening(self):
        """The practical consequence: widening must not move the call at the
        centre, only soften the confidence away from it."""
        params = {"shape": 2.2, "scale": 22.0, "loc": 0.0}
        mean = _mean_of("gamma", params)
        projection = Projection(
            market_key="rec_yards", distribution="gamma", params=params, mean=mean
        )
        median = _median_of("gamma", params)
        before = projection.probability_over(median)
        after = rescale(projection, 1.65).probability_over(
            _median_of("gamma", rescale(projection, 1.65).params)
        )
        assert before == pytest.approx(0.5, abs=0.01)
        assert after == pytest.approx(0.5, abs=0.01)

    def test_narrowing_a_beta_binomial_stops_at_the_binomial_limit(self):
        projection = Projection(
            market_key="receptions", distribution="beta_binomial",
            params={"n": 10.0, "a": 2.0, "b": 6.0}, mean=2.5,
        )
        out = rescale(projection, 0.2)
        assert math.isfinite(distribution_sd("beta_binomial", out.params))
        assert out.params["a"] > 0 and out.params["b"] > 0


def _median_of(distribution: str, params: dict) -> float:
    if distribution == "gamma":
        return float(
            st.gamma.median(
                a=params["shape"], loc=params.get("loc", 0.0), scale=params["scale"]
            )
        )
    if distribution == "lognormal":
        return float(
            st.lognorm.median(
                s=params["sigma"],
                loc=params.get("loc", 0.0),
                scale=math.exp(params["mu"]),
            )
        )
    raise AssertionError(distribution)


def _mean_of(distribution: str, params: dict) -> float:
    if distribution == "normal":
        return float(params["mu"])
    if distribution == "gamma":
        return params["shape"] * params["scale"] + params.get("loc", 0.0)
    if distribution == "lognormal":
        return (
            math.exp(params["mu"] + params["sigma"] ** 2 / 2) + params.get("loc", 0.0)
        )
    if distribution == "negative_binomial":
        return params["r"] * (1 - params["p"]) / params["p"]
    if distribution == "beta_binomial":
        return params["n"] * params["a"] / (params["a"] + params["b"])
    if distribution == "poisson":
        return float(params["lam"])
    raise AssertionError(distribution)


class TestDistributionSd:
    @pytest.mark.parametrize(
        "distribution,params,expected",
        [
            ("normal", {"mu": 0.0, "sigma": 7.0}, 7.0),
            ("gamma", {"shape": 9.0, "scale": 2.0}, 6.0),
            ("poisson", {"lam": 9.0}, 3.0),
            ("bernoulli", {"p": 0.5}, 0.5),
        ],
    )
    def test_matches_the_closed_form(self, distribution, params, expected):
        assert distribution_sd(distribution, params) == pytest.approx(expected)

    def test_agrees_with_scipy_on_every_fitted_family(self):
        cases = [
            ("lognormal", {"mu": 3.0, "sigma": 0.6},
             st.lognorm(s=0.6, scale=math.exp(3.0))),
            ("negative_binomial", {"r": 8.0, "p": 0.35},
             st.nbinom(n=8.0, p=0.35)),
            ("beta_binomial", {"n": 14.0, "a": 2.5, "b": 4.5},
             st.betabinom(n=14, a=2.5, b=4.5)),
        ]
        for distribution, params, reference in cases:
            assert distribution_sd(distribution, params) == pytest.approx(
                reference.std(), rel=1e-6
            ), distribution

    def test_unknown_family_raises_rather_than_guessing(self):
        with pytest.raises(ValueError, match="Unknown distribution"):
            distribution_sd("cauchy", {"mu": 0.0, "sigma": 1.0})
