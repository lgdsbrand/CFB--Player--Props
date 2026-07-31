"""Tests for the projection → probability core.

These pin the numbers the entire product is built on. The odds vectors are
duplicated in SQL (migration 0006) and must produce identical values there —
see the note at the top of worker/core/probability.py.
"""

from __future__ import annotations

import math

import pytest

import itertools

import scipy.stats as st

from worker.core.probability import (
    DEFAULT_DEVIG_METHOD,
    american_to_implied_probability,
    consensus_book_probability,
    devig_two_way,
    edge_on_side,
    prob_over,
    side_and_confidence,
    solve_shin_z,
    validate_params,
)

# Real-world American prices spanning heavy favourites to long longshots. Used
# to sweep properties that must hold across the whole price surface, not just at
# the -110/-110 point where every method agrees anyway.
PRICE_LADDER = [
    -5000, -2000, -1100, -600, -300, -190, -150, -130, -110, -105,
    100, 105, 110, 130, 150, 190, 300, 600, 1100, 2000, 5000,
]


def _coherent_pairs():
    """Price pairs that form a real vigged market (implied total >= 1)."""
    for over, under in itertools.product(PRICE_LADDER, PRICE_LADDER):
        total = (
            american_to_implied_probability(over)
            + american_to_implied_probability(under)
        )
        if total >= 1.0:
            yield over, under


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

    def test_default_method_is_shin(self):
        assert DEFAULT_DEVIG_METHOD == "shin"
        assert devig_two_way(-150, 130) == pytest.approx(
            devig_two_way(-150, 130, "shin")
        )

    def test_unknown_method_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown de-vig method"):
            devig_two_way(-110, -110, "wishful")  # type: ignore[arg-type]

    @pytest.mark.parametrize("method", ["proportional", "additive", "shin"])
    def test_every_method_returns_a_coherent_pair(self, method):
        """Whatever the method, the two fair sides must sum to exactly 1."""
        for over, under in _coherent_pairs():
            fair_over = devig_two_way(over, under, method)
            fair_under = devig_two_way(under, over, method)
            if fair_over is None or fair_under is None:
                continue
            assert fair_over + fair_under == pytest.approx(1.0, abs=1e-9), (
                f"{method} at {over:+}/{under:+}"
            )

    @pytest.mark.parametrize("method", ["proportional", "additive", "shin"])
    def test_every_method_strips_vig_from_the_favourite(self, method):
        """De-vigging must always REDUCE the favourite's implied probability."""
        for over, under in _coherent_pairs():
            fair = devig_two_way(over, under, method)
            if fair is None:
                continue
            raw = american_to_implied_probability(over)
            total = raw + american_to_implied_probability(under)
            if total > 1.0 + 1e-12 and raw > 0.5:
                assert fair < raw, f"{method} at {over:+}/{under:+}"


class TestShinEqualsAdditiveForTwoWay:
    """Shin and additive are the same number for a TWO-outcome market.

    Proved in the header of worker/core/probability.py and again in migration
    0013. Pinned here because it is the fact that justifies implementing Shin in
    SQL as a closed form rather than as bisection, and because a regression in
    either implementation would show up as this test failing rather than as
    silently divergent edges.
    """

    def test_identity_holds_across_the_price_surface(self):
        checked = 0
        for over, under in _coherent_pairs():
            shin = devig_two_way(over, under, "shin")
            additive = devig_two_way(over, under, "additive")
            assert (shin is None) == (additive is None)
            if shin is None:
                continue
            checked += 1
            assert shin == pytest.approx(additive, abs=1e-9), f"{over:+}/{under:+}"
        assert checked > 200, "price sweep did not actually exercise anything"

    def test_proportional_genuinely_differs_on_lopsided_prices(self):
        """The identity would be uninteresting if all three agreed everywhere."""
        # A longshot anytime-TD price: proportional 13.5%, shin 11.3%.
        proportional = devig_two_way(600, -1100, "proportional")
        shin = devig_two_way(600, -1100, "shin")
        assert proportional == pytest.approx(0.13483, abs=1e-5)
        assert shin == pytest.approx(0.11310, abs=1e-5)
        # Big enough on its own to move a pick across the 5% edge threshold.
        assert proportional - shin > 0.02

    def test_methods_converge_near_even_money(self):
        # At -110/-110 the choice of method is immaterial, which is why it does
        # not matter for yardage and reception props.
        values = {
            devig_two_way(-110, -110, m)
            for m in ("proportional", "additive", "shin")
        }
        assert max(values) - min(values) < 1e-12


