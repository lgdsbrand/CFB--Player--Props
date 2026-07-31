"""Tests for the two-stage volume x efficiency projection model.

Pure logic — no database. The end-to-end run against real data lives in the
Phase 3f backtest; what matters here is that the blending, the parameter
solvers and the composition behave the way the model claims they do.
"""

from __future__ import annotations

import math

import pytest
from scipy import stats as st

from worker.core.models import (
    BASELINE_PSEUDO_GAMES,
    MIN_RELATIVE_SD,
    Projection,
    _sd,
    beta_binomial_params,
    blend,
    gamma_params,
    lognormal_params,
    matchup_multiplier,
    negative_binomial_params,
    position_baselines,
    project,
    project_anytime_td,
    score_probability,
)


class TestBlend:
    def test_no_evidence_returns_the_baseline(self):
        assert blend(
            current=None, current_games=0, prior=None, prior_games=0,
            prior_weight=0.0, baseline=7.0,
        ) == pytest.approx(7.0)

    def test_heavy_current_season_dominates_the_baseline(self):
        result = blend(
            current=20.0, current_games=40, prior=None, prior_games=0,
            prior_weight=0.0, baseline=5.0,
        )
        assert result > 18.0

    def test_one_game_is_pulled_hard_toward_the_baseline(self):
        """Week 2 is why the baseline term exists at all."""
        result = blend(
            current=40.0, current_games=1, prior=None, prior_games=0,
            prior_weight=0.0, baseline=10.0,
        )
        expected = (10.0 * BASELINE_PSEUDO_GAMES + 40.0) / (BASELINE_PSEUDO_GAMES + 1)
        assert result == pytest.approx(expected)
        assert result < 20.0

    def test_prior_season_is_discounted_by_its_weight(self):
        light = blend(
            current=10.0, current_games=2, prior=30.0, prior_games=12,
            prior_weight=0.1, baseline=10.0,
        )
        heavy = blend(
            current=10.0, current_games=2, prior=30.0, prior_games=12,
            prior_weight=0.5, baseline=10.0,
        )
        assert heavy > light

    def test_zero_prior_weight_ignores_the_prior_entirely(self):
        with_prior = blend(
            current=10.0, current_games=4, prior=99.0, prior_games=12,
            prior_weight=0.0, baseline=10.0,
        )
        without = blend(
            current=10.0, current_games=4, prior=None, prior_games=0,
            prior_weight=0.0, baseline=10.0,
        )
        assert with_prior == pytest.approx(without)


class TestParameterSolvers:
    @pytest.mark.parametrize("mean,dispersion", [(5.0, 1.5), (28.0, 2.74), (11.0, 2.2)])
    def test_negative_binomial_round_trips_mean_and_dispersion(self, mean, dispersion):
        params = negative_binomial_params(mean, dispersion)
        r, p = params["r"], params["p"]
        assert r * (1 - p) / p == pytest.approx(mean, rel=1e-6)
        assert (r * (1 - p) / (p * p)) / (r * (1 - p) / p) == pytest.approx(
            dispersion, rel=1e-6
        )

    def test_negative_binomial_refuses_underdispersion(self):
        """It cannot represent variance below the mean, so it must not pretend."""
        params = negative_binomial_params(5.0, 0.4)
        r, p = params["r"], params["p"]
        mean = r * (1 - p) / p
        variance = r * (1 - p) / (p * p)
        assert variance > mean

    @pytest.mark.parametrize("mean,sd,loc", [(60.0, 25.0, 0.0), (20.0, 35.0, -40.0)])
    def test_gamma_round_trips_mean_and_sd(self, mean, sd, loc):
        params = gamma_params(mean, sd, loc)
        dist = st.gamma(a=params["shape"], loc=params["loc"], scale=params["scale"])
        assert dist.mean() == pytest.approx(mean, rel=1e-6)
        assert dist.std() == pytest.approx(sd, rel=1e-6)

    def test_gamma_with_negative_location_admits_negative_outcomes(self):
        """QB rushing: 26% of games are negative because sacks are rush losses."""
        params = gamma_params(20.0, 35.0, -60.0)
        dist = st.gamma(a=params["shape"], loc=params["loc"], scale=params["scale"])
        assert dist.cdf(0.0) > 0.15

    @pytest.mark.parametrize("mean,sd", [(50.0, 30.0), (16.0, 17.0)])
    def test_lognormal_round_trips_mean_and_sd(self, mean, sd):
        params = lognormal_params(mean, sd, 0.0)
        dist = st.lognorm(
            s=params["sigma"], loc=params["loc"], scale=math.exp(params["mu"])
        )
        assert dist.mean() == pytest.approx(mean, rel=1e-6)
        assert dist.std() == pytest.approx(sd, rel=1e-6)


