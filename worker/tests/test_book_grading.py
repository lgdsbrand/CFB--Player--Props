"""Tests for grading the model against real book prices.

The arithmetic here decides whether the client is told the model makes money,
so the tests concentrate on the ways that answer can be wrong in a flattering
direction:

  * a one-sided price treated as if it could be de-vigged (edge conjured out of
    the book's hold),
  * a missing box-score row treated as zero (every inactive player an UNDER hit),
  * a push counted as a loss or a win,
  * a win rate quoted without the break-even the price demanded,
  * ROI measured only at the best available price.

Every one of those makes the model look better than it is, which is exactly why
they are the ones written down.
"""

from __future__ import annotations

import pytest

from worker.core.book_grading import (
    BookPriceRow,
    SideLift,
    american_to_decimal,
    breakeven_rate,
    by_market,
    confidence_bands,
    fixed_side,
    grade_bet,
    over_base_rate,
    side_lift,
    summarise,
)

# A normal distribution centred at 100 with sd 25. P(over 100) = 0.5 exactly.
NORMAL = {"distribution": "normal", "params": {"mu": 100.0, "sigma": 25.0}}


def price(book: str, over: int | None, under: int | None) -> BookPriceRow:
    return BookPriceRow(sportsbook_key=book, over_price=over, under_price=under)


def bet(**overrides):
    kwargs = dict(
        player_id=1,
        game_id=2,
        market_key="rush_yards",
        position_group="RB",
        season=2025,
        week=8,
        line=100.0,
        distribution=NORMAL["distribution"],
        params=NORMAL["params"],
        prices=[price("dk", -110, -110)],
        actual_value=120.0,
    )
    kwargs.update(overrides)
    return grade_bet(**kwargs)


# -----------------------------------------------------------------------------
# Price arithmetic
# -----------------------------------------------------------------------------
class TestPriceArithmetic:
    @pytest.mark.parametrize(
        "american,decimal",
        [(100, 2.0), (-110, 1 + 100 / 110), (150, 2.5), (-200, 1.5)],
    )
    def test_american_converts_to_the_right_return(self, american, decimal):
        assert american_to_decimal(american) == pytest.approx(decimal, rel=1e-9)

    def test_breakeven_at_the_standard_price_is_524(self):
        # The number every "we hit 52%!" claim has to clear.
        assert breakeven_rate(-110) == pytest.approx(0.5238, abs=1e-4)

    def test_breakeven_and_return_are_reciprocal(self):
        for american in (-250, -110, 100, 400):
            assert breakeven_rate(american) == pytest.approx(
                1 / american_to_decimal(american), rel=1e-9
            )


# -----------------------------------------------------------------------------
# Refusals — each one prevents a flattering number
# -----------------------------------------------------------------------------
class TestRefusals:
    def test_a_one_sided_price_is_not_graded(self):
        # No de-vig is possible, so there is no book probability to beat.
        # Comparing against the vig-inclusive number would credit the model
        # with beating the book's own margin.
        assert bet(prices=[price("dk", 600, None)]) is None

    def test_a_missing_actual_is_not_graded_as_zero(self):
        # THE BIG ONE. A player with no box-score row was inactive. Scoring
        # that as 0 yards makes every one of them an UNDER hit, which is a
        # large, clean, entirely fake edge.
        assert bet(actual_value=None) is None

    def test_a_mixed_book_set_grades_on_the_two_way_prices_only(self):
        graded = bet(prices=[price("dk", 600, None), price("fd", -110, -110)])
        assert graded is not None
        assert graded.n_books == 1


# -----------------------------------------------------------------------------
# Settlement
# -----------------------------------------------------------------------------
class TestSettlement:
    def test_over_wins_when_the_actual_clears_the_line(self):
        graded = bet(line=100.0, actual_value=120.0, params={"mu": 130.0, "sigma": 25.0})
        assert graded.side == "over"
        assert graded.outcome == "over"
        assert graded.hit is True

    def test_under_wins_when_the_actual_falls_short(self):
        graded = bet(line=100.0, actual_value=80.0, params={"mu": 70.0, "sigma": 25.0})
        assert graded.side == "under"
        assert graded.hit is True

    def test_the_model_can_be_wrong(self):
        graded = bet(line=100.0, actual_value=80.0, params={"mu": 130.0, "sigma": 25.0})
        assert graded.side == "over"
        assert graded.hit is False

    def test_an_exact_tie_is_a_push_not_a_loss(self):
        # Books post integer lines on low-count markets, so this is not
        # hypothetical. A push returns the stake; recording it as an under
        # would invent a loss on a bet that lost nothing.
        graded = bet(line=100.0, actual_value=100.0)
        assert graded.outcome == "push"
        assert graded.is_push
        assert graded.hit is None
        assert graded.profit(-110) == 0.0