class TestShinZ:
    def test_z_is_zero_without_overround(self):
        # No vig to explain, so no informed-money share to infer.
        assert solve_shin_z((0.5, 0.5)) == pytest.approx(0.0)

    def test_z_matches_the_closed_form(self):
        """z from bisection must match the algebraic solution used in SQL.

        Closed form: z = (pi1^2/PI - p1^2) / (p1 * p2).
        """
        for over, under in _coherent_pairs():
            raw_over = american_to_implied_probability(over)
            raw_under = american_to_implied_probability(under)
            total = raw_over + raw_under
            if total <= 1.0 + 1e-9:
                continue
            fair_over = devig_two_way(over, under, "shin")
            assert fair_over is not None
            fair_under = 1.0 - fair_over
            expected = (
                (raw_over * raw_over / total) - fair_over * fair_over
            ) / (fair_over * fair_under)
            assert solve_shin_z((raw_over, raw_under)) == pytest.approx(
                expected, abs=1e-8
            ), f"{over:+}/{under:+}"

    def test_z_tracks_the_hold(self):
        # A juicier market implies a larger informed-money share.
        light = solve_shin_z(
            (
                american_to_implied_probability(-105),
                american_to_implied_probability(-105),
            )
        )
        heavy = solve_shin_z(
            (
                american_to_implied_probability(-130),
                american_to_implied_probability(-130),
            )
        )
        assert heavy > light


class TestIncoherentMarket:
    """A two-way total below 1 is not a market — it is a data error."""

    def test_total_below_one_is_rejected(self):
        # +600 / +5000 implies 0.143 + 0.020 = 0.16. Free money does not exist;
        # this is a mispull or two crossed lines.
        for method in ("proportional", "additive", "shin"):
            assert devig_two_way(600, 5000, method) is None, method

    def test_zero_vig_market_is_accepted(self):
        # +100 / -100 sums to exactly 1.0 — coherent, just with no margin.
        for method in ("proportional", "additive", "shin"):
            assert devig_two_way(100, -100, method) == pytest.approx(0.5), method


