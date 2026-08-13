"""The alternate-line ladder.

Two properties matter more than any individual number, and both are asserted
against every family in live use rather than against a chosen example:

  * probabilities must be NON-INCREASING as the line rises. A ladder that rises
    somewhere is reporting a mismatch between the declared family and its params,
    and it would look plausible on a card.
  * every rung must be a line a book could post -- a half-integer on the market's
    own grid -- so a reader comparing a rung to a real price is comparing like
    with like.
"""

from __future__ import annotations

import pytest

from worker.core.ladder import (
    MAX_RUNGS,
    MIN_RUNGS,
    build_ladder,
    ladder_json,
    rung_lines,
)

# One realistic parameter set per family actually present in `projections`,
# measured shapes rather than round numbers. rec_yards appears as BOTH gamma and
# lognormal on live rows, which is why both are here.
FAMILIES: dict[str, dict[str, float]] = {
    "normal": {"mu": 247.0, "sigma": 58.0},
    "lognormal": {"mu": 3.1, "sigma": 0.9},
    "gamma": {"shape": 1.4, "scale": 24.0},
    "poisson": {"lam": 1.7},
    "negative_binomial": {"r": 4.2, "p": 0.31},
    "beta_binomial": {"n": 9, "a": 2.2, "b": 4.1},
}

# The seeded steps from migration 0039, so the tests exercise the real grid.
STEPS = {
    "pass_yards": 25.0,
    "pass_tds": 1.0,
    "pass_attempts": 5.0,
    "pass_completions": 5.0,
    "rush_yards": 10.0,
    "rush_attempts": 2.0,
    "receptions": 1.0,
    "rec_yards": 10.0,
}


class TestRungLines:
    def test_every_rung_is_a_half_integer(self):
        """Books post 60.5, never 60. Also removes the push case outright."""
        for step in STEPS.values():
            for line in rung_lines(step, 0.0, 100.0):
                assert line % 1 == pytest.approx(0.5), f"step {step} gave {line}"

    def test_rungs_are_ascending_and_unique(self):
        lines = rung_lines(10.0, 5.0, 95.0)
        assert lines == sorted(lines)
        assert len(set(lines)) == len(lines)

    def test_the_cap_is_respected(self):
        """The widest real case: passing yards average a 262-yard p10-p90 spread
        against a 25-yard step, which would otherwise be 10+ rungs."""
        assert len(rung_lines(25.0, 40.0, 302.0)) <= MAX_RUNGS

    def test_a_wide_window_is_covered_not_truncated(self):
        """Coverage beats nominal spacing: the ladder must still reach the top of
        the window after widening, or it fails at its only job."""
        low, high = 40.0, 302.0
        lines = rung_lines(25.0, low, high)
        # Within one widened step of each end, which is as close as a grid gets.
        assert lines[0] <= low + 50.0
        assert lines[-1] >= high - 50.0

    def test_a_widened_step_stays_on_the_original_grid(self):
        """A coarse rung must still be a point a book could post, not an
        arbitrary number produced by dividing the range."""
        for line in rung_lines(25.0, 40.0, 402.0):
            assert (line - 0.5) % 25.0 == pytest.approx(0.0)

    def test_a_narrow_window_is_extended_upward(self):
        """Two rungs read as an accident. Extended UP, because "how high can I
        push it" is the question the feature exists to answer."""
        lines = rung_lines(10.0, 12.0, 18.0)
        assert len(lines) >= MIN_RUNGS
        assert lines == sorted(lines)

    def test_no_rung_below_the_market_floor(self):
        """QB rushing goes negative -- NCAA charges a sack as a rushing loss -- so
        the distribution can, but no book posts "over -5.5 rushing yards"."""
        for line in rung_lines(10.0, -40.0, 30.0):
            assert line >= 0.5

    def test_a_window_between_two_grid_points_still_yields_a_rung(self):
        """A very tight distribution must render something rather than nothing."""
        lines = rung_lines(25.0, 60.0, 61.0)
        assert len(lines) >= 1

    def test_a_reversed_window_is_tolerated(self):
        assert rung_lines(10.0, 90.0, 10.0) == rung_lines(10.0, 10.0, 90.0)

    def test_a_zero_step_raises_rather_than_hanging(self):
        with pytest.raises(ValueError, match="step must be positive"):
            rung_lines(0.0, 10.0, 90.0)

    def test_a_negative_step_raises(self):
        with pytest.raises(ValueError, match="step must be positive"):
            rung_lines(-5.0, 10.0, 90.0)