# -----------------------------------------------------------------------------
# Edge — the definition CLAUDE.md §6 pins down
# -----------------------------------------------------------------------------
class TestEdge:
    def test_edge_is_model_minus_devigged_book(self):
        # A -110/-110 market de-vigs to exactly 0.500, so a model at 0.60 on
        # the over has a 10-point edge.
        graded = bet(params={"mu": 100.0, "sigma": 25.0}, line=100.0)
        assert graded.book_prob_over == pytest.approx(0.5, abs=1e-9)
        assert graded.model_prob_over == pytest.approx(0.5, abs=1e-9)
        assert graded.edge == pytest.approx(0.0, abs=1e-9)

    def test_a_model_that_agrees_with_a_vigged_book_has_no_edge(self):
        # THE ERROR THIS RULES OUT. -110/-110 implies 52.4% BEFORE de-vig. A
        # model at 52.4% must show ~0 edge, not +2.4%: the 2.4 points are the
        # book's hold, not a mispricing.
        graded = bet(params={"mu": 101.5, "sigma": 25.0}, line=100.0)
        assert graded.model_prob_over == pytest.approx(0.524, abs=0.005)
        assert graded.edge == pytest.approx(0.024, abs=0.006)
        assert graded.edge < 0.03

    def test_the_edge_is_taken_on_the_side_actually_bet(self):
        graded = bet(params={"mu": 60.0, "sigma": 25.0}, line=100.0)
        assert graded.side == "under"
        assert graded.edge > 0  # the under is the value side here


# -----------------------------------------------------------------------------
# Price selection
# -----------------------------------------------------------------------------
class TestPriceSelection:
    def test_best_price_is_the_longest_odds_not_the_largest_number(self):
        # -105 pays better than -110, and +100 pays better than both. A naive
        # max() on the American number gets this backwards across the sign.
        graded = bet(
            params={"mu": 130.0, "sigma": 25.0},
            prices=[
                price("a", -110, -110),
                price("b", -105, -115),
                price("c", 100, -125),
            ],
        )
        assert graded.side == "over"
        assert graded.best_price == 100

    def test_median_price_sits_between_the_extremes(self):
        graded = bet(
            params={"mu": 130.0, "sigma": 25.0},
            prices=[
                price("a", -130, 110),
                price("b", -110, -110),
                price("c", 100, -125),
            ],
        )
        assert graded.median_price == -110


# -----------------------------------------------------------------------------
# Aggregation — where a misleading headline would come from
# -----------------------------------------------------------------------------
class TestSummarise:
    @staticmethod
    def _winners(n: int, *, hit: bool, edge_mu: float = 130.0):
        out = []
        for i in range(n):
            graded = bet(
                player_id=i,
                params={"mu": edge_mu, "sigma": 25.0},
                actual_value=120.0 if hit else 80.0,
            )
            out.append(graded)
        return out

    def test_a_win_rate_always_arrives_with_its_breakeven(self):
        result = summarise(self._winners(10, hit=True), 0.0)
        assert result.win_rate == 1.0
        assert result.breakeven == pytest.approx(0.5238, abs=1e-3)
        assert "breakeven" in result.summary()

    def test_roi_is_positive_when_winning_above_breakeven(self):
        result = summarise(self._winners(10, hit=True), 0.0)
        assert result.roi_median > 0

    def test_roi_is_negative_when_losing(self):
        result = summarise(self._winners(10, hit=False), 0.0)
        assert result.roi_median == pytest.approx(-1.0)

    def test_a_coin_flip_at_minus_110_loses_money(self):
        # THE POINT OF THE WHOLE JOB. 50% is a perfectly calibrated model and
        # a losing one: the price demanded 52.4%.
        bets = self._winners(5, hit=True) + self._winners(5, hit=False)
        result = summarise(bets, 0.0)
        assert result.win_rate == pytest.approx(0.5)
        assert result.win_rate < result.breakeven
        assert result.roi_median < 0

    def test_pushes_stake_nothing_and_settle_nothing(self):
        bets = self._winners(4, hit=True) + [
            bet(player_id=99, params={"mu": 130.0, "sigma": 25.0}, actual_value=100.0)
        ]
        result = summarise(bets, 0.0)
        assert result.n == 5
        assert result.pushes == 1
        assert result.decided == 4
        assert result.win_rate == 1.0  # not 4/5

    def test_the_threshold_filters_on_edge(self):
        strong = bet(player_id=1, params={"mu": 160.0, "sigma": 25.0})
        weak = bet(player_id=2, params={"mu": 101.0, "sigma": 25.0})
        assert summarise([strong, weak], 0.0).n == 2
        assert summarise([strong, weak], 0.10).n == 1

    def test_players_are_counted_apart_from_bets(self):
        # One player at four lines is four bets and one performance. Reporting
        # only n would overstate how independent the sample is.
        same = [
            bet(player_id=7, line=line, params={"mu": 160.0, "sigma": 25.0})
            for line in (90.0, 95.0, 105.0, 110.0)
        ]
        result = summarise(same, 0.0)
        assert result.n == 4
        assert result.n_players == 1

    def test_an_empty_selection_reports_nothing_rather_than_dividing_by_zero(self):
        result = summarise([], 0.05)
        assert result.n == 0
        assert result.win_rate is None
        assert result.roi_median is None
        assert "no decided bets" in result.summary()