class TestConsensusBookProbability:
    def test_median_of_three_books(self):
        prices = [(-115, -105), (-120, 100), (-110, -110)]
        expected = sorted(devig_two_way(o, u) for o, u in prices)[1]
        assert consensus_book_probability(prices) == pytest.approx(expected)

    def test_even_count_averages_the_middle_two(self):
        prices = [(-110, -110), (-130, 110)]
        fair = sorted(devig_two_way(o, u) for o, u in prices)
        assert consensus_book_probability(prices) == pytest.approx(
            0.5 * (fair[0] + fair[1])
        )

    def test_one_sided_books_are_skipped_not_counted(self):
        # The two-way book alone decides the consensus.
        prices = [(600, None), (-110, -110), (None, 250)]
        assert consensus_book_probability(prices) == pytest.approx(0.5)

    def test_no_two_way_price_anywhere_gives_none(self):
        assert consensus_book_probability([(600, None), (550, None)]) is None

    def test_empty_input_gives_none(self):
        assert consensus_book_probability([]) is None

    def test_single_book_is_just_that_book(self):
        assert consensus_book_probability([(-150, 130)]) == pytest.approx(
            devig_two_way(-150, 130)
        )


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

    def test_gamma_matches_scipy(self):
        # Receiving yards: non-negative and right-skewed.
        params = {"shape": 4.0, "scale": 20.0}
        assert prob_over("gamma", params, 60.5) == pytest.approx(
            st.gamma.sf(60.5, a=4.0, scale=20.0)
        )

    def test_gamma_at_its_mean_is_below_half(self):
        # mean = shape * scale = 80; a right-skewed distribution has its median
        # below its mean, so P(X > mean) < 0.5.
        assert prob_over("gamma", {"shape": 4.0, "scale": 20.0}, 80.0) < 0.5

    def test_gamma_below_zero_is_certain(self):
        # Yardage cannot be negative, so any non-positive line is a lock.
        assert prob_over("gamma", {"shape": 4.0, "scale": 20.0}, 0.0) == 1.0
        assert prob_over("gamma", {"shape": 4.0, "scale": 20.0}, -5.0) == 1.0

    def test_gamma_is_monotonic_in_the_line(self):
        params = {"shape": 4.0, "scale": 20.0}
        values = [prob_over("gamma", params, line) for line in (40, 80, 120)]
        assert values[0] > values[1] > values[2]

    def test_negative_binomial_matches_scipy(self):
        params = {"r": 5.0, "p": 0.45}
        assert prob_over("negative_binomial", params, 6.5) == pytest.approx(
            st.nbinom.sf(6, n=5.0, p=0.45)
        )

    def test_negative_binomial_integer_line_excludes_the_push(self):
        # P(X > 6) must equal P(X > 6.5), not P(X >= 6).
        params = {"r": 5.0, "p": 0.45}
        assert prob_over("negative_binomial", params, 6.0) == pytest.approx(
            prob_over("negative_binomial", params, 6.5)
        )

    def test_negative_binomial_is_overdispersed_relative_to_poisson(self):
        """The reason count markets use it instead of Poisson.

        Game-to-game usage varies with game script far more than a Poisson
        allows, so the negative binomial must put more mass in the upper tail at
        the same mean.
        """
        r, p = 5.0, 0.45
        mean = r * (1 - p) / p  # scipy's parameterization
        far = mean * 2.5
        nb = prob_over("negative_binomial", {"r": r, "p": p}, far)
        poisson = prob_over("poisson", {"lam": mean}, far)
        assert nb > poisson

    def test_gamma_location_shifts_the_floor(self):
        """26% of QB rushing games are negative — sacks are charged as rushes.

        Without a free location a gamma cannot describe that at all, which is
        why migration 0009 had settled for normal.
        """
        params = {"shape": 3.0, "scale": 15.0, "loc": -20.0}
        # Support now starts at -20, so a line there is a certainty.
        assert prob_over("gamma", params, -20.0) == 1.0
        # And negative outcomes carry real mass.
        assert 0.0 < prob_over("gamma", params, -5.0) < 1.0
        assert prob_over("gamma", params, -5.0) == pytest.approx(
            st.gamma.sf(-5.0, a=3.0, loc=-20.0, scale=15.0)
        )

    def test_gamma_without_location_is_unchanged(self):
        """The parameter is optional; two-parameter dicts must still work."""
        assert prob_over("gamma", {"shape": 4.0, "scale": 20.0}, 60.5) == pytest.approx(
            prob_over("gamma", {"shape": 4.0, "scale": 20.0, "loc": 0.0}, 60.5)
        )

    def test_lognormal_location_shifts_the_floor(self):
        params = {"mu": 3.0, "sigma": 0.6, "loc": -10.0}
        assert prob_over("lognormal", params, -10.0) == 1.0
        assert prob_over("lognormal", params, 5.0) == pytest.approx(
            st.lognorm.sf(5.0, s=0.6, loc=-10.0, scale=math.exp(3.0))
        )

    def test_beta_binomial_matches_scipy(self):
        params = {"n": 8, "a": 4.0, "b": 3.0}
        assert prob_over("beta_binomial", params, 4.5) == pytest.approx(
            st.betabinom.sf(4, n=8, a=4.0, b=3.0)
        )

    def test_beta_binomial_integer_line_excludes_the_push(self):
        params = {"n": 8, "a": 4.0, "b": 3.0}
        assert prob_over("beta_binomial", params, 4.0) == pytest.approx(
            prob_over("beta_binomial", params, 4.5)
        )

    def test_beta_binomial_cannot_exceed_its_trial_count(self):
        params = {"n": 6, "a": 2.0, "b": 2.0}
        # A receiver cannot catch more passes than were thrown at them.
        assert prob_over("beta_binomial", params, 6.5) == pytest.approx(0.0)

    def test_beta_binomial_can_be_underdispersed(self):
        """The property that rules negative binomial out for receptions.

        Measured within player, receptions have variance/mean of roughly 0.53
        (RB) to 0.95 (WR). A negative binomial's variance always EXCEEDS its
        mean, so it cannot reach that range at any parameterization.
        """
        n, a, b = 8, 20.0, 12.0
        mean = n * a / (a + b)
        variance = (
            n * a * b * (a + b + n) / ((a + b) ** 2 * (a + b + 1))
        )
        assert variance / mean < 1.0

        # And a negative binomial with the same mean cannot get there.
        r, p = 5.0, 0.5
        nb_mean = r * (1 - p) / p
        nb_variance = r * (1 - p) / (p * p)
        assert nb_variance / nb_mean > 1.0

    def test_beta_binomial_requires_all_three_parameters(self):
        with pytest.raises(ValueError, match="missing"):
            validate_params("beta_binomial", {"n": 8, "a": 4.0})

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
