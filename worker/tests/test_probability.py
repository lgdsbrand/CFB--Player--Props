"""Tests for the projection → probability core.

These pin the numbers the entire product is built on. The odds vectors are
duplicated in SQL (migration 0006) and must produce identical values there —
see the note at the top of worker/core/probability.py.
"""

from __future__ import annotations

import math

import pytest

from worker.core.probability import (
    american_to_implied_probability,
    devig_two_way,
    edge_on_side,
    prob_over,
    side_and_confidence,
    validate_params,
)


class TestAmericanOdds:
    def test_even_money_is_a_half(self):
        assert american_to_implied_probability(100) == pytest.approx(0.5)
        assert american_to_implied_probability(-100) == pytest.approx(0.5)

    def test_favourite_above_half(self):
        # -150 → 150/250
        assert american_to_implied_probability(-150) == pytest.approx(0.6)

    def test_underdog_below_half(self):
        # +150 → 100/250
        assert american_to_implied_probability(150) == pytest.approx(0.4)


class TestDevig:
    def test_standard_two_way_juice_sums_to_one(self):
        # -110 / -110 is the canonical props price. Raw implied sums to ~1.0476;
        # after de-vig both sides must be exactly 0.5.
        fair_over = devig_two_way(-110, -110)
        assert fair_over == pytest.approx(0.5)

    def test_devigged_pair_sums_to_one(self):
        fair_over = devig_two_way(-130, +110)
        assert fair_over is not None
        raw_total = american_to_implied_probability(-130) + american_to_implied_probability(110)
        assert raw_total > 1.0  # there was vig to remove
        assert fair_over < american_to_implied_probability(-130)

    def test_one_sided_price_is_not_devigable(self):
        # Must be None, never 0.0 — "no book probability" is not "no edge".
        assert devig_two_way(-110, None) is None
        assert devig_two_way(None, -110) is None


class TestEdge:
    def test_edge_on_over(self):
        # Model 62%, de-vigged book 55% → +7 points on the over.
        assert edge_on_side(0.62, 0.55, "over") == pytest.approx(0.07)

    def test_edge_on_under(self):
        # Model says over 38%, so the under is 62%; book under is 55%.
        assert edge_on_side(0.38, 0.45, "under") == pytest.approx(0.07)

    def test_no_book_probability_gives_no_edge(self):
        assert edge_on_side(0.62, None, "over") is None

    def test_edge_is_not_projection_over_line(self):
        """Guards the definition in CLAUDE.md §6.

        A model probability equal to the de-vigged book probability is zero
        edge, no matter how far the projection sits from the line.
        """
        assert edge_on_side(0.55, 0.55, "over") == pytest.approx(0.0)


class TestSideAndConfidence:
    def test_majority_over(self):
        assert side_and_confidence(0.62) == ("over", pytest.approx(0.62))

    def test_majority_under_reports_under_mass(self):
        side, confidence = side_and_confidence(0.38)
        assert side == "under"
        assert confidence == pytest.approx(0.62)

    def test_confidence_never_below_half(self):
        for p in (0.0, 0.1, 0.5, 0.9, 1.0):
            _, confidence = side_and_confidence(p)
            assert confidence >= 0.5


class TestProbOver:
    def test_normal_at_the_mean_is_half(self):
        assert prob_over("normal", {"mu": 250.0, "sigma": 60.0}, 250.0) == pytest.approx(0.5)

    def test_normal_one_sigma_above(self):
        p = prob_over("normal", {"mu": 250.0, "sigma": 60.0}, 310.0)
        assert p == pytest.approx(0.15866, abs=1e-4)

    def test_normal_is_monotonic_in_the_line(self):
        params = {"mu": 250.0, "sigma": 60.0}
        probabilities = [prob_over("normal", params, line) for line in (200, 250, 300)]
        assert probabilities[0] > probabilities[1] > probabilities[2]

    def test_poisson_half_integer_line(self):
        # P(X > 1.5) = P(X >= 2) = 1 - e^-l(1 + l)
        lam = 2.0
        expected = 1.0 - math.exp(-lam) * (1 + lam)
        assert prob_over("poisson", {"lam": lam}, 1.5) == pytest.approx(expected)

    def test_poisson_integer_line_excludes_the_push(self):
        # P(X > 1) must equal P(X >= 2), not P(X >= 1).
        lam = 2.0
        assert prob_over("poisson", {"lam": lam}, 1.0) == pytest.approx(
            prob_over("poisson", {"lam": lam}, 1.5)
        )

    def test_bernoulli_anytime_td(self):
        # The anytime-TD market: over 0.5 offensive TDs is exactly p.
        assert prob_over("bernoulli", {"p": 0.42}, 0.5) == pytest.approx(0.42)

    def test_lognormal_at_the_median(self):
        # median = exp(mu), so P(X > median) = 0.5
        params = {"mu": 4.0, "sigma": 0.5}
        assert prob_over("lognormal", params, math.exp(4.0)) == pytest.approx(0.5)

    def test_unimplemented_families_are_explicit(self):
        with pytest.raises(NotImplementedError):
            prob_over("gamma", {"shape": 2.0, "scale": 30.0}, 60.0)
        with pytest.raises(NotImplementedError):
            prob_over("negative_binomial", {"r": 4.0, "p": 0.3}, 10.5)

    def test_unknown_family_rejected(self):
        with pytest.raises(ValueError):
            prob_over("wishful", {}, 10.0)


class TestValidateParams:
    def test_missing_parameter_is_rejected(self):
        with pytest.raises(ValueError, match="missing"):
            validate_params("normal", {"mu": 250.0})

    def test_complete_parameters_pass(self):
        validate_params("normal", {"mu": 250.0, "sigma": 60.0})


class TestEndToEnd:
    def test_distribution_to_card(self):
        """The full §1 path: distribution → probability → call + confidence + edge."""
        params = {"mu": 262.0, "sigma": 55.0}
        line = 249.5

        model_prob_over = prob_over("normal", params, line)
        side, confidence = side_and_confidence(model_prob_over)
        book_prob_over = devig_two_way(-110, -110)
        edge = edge_on_side(model_prob_over, book_prob_over, side)

        assert side == "over"
        assert 0.5 < confidence < 1.0
        assert book_prob_over == pytest.approx(0.5)
        assert edge == pytest.approx(model_prob_over - 0.5)