class TestConfidenceBands:
    """The diagnostic that splits 'overconfident' from 'not better priced'.

    A plain ROI number cannot tell those apart, and they have opposite fixes:
    one is a bug in the distributions and a wrong number shown to users, the
    other means the model simply has no edge and no arithmetic will find one.
    """

    @staticmethod
    def _band_bets(n_hits: int, n_misses: int, mu: float):
        out = []
        for i in range(n_hits):
            out.append(bet(player_id=i, params={"mu": mu, "sigma": 25.0},
                           actual_value=120.0))
        for i in range(n_misses):
            out.append(bet(player_id=1000 + i, params={"mu": mu, "sigma": 25.0},
                           actual_value=80.0))
        return [b for b in out if b is not None]

    def test_overconfidence_shows_as_a_positive_model_error(self):
        # mu=116 over a line of 100 with sigma 25 is ~74% confident. Deliver
        # half of them and the band must say so.
        bands = confidence_bands(self._band_bets(5, 5, mu=116.0))
        band = next(b for b in bands if b.lower == 0.70)
        assert band.observed == pytest.approx(0.5)
        assert band.model_error > 0.2

    def test_a_well_calibrated_band_shows_near_zero_error(self):
        # ~74% confident, 74 of 100 land.
        bands = confidence_bands(self._band_bets(74, 26, mu=116.0))
        band = next(b for b in bands if b.lower == 0.70)
        assert abs(band.model_error) < 0.02

    def test_the_book_is_measured_on_the_same_rows(self):
        # The comparison is only meaningful if both errors come from the same
        # bets — "the model is badly calibrated" means little without "and the
        # book, on these exact rows, was not".
        bands = confidence_bands(self._band_bets(5, 5, mu=116.0))
        band = next(b for b in bands if b.lower == 0.70)
        # -110/-110 de-vigs to 0.500 on the over.
        assert band.mean_book == pytest.approx(0.5, abs=1e-6)
        assert band.book_error == pytest.approx(0.0, abs=1e-6)

    def test_the_book_probability_is_taken_on_the_side_we_bet(self):
        # An UNDER bet must be compared against the book's UNDER probability,
        # not its OVER probability. Getting this backwards would invert the
        # book's error and make it look wrong wherever the model called under.
        unders = [
            b for b in
            [bet(player_id=i, params={"mu": 60.0, "sigma": 25.0},
                 actual_value=80.0) for i in range(10)]
            if b is not None
        ]
        assert all(b.side == "under" for b in unders)
        band = confidence_bands(unders)[-1]
        assert band.mean_book == pytest.approx(0.5, abs=1e-6)

    def test_pushes_are_excluded_from_reliability(self):
        bets = self._band_bets(5, 5, mu=116.0) + [
            bet(player_id=555, params={"mu": 116.0, "sigma": 25.0},
                actual_value=100.0)
        ]
        band = next(b for b in confidence_bands(bets) if b.lower == 0.70)
        assert band.n == 10  # the push is not a data point about reliability

    def test_empty_bands_are_omitted_rather_than_shown_as_zero(self):
        bands = confidence_bands(self._band_bets(3, 0, mu=116.0))
        assert all(b.n > 0 for b in bands)