class TestBuildLadder:
    @pytest.mark.parametrize("family", sorted(FAMILIES))
    def test_probability_never_rises_with_the_line(self, family: str):
        """THE invariant. A rise means the family and its params disagree, and
        nothing on a card would reveal it."""
        rungs = build_ladder(
            family, FAMILIES[family], 5.0, low=0.0, high=60.0
        )
        probs = [r.prob_over for r in rungs]
        assert probs == sorted(probs, reverse=True), f"{family}: {probs}"

    @pytest.mark.parametrize("family", sorted(FAMILIES))
    def test_every_probability_is_a_probability(self, family: str):
        for rung in build_ladder(family, FAMILIES[family], 5.0, low=0.0, high=60.0):
            assert 0.0 <= rung.prob_over <= 1.0

    def test_a_market_with_no_step_gets_no_ladder(self):
        """anytime_td is a single probability by construction; a ladder of it
        would be the same number repeated."""
        assert build_ladder("bernoulli", {"p": 0.42}, None, low=0.0, high=1.0) == []

    def test_bad_params_raise_rather_than_producing_a_number(self):
        with pytest.raises(ValueError, match="requires"):
            build_ladder("normal", {"mu": 100.0}, 10.0, low=0.0, high=200.0)

    def test_an_unknown_family_raises(self):
        with pytest.raises(ValueError, match="Unknown distribution family"):
            build_ladder("weibull", {"mu": 1.0}, 10.0, low=0.0, high=10.0)

    def test_the_ladder_brackets_the_median(self):
        """The client's actual question -- book line 60, model says 90 -- only
        reads if the rungs span where the model thinks the outcome lands."""
        params = {"mu": 90.0, "sigma": 20.0}
        rungs = build_ladder("normal", params, 10.0, low=64.0, high=116.0)
        assert rungs[0].prob_over > 0.5 > rungs[-1].prob_over

    def test_the_rung_nearest_the_median_is_near_fifty_percent(self):
        params = {"mu": 90.0, "sigma": 20.0}
        rungs = build_ladder("normal", params, 10.0, low=64.0, high=116.0)
        nearest = min(rungs, key=lambda r: abs(r.line - 90.0))
        assert nearest.prob_over == pytest.approx(0.5, abs=0.05)


class TestLadderJson:
    def test_the_stored_shape(self):
        rungs = build_ladder(
            "normal", {"mu": 90.0, "sigma": 20.0}, 10.0, low=70.0, high=110.0
        )
        stored = ladder_json(rungs)
        assert stored is not None
        assert all(set(r) == {"line", "prob_over"} for r in stored)
        assert [r["line"] for r in stored] == sorted(r["line"] for r in stored)

    def test_probabilities_are_rounded_for_storage(self):
        stored = ladder_json(
            build_ladder("normal", {"mu": 90.0, "sigma": 20.0}, 10.0, low=70.0, high=110.0)
        )
        assert stored is not None
        for rung in stored:
            assert rung["prob_over"] == round(rung["prob_over"], 4)

    def test_an_empty_ladder_stores_as_null_not_an_empty_array(self):
        """The column means "no ladder for this market". An empty array would read
        as "a ladder was computed and came out empty", which says something else
        entirely."""
        assert ladder_json([]) is None