class TestBetaBinomialParams:
    def test_mean_is_preserved_after_n_is_rounded(self):
        """Regression: the mean must survive rounding the trial count.

        Pairing a rounded n with the raw success rate moved the mean, and for a
        low-usage receiver moved it a lot — 0.78 expected receptions became a
        distribution certain of exactly one.
        """
        for trials, rate in ((0.78, 1.0), (4.4, 0.62), (9.6, 0.71), (1.2, 0.5)):
            params = beta_binomial_params(trials, rate, 1.0)
            dist = st.betabinom(n=params["n"], a=params["a"], b=params["b"])
            assert dist.mean() == pytest.approx(trials * rate, rel=0.02)

    def test_can_produce_underdispersion(self):
        """The property that makes it the right family for receptions."""
        params = beta_binomial_params(8.0, 0.65, 0.7)
        dist = st.betabinom(n=params["n"], a=params["a"], b=params["b"])
        assert dist.var() / dist.mean() < 1.0

    def test_low_dispersion_request_collapses_to_binomial(self):
        params = beta_binomial_params(10.0, 0.6, 0.1)
        dist = st.betabinom(n=params["n"], a=params["a"], b=params["b"])
        binomial = st.binom(n=params["n"], p=0.6)
        assert dist.var() == pytest.approx(binomial.var(), rel=0.05)

    def test_trials_never_round_below_one(self):
        params = beta_binomial_params(0.2, 0.5, 1.0)
        assert params["n"] >= 1


class TestMatchupMultiplier:
    def test_average_defense_is_neutral(self):
        assert matchup_multiplier(100.0, 100.0) == pytest.approx(1.0)

    def test_soft_defense_inflates(self):
        assert matchup_multiplier(120.0, 100.0) > 1.0

    def test_clamped_at_both_ends(self):
        # Early-season fits on a barely-connected schedule graph will occasionally
        # report a confident, spurious extreme.
        assert matchup_multiplier(1000.0, 100.0) == pytest.approx(1.4)
        assert matchup_multiplier(1.0, 100.0) == pytest.approx(0.6)

    def test_missing_data_is_neutral_not_zero(self):
        assert matchup_multiplier(None, 100.0) == 1.0
        assert matchup_multiplier(100.0, None) == 1.0
        assert matchup_multiplier(100.0, 0.0) == 1.0

    def test_shrinkage_pulls_toward_neutral(self):
        full = matchup_multiplier(130.0, 100.0, 1.0)
        weak = matchup_multiplier(130.0, 100.0, 0.2)
        assert 1.0 < weak < full


class TestStandardDeviationFloor:
    def test_collapsed_sample_sd_is_floored(self):
        """Three near-identical games must not become 99% confidence."""
        assert _sd(100.0, 0.5) == pytest.approx(MIN_RELATIVE_SD * 100.0)

    def test_genuine_sd_is_kept(self):
        assert _sd(100.0, 60.0) == pytest.approx(60.0)

    def test_missing_sd_falls_back_to_the_floor(self):
        assert _sd(100.0, None) == pytest.approx(MIN_RELATIVE_SD * 100.0)


class TestPositionBaselines:
    def test_uses_median_not_mean(self):
        """The pool includes bench players whose zeros would drag a mean down."""
        rows = [
            {"position_group": "WR", "rec_yards_pg": 0.0},
            {"position_group": "WR", "rec_yards_pg": 0.0},
            {"position_group": "WR", "rec_yards_pg": 60.0},
            {"position_group": "WR", "rec_yards_pg": 70.0},
            {"position_group": "WR", "rec_yards_pg": 80.0},
        ]
        baselines = position_baselines(rows)
        assert baselines["WR"]["rec_yards_pg"] == pytest.approx(60.0)

    def test_positions_are_kept_separate(self):
        rows = [
            {"position_group": "WR", "rec_yards_pg": 60.0},
            {"position_group": "RB", "rec_yards_pg": 15.0},
        ]
        baselines = position_baselines(rows)
        assert baselines["WR"]["rec_yards_pg"] == pytest.approx(60.0)
        assert baselines["RB"]["rec_yards_pg"] == pytest.approx(15.0)

    def test_prior_and_team_columns_are_excluded(self):
        rows = [
            {
                "position_group": "WR",
                "rec_yards_pg": 60.0,
                "prior_rec_yards_pg": 99.0,
                "team_pass_yards_pg": 250.0,
                "opp_adj_rec_yards_allowed_pg_WR": 160.0,
            }
        ]
        baselines = position_baselines(rows)["WR"]
        assert "rec_yards_pg" in baselines
        assert not any(
            k.startswith(("prior_", "team_", "opp_")) for k in baselines
        )