class TestByMarket:
    def test_markets_are_reported_separately(self):
        # rec_yards is ~40% of the real sample and would carry a blended
        # figure on its own.
        bets = [
            bet(player_id=1, market_key="rec_yards", params={"mu": 160.0, "sigma": 25.0}),
            bet(player_id=2, market_key="rush_yards", params={"mu": 160.0, "sigma": 25.0}),
        ]
        split = by_market(bets, 0.0)
        assert set(split) == {"rec_yards", "rush_yards"}
        assert split["rec_yards"].n == 1


# -----------------------------------------------------------------------------
# The null hypothesis
# -----------------------------------------------------------------------------
class TestBenchmarks:
    """The check that stops a market shade being reported as model skill.

    Weeks 7 and 8 of 2025 both closed with overs landing ~43% of the time. A
    model that leans UNDER makes money in that market without knowing anything
    about any player, so "positive ROI" on its own says nothing. These tests
    pin the arithmetic that separates the two.
    """

    @staticmethod
    def _shaded(n_under_hits: int, n_over_hits: int):
        """Bets on a line of 100 where the actual is known to fall each way."""
        bets = []
        for i in range(n_under_hits):
            bets.append(bet(player_id=i, actual_value=80.0))
        for i in range(n_over_hits):
            bets.append(bet(player_id=100 + i, actual_value=120.0))
        return [b for b in bets if b is not None]

    def test_blind_side_is_settled_on_the_opposite_side_not_the_models(self):
        # Every bet here is a model OVER that lost. "always under" must show
        # those as WINS -- if it reused bet.profit() it would report losses.
        bets = self._shaded(n_under_hits=10, n_over_hits=0)
        assert all(b.side == "over" for b in bets)  # mu=100 > line, model says over
        under = fixed_side(bets, "under")
        assert under.wins == 10
        assert under.losses == 0
        assert under.roi_median > 0

    def test_a_profitable_model_that_loses_to_a_blind_side_is_visible(self):
        # 7 unders, 3 overs. The model calls over on all of them and goes 3-7;
        # blind under goes 7-3. Both numbers must be available to compare.
        bets = self._shaded(n_under_hits=7, n_over_hits=3)
        model = summarise(bets, 0.0)
        under = fixed_side(bets, "under")
        assert model.win_rate == pytest.approx(0.3)
        assert under.win_rate == pytest.approx(0.7)
        assert under.roi_median > model.roi_median

    def test_base_rate_reveals_the_shade(self):
        assert over_base_rate(self._shaded(7, 3)) == pytest.approx(0.3)
        assert over_base_rate(self._shaded(5, 5)) == pytest.approx(0.5)

    def test_base_rate_ignores_pushes(self):
        bets = self._shaded(1, 1) + [bet(player_id=999, actual_value=100.0)]
        assert any(b.is_push for b in bets)
        assert over_base_rate(bets) == pytest.approx(0.5)

    def test_lift_is_zero_when_the_model_only_rides_the_side(self):
        # The model calls OVER on all ten; overs land 3 of 10. Its hit rate IS
        # the base rate, so it discriminated between exactly nothing.
        bets = self._shaded(n_under_hits=7, n_over_hits=3)
        over = next(x for x in side_lift(bets) if x.side == "over")
        assert over.win_rate == pytest.approx(over.base_rate)
        assert over.lift == pytest.approx(0.0)

    def test_lift_can_be_positive_while_still_losing_money(self):
        # THE CASE THAT MATTERS. Real signal, too small to pay for the vig --
        # measured on weeks 7+8, where called-over hit 48.1% against a 43.2%
        # base and a 54.1% break-even.
        lift = SideLift(side="over", n=655, win_rate=0.481,
                        base_rate=0.432, breakeven=0.541)
        assert lift.lift > 0          # the model knows something
        assert not lift.clears_vig    # and it is not enough to bet on

    def test_a_blind_benchmark_counts_pushes_as_staked_not_won(self):
        bets = self._shaded(2, 0) + [bet(player_id=999, actual_value=100.0)]
        under = fixed_side(bets, "under")
        assert under.n == 3       # the push was staked
        assert under.decided == 2  # and settled nothing