class TestProjectComposition:
    """Volume x efficiency must actually compose."""

    @staticmethod
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

    @staticmethod
    def _baselines():
        return {"targets_pg": 6.0, "receptions_pg": 4.0, "rec_yards_pg": 50.0}

    def test_more_production_raises_the_projection(self):
        low = project(
            self._wr_row(rec_yards_pg=30.0), "rec_yards", "gamma",
            self._baselines(), {"WR": {}},
        )
        high = project(
            self._wr_row(rec_yards_pg=110.0), "rec_yards", "gamma",
            self._baselines(), {"WR": {}},
        )
        assert low is not None and high is not None
        assert high.mean > low.mean

    def test_the_chain_telescopes_to_the_blended_outcome(self):
        """THE key identity of this model, pinned deliberately.

        targets x catch_rate x yards_per_reception collapses exactly to blended
        receiving yards, because each rate is blended with weight equal to its
        own denominator. Reshuffling volume against efficiency while holding the
        product fixed therefore changes nothing.

        This is a feature: the decomposition is a pure re-expression that cannot
        double-count. It also means the split earns its keep only through the
        opponent adjustment (applied separately to volume and efficiency), the
        distribution SHAPE it implies — receptions become beta-binomial via
        targets rather than negative binomial — and the explanation it gives the
        weekly AI read. Anyone tempted to "improve" the blend weights should
        know they are trading this guarantee away.
        """
        few_catches_long = project(
            self._wr_row(targets_pg=4.0, receptions_pg=2.5), "rec_yards", "gamma",
            self._baselines(), {"WR": {}},
        )
        many_catches_short = project(
            self._wr_row(targets_pg=12.0, receptions_pg=7.5), "rec_yards", "gamma",
            self._baselines(), {"WR": {}},
        )
        assert few_catches_long is not None and many_catches_short is not None
        assert few_catches_long.mean == pytest.approx(many_catches_short.mean)
        # Same total, reached differently — which is what gets reported.
        assert many_catches_short.volume > few_catches_long.volume
        assert many_catches_short.efficiency < few_catches_long.efficiency

    def test_volume_and_efficiency_defenses_are_distinguished(self):
        """A defense conceding catches but no yards after them is not the same
        matchup as one conceding neither."""
        league = {"WR": {
            "adj_receptions_allowed_pg": 12.0,
            "adj_rec_yards_allowed_pg": 160.0,
        }}
        # Concedes plenty of catches, but chokes the yardage.
        short = self._wr_row(
            opp_adj_receptions_allowed_pg_WR=15.0,
            opp_adj_rec_yards_allowed_pg_WR=160.0,
        )
        # Concedes the same yardage on far fewer catches — big plays.
        deep = self._wr_row(
            opp_adj_receptions_allowed_pg_WR=9.0,
            opp_adj_rec_yards_allowed_pg_WR=160.0,
        )
        a = project(short, "rec_yards", "gamma", self._baselines(), league)
        b = project(deep, "rec_yards", "gamma", self._baselines(), league)
        assert a is not None and b is not None
        # Same total yardage conceded, so similar means...
        assert a.mean == pytest.approx(b.mean, rel=0.05)
        # ...but reached by different routes.
        assert a.volume > b.volume
        assert a.efficiency < b.efficiency

    def test_volume_and_efficiency_are_reported_separately(self):
        """The AI read in Phase 5 can only explain what the model separates."""
        projection = project(
            self._wr_row(), "rec_yards", "gamma", self._baselines(), {"WR": {}}
        )
        assert projection is not None
        assert projection.volume is not None
        assert projection.efficiency is not None

    def test_probability_decreases_as_the_line_rises(self):
        projection = project(
            self._wr_row(), "rec_yards", "gamma", self._baselines(), {"WR": {}}
        )
        assert projection is not None
        probs = [projection.probability_over(x) for x in (20, 50, 80, 120)]
        assert probs == sorted(probs, reverse=True)

    def test_quantiles_are_ordered(self):
        projection = project(
            self._wr_row(), "rec_yards", "gamma", self._baselines(), {"WR": {}}
        )
        assert projection is not None
        q = projection.quantiles
        assert q["p10"] <= q["p25"] <= q["p50"] <= q["p75"] <= q["p90"]

    def test_unknown_market_is_rejected(self):
        with pytest.raises(ValueError, match="No projector"):
            project(self._wr_row(), "field_goals", "poisson", {}, {})

    def test_zero_volume_yields_no_projection(self):
        """Better no row than a fabricated league-average line on the board."""
        row = self._wr_row(targets_pg=0.0, receptions_pg=0.0, rec_yards_pg=0.0)
        projection = project(
            row, "rec_yards", "gamma",
            {"targets_pg": 0.0, "receptions_pg": 0.0, "rec_yards_pg": 0.0},
            {"WR": {}},
        )
        assert projection is None


class TestScoreProbability:
    """Touchdowns cluster, so Poisson overstates P(at least one)."""

    OBSERVED = {"RB": (0.455, 0.335), "QB": (0.370, 0.285),
                "WR": (0.298, 0.251), "TE": (0.250, 0.225)}

    def test_reproduces_observed_base_rates(self):
        """The clustering constants exist to make this true."""
        for position, (lam, observed) in self.OBSERVED.items():
            assert score_probability(lam, position) == pytest.approx(
                observed, abs=0.005
            ), position

    def test_below_poisson_where_clustering_was_measured(self):
        for position in ("RB", "QB", "WR"):
            lam = self.OBSERVED[position][0]
            assert score_probability(lam, position) < 1 - math.exp(-lam)

    def test_running_backs_are_the_worst_poisson_case(self):
        """A naive Poisson would overstate RB anytime-TD by ~3 points, which on
        the most lopsidedly priced market we serve sits on top of the 5% edge
        threshold."""
        lam = self.OBSERVED["RB"][0]
        assert (1 - math.exp(-lam)) - score_probability(lam, "RB") > 0.025

    def test_tight_ends_fall_back_to_poisson(self):
        lam = 0.25
        assert score_probability(lam, "TE") == pytest.approx(1 - math.exp(-lam))

    def test_monotonic_and_bounded(self):
        previous = -1.0
        for lam in (0.0, 0.1, 0.5, 1.0, 3.0, 10.0):
            p = score_probability(lam, "RB")
            assert 0.0 <= p <= 1.0
            assert p > previous
            previous = p

    def test_zero_expectation_is_zero_probability(self):
        assert score_probability(0.0, "RB") == 0.0

    def test_unknown_position_uses_a_default(self):
        assert 0.0 < score_probability(0.4, "K") < 1.0


class TestAnytimeTdProjection:
    @staticmethod
    def _row(**overrides):
        row = {
            "position_group": "RB",
            "games_played": 7,
            "goal_line_opportunities": 14,
            "goal_line_tds": 4,
            "open_field_opportunities": 91,
            "open_field_tds": 2,
        }
        row.update(overrides)
        return row

    @staticmethod
    def _league():
        return {"RB": {"goal_line_conversion": 0.34,
                       "open_field_conversion": 0.02}}

    def test_produces_a_bernoulli_probability(self):
        p = project_anytime_td(self._row(), {}, self._league())
        assert p is not None
        assert p.distribution == "bernoulli"
        assert 0.0 < p.params["p"] < 1.0
        # The line is 0.5, so "over" is exactly the scoring probability.
        assert p.probability_over(0.5) == pytest.approx(p.params["p"])

    def test_more_goal_line_work_raises_the_probability(self):
        few = project_anytime_td(
            self._row(goal_line_opportunities=2, goal_line_tds=0), {}, self._league()
        )
        many = project_anytime_td(
            self._row(goal_line_opportunities=28, goal_line_tds=8), {}, self._league()
        )
        assert few is not None and many is not None
        assert many.params["p"] > few.params["p"]

    def test_open_field_chances_alone_can_produce_a_scorer(self):
        """Only 28.5% of WR touchdowns start inside the ten — a pure goal-line
        model would score every deep threat at zero."""
        deep = project_anytime_td(
            self._row(
                position_group="WR",
                goal_line_opportunities=0,
                goal_line_tds=0,
                open_field_opportunities=56,
                open_field_tds=5,
            ),
            {},
            {"WR": {"goal_line_conversion": 0.5, "open_field_conversion": 0.06}},
        )
        assert deep is not None
        assert deep.params["p"] > 0.10

    def test_a_thin_sample_is_shrunk_toward_the_position_rate(self):
        """Three goal-line carries and one score is not a 33% finisher."""
        thin = project_anytime_td(
            self._row(goal_line_opportunities=3, goal_line_tds=3,
                      open_field_opportunities=0, open_field_tds=0),
            {},
            self._league(),
        )
        assert thin is not None
        # Raw rate would be 1.0; shrinkage must pull it well below.
        assert thin.efficiency < 0.55

    def test_no_opportunities_yields_no_projection(self):
        assert project_anytime_td(
            self._row(goal_line_opportunities=0, open_field_opportunities=0),
            {}, self._league(),
        ) is None

    def test_no_games_played_yields_no_projection(self):
        assert project_anytime_td(self._row(games_played=0), {}, self._league()) is None

    def test_probability_never_reaches_certainty(self):
        p = project_anytime_td(
            self._row(goal_line_opportunities=200, goal_line_tds=200), {}, self._league()
        )
        assert p is not None
        assert p.params["p"] < 1.0


class TestProjectionObject:
    def test_probability_over_delegates_to_the_distribution(self):
        projection = Projection(
            market_key="pass_yards",
            distribution="normal",
            params={"mu": 250.0, "sigma": 60.0},
            mean=250.0,
        )
        assert projection.probability_over(250.0) == pytest.approx(0.5)
