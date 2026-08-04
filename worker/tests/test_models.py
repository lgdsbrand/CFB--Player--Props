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
    MAX_PSEUDO_GAMES,
    MIN_PSEUDO_GAMES,
    MIN_RELATIVE_SD,
    Projection,
    _blended_sd,
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
    rescale,
    score_probability,
)
from worker.core.probability import distribution_median


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
        # p is derived from the mean and the ceiling, so read it back rather
        # than assuming it equals the success rate that was passed in.
        p = params["a"] / (params["a"] + params["b"])
        dist = st.betabinom(n=params["n"], a=params["a"], b=params["b"])
        binomial = st.binom(n=params["n"], p=p)
        assert dist.var() == pytest.approx(binomial.var(), rel=0.05)

    def test_trials_never_round_below_one(self):
        params = beta_binomial_params(0.2, 0.5, 1.0)
        assert params["n"] >= 1

    def test_outcomes_above_the_expected_trial_count_stay_possible(self):
        """Regression, and the worst defect the first backtest surfaced.

        n was the EXPECTED target count, so a receiver projected for 4 targets
        had ~0 probability of exceeding 4 receptions. Receivers who drew 9
        targets and caught 6 turned those near-certainties into losses:
        receptions scored log loss 3.08 and Brier skill -0.233, well worse than
        predicting the base rate. Confidently wrong is much more expensive than
        vaguely wrong.
        """
        params = beta_binomial_params(4.0, 0.65, 1.0, trials_sd=2.0)
        dist = st.betabinom(n=params["n"], a=params["a"], b=params["b"])
        # Well above the mean of 2.6 must remain reachable, not impossible.
        assert dist.sf(6) > 0.005
        assert dist.sf(4) > 0.03

    def test_ceiling_always_exceeds_the_mean(self):
        for trials, rate in ((0.5, 0.9), (2.0, 0.8), (11.0, 0.7)):
            params = beta_binomial_params(trials, rate, 1.0)
            assert params["n"] > trials * rate

    def test_mean_survives_the_larger_ceiling(self):
        for trials, rate in ((0.78, 1.0), (4.4, 0.62), (9.6, 0.71)):
            params = beta_binomial_params(trials, rate, 1.0, trials_sd=2.0)
            dist = st.betabinom(n=params["n"], a=params["a"], b=params["b"])
            assert dist.mean() == pytest.approx(trials * rate, rel=0.02)


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
            {"position_group": "WR", "games_played": 6, "rec_yards_pg": 0.0},
            {"position_group": "WR", "games_played": 6, "rec_yards_pg": 0.0},
            {"position_group": "WR", "games_played": 6, "rec_yards_pg": 60.0},
            {"position_group": "WR", "games_played": 6, "rec_yards_pg": 70.0},
            {"position_group": "WR", "games_played": 6, "rec_yards_pg": 80.0},
        ]
        baselines = position_baselines(rows)
        assert baselines["WR"]["rec_yards_pg"] == pytest.approx(60.0)

    def test_positions_are_kept_separate(self):
        rows = [
            {"position_group": "WR", "games_played": 6, "rec_yards_pg": 60.0},
            {"position_group": "RB", "games_played": 6, "rec_yards_pg": 15.0},
        ]
        baselines = position_baselines(rows)
        assert baselines["WR"]["rec_yards_pg"] == pytest.approx(60.0)
        assert baselines["RB"]["rec_yards_pg"] == pytest.approx(15.0)

    def test_prior_and_team_columns_are_excluded(self):
        rows = [
            {
                "position_group": "WR",
                "games_played": 6,
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


class TestScoringBaselines:
    """The pooled goal-line rates the anytime-TD model shrinks toward.

    An ABSENT one is not neutral: `opportunity_rate` shrinks toward
    `_chances_pg` and reads a missing baseline as zero, which is a confident
    claim that nobody at the position gets a goal-line carry.
    """

    @staticmethod
    def _row(**overrides):
        row = {
            "position_group": "RB",
            "games_played": 6,
            "goal_line_opportunities": 12,
            "goal_line_tds": 3,
            "open_field_opportunities": 60,
            "open_field_tds": 1,
        }
        row.update(overrides)
        return row

    def test_pooled_across_players_not_averaged_per_player(self):
        baselines = position_baselines([self._row(), self._row()])["RB"]
        assert baselines["goal_line_chances_pg"] == pytest.approx(2.0)
        assert baselines["goal_line_conversion"] == pytest.approx(0.25)

    def test_week_one_falls_back_to_the_prior_seasons_pool(self):
        """Every current-season pool is empty entering week 1, so without this
        the whole position shrinks toward zero opportunities."""
        rows = [
            {
                "position_group": "RB",
                "games_played": 0,
                "prior_games_played": 12,
                "prior_goal_line_opportunities": 24,
                "prior_goal_line_tds": 6,
                "prior_open_field_opportunities": 120,
                "prior_open_field_tds": 2,
            }
        ]
        baselines = position_baselines(rows)["RB"]
        assert baselines["goal_line_chances_pg"] == pytest.approx(2.0)
        assert baselines["goal_line_conversion"] == pytest.approx(0.25)

    def test_the_current_season_wins_wherever_it_exists(self):
        rows = [
            self._row(
                prior_games_played=12,
                prior_goal_line_opportunities=120,
                prior_goal_line_tds=60,
                prior_open_field_opportunities=120,
                prior_open_field_tds=60,
            )
        ]
        baselines = position_baselines(rows)["RB"]
        assert baselines["goal_line_chances_pg"] == pytest.approx(2.0)
        assert baselines["goal_line_conversion"] == pytest.approx(0.25)

    def test_a_season_with_games_but_no_play_data_sets_no_rate(self):
        """2023 was backfilled box-scores-only: prior games exist and prior
        opportunities do not. Pooling that as 0.0 chances a game would turn an
        absence of data into a claim about the position."""
        rows = [
            {
                "position_group": "RB",
                "games_played": 0,
                "prior_games_played": 12,
                "prior_goal_line_opportunities": 0,
                "prior_goal_line_tds": 0,
                "prior_open_field_opportunities": 0,
                "prior_open_field_tds": 0,
            }
        ]
        assert "goal_line_chances_pg" not in position_baselines(rows)["RB"]


def _rows(position: str, stat: str, means, sd: float, games: int = 6) -> list[dict]:
    # `games_played` is not decoration: since Phase 6b.2 a row with no
    # current-season game does not vote on the current-season baseline, because
    # the frame now carries roster players who have not played yet. Every real
    # frame row has this column, so a fixture without one was describing a row
    # that cannot exist.
    return [
        {
            "position_group": position,
            "games_played": games,
            f"{stat}_pg": mean,
            f"{stat}_sd": sd,
        }
        for mean in means
    ]


class TestEmpiricalBayesShrinkage:
    """Regression for the second defect the first backtest surfaced.

    One pseudo-count for every market was measurably wrong. Pass attempts
    separate quarterbacks enormously — a starter throws 35 a game, a backup 5 —
    while varying only moderately for the same player week to week. Shrinking a
    starter halfway toward a median that includes every backup dragged the
    projection down systematically: pass_attempts scored a Brier skill of -0.079
    and pass_completions -0.032, both WORSE than predicting the base rate.

    The shrinkage strength is now within-player variance over between-player
    variance, estimated per stat. Nothing about the fix raises if it is reverted,
    so it has to be pinned here.
    """

    def test_a_separating_stat_earns_little_shrinkage(self):
        # Players differ hugely (5 to 40 attempts); each is steady (sd 3).
        baselines = position_baselines(
            _rows("QB", "pass_attempts", [5, 8, 12, 18, 24, 30, 35, 38, 40], 3.0)
        )["QB"]
        assert baselines["pass_attempts_pg__pseudo_games"] < 1.0

    def test_a_noisy_stat_earns_heavy_shrinkage(self):
        # Players barely differ (9 to 11); each swings wildly (sd 9).
        baselines = position_baselines(
            _rows("WR", "rec_yards", [9, 9.5, 10, 10, 10, 10.5, 11, 10.2, 9.8], 9.0)
        )["WR"]
        assert baselines["rec_yards_pg__pseudo_games"] > 5.0

    def test_the_estimate_is_clamped_at_both_ends(self):
        floor = position_baselines(
            _rows("QB", "pass_attempts", [1, 6, 12, 19, 26, 33, 39, 44, 50], 0.01)
        )["QB"]["pass_attempts_pg__pseudo_games"]
        ceiling = position_baselines(
            _rows("WR", "rec_yards", [10, 10, 10, 10.1, 10, 9.9, 10, 10, 10], 50.0)
        )["WR"]["rec_yards_pg__pseudo_games"]
        assert floor == pytest.approx(MIN_PSEUDO_GAMES)
        assert ceiling == pytest.approx(MAX_PSEUDO_GAMES)

    def test_too_few_players_falls_back_rather_than_guessing(self):
        """A variance ratio from four players is noise wearing a number's
        clothes. Absent means _blend_stat uses BASELINE_PSEUDO_GAMES."""
        baselines = position_baselines(
            _rows("TE", "receptions", [1.0, 3.0, 5.0, 7.0], 2.0)
        )["TE"]
        assert "receptions_pg__pseudo_games" not in baselines

    def test_a_stat_without_a_measured_spread_is_left_alone(self):
        rows = [
            {"position_group": "QB", "pass_attempts_pg": float(m)}
            for m in (5, 8, 12, 18, 24, 30, 35, 38, 40)
        ]
        assert "pass_attempts_pg__pseudo_games" not in position_baselines(rows)["QB"]

    def test_a_starter_is_not_dragged_toward_a_backup_laden_median(self):
        """The defect itself, end to end.

        Three games into the season a 35-attempt starter must still project as a
        starter. Under the fixed pseudo-count they were pulled most of the way to
        a median that half the league's backups voted on.
        """
        baselines = position_baselines(
            _rows("QB", "pass_attempts", [5, 8, 12, 18, 24, 30, 35, 38, 40], 3.0)
        )["QB"]
        median = baselines["pass_attempts_pg"]
        estimated = baselines["pass_attempts_pg__pseudo_games"]

        def project_starter(pseudo_games: float) -> float:
            return blend(
                current=35.0, current_games=3, prior=None, prior_games=0,
                prior_weight=0.0, baseline=median, pseudo_games=pseudo_games,
            )

        assert project_starter(estimated) > project_starter(BASELINE_PSEUDO_GAMES)
        assert project_starter(estimated) > 33.0


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

    def test_no_games_and_no_prior_season_yields_no_projection(self):
        assert project_anytime_td(self._row(games_played=0), {}, self._league()) is None

    @staticmethod
    def _prior_row(**overrides):
        """A week-1 row: on a roster, nothing played yet, last season behind it."""
        row = TestAnytimeTdProjection._row(
            games_played=0,
            goal_line_opportunities=0,
            goal_line_tds=0,
            open_field_opportunities=0,
            open_field_tds=0,
            prior_games_played=12,
            prior_goal_line_opportunities=24,
            prior_goal_line_tds=6,
            prior_open_field_opportunities=120,
            prior_open_field_tds=2,
        )
        row.update(overrides)
        return row

    def test_week_one_scores_off_last_seasons_record(self):
        """Before this the market produced NOTHING on opening weekend: the
        current-season columns are all zero, so `project_anytime_td` returned
        None for every player on the board."""
        week_one = project_anytime_td(self._prior_row(), {}, self._league())
        assert week_one is not None
        assert 0.0 < week_one.params["p"] < 1.0

    def test_last_seasons_volume_still_separates_players(self):
        quiet = project_anytime_td(
            self._prior_row(
                prior_goal_line_opportunities=1, prior_goal_line_tds=0
            ),
            {}, self._league(),
        )
        heavy = project_anytime_td(
            self._prior_row(
                prior_goal_line_opportunities=40, prior_goal_line_tds=12
            ),
            {}, self._league(),
        )
        assert quiet is not None and heavy is not None
        assert heavy.params["p"] > quiet.params["p"]

    def test_a_prior_season_with_no_play_data_yields_no_projection(self):
        """2023 holds box scores and no plays, so a 2024 week-1 row has prior
        games and no prior opportunities. Nothing to project from."""
        assert project_anytime_td(
            self._prior_row(
                prior_goal_line_opportunities=0, prior_open_field_opportunities=0
            ),
            {}, self._league(),
        ) is None

    def test_a_player_who_has_played_ignores_the_prior_season(self):
        """The substitution happens only where there is nothing to blend with,
        which is what keeps every already-graded week identical."""
        played = self._row(
            prior_games_played=12,
            prior_goal_line_opportunities=99,
            prior_goal_line_tds=40,
            prior_open_field_opportunities=200,
            prior_open_field_tds=20,
        )
        with_prior = project_anytime_td(played, {}, self._league())
        without = project_anytime_td(self._row(), {}, self._league())
        assert with_prior is not None and without is not None
        assert with_prior.params["p"] == without.params["p"]

    def test_probability_never_reaches_certainty(self):
        p = project_anytime_td(
            self._row(goal_line_opportunities=200, goal_line_tds=200), {}, self._league()
        )
        assert p is not None
        assert p.params["p"] < 1.0

    @staticmethod
    def _league_with_rates():
        return {
            "WR": {
                "goal_line_conversion": 0.30,
                "open_field_conversion": 0.035,
                "goal_line_chances_pg": 0.42,
                "open_field_chances_pg": 5.1,
            }
        }

    def test_no_goal_line_history_is_not_a_claim_of_never(self):
        """The largest error the first backtest found in this market.

        Conversion rates were shrunk toward the position; opportunity COUNTS
        were not. A player with four games and no goal-line carries got a
        goal-line rate of exactly zero — a confident claim he will never get
        one. Measured on 2024-25, receivers with no goal-line work through week
        6 went on to score in 19.6% of games, tight ends 21.1%. The bottom two
        bins hold 44.6% of all anytime-TD predictions and said 6% where 16.5%
        happened.
        """
        none_yet = project_anytime_td(
            self._row(
                position_group="WR", games_played=4,
                goal_line_opportunities=0, goal_line_tds=0,
                open_field_opportunities=18, open_field_tds=1,
            ),
            {},
            self._league_with_rates(),
        )
        assert none_yet is not None
        # Some goal-line expectation must survive, and the total probability has
        # to be in the neighbourhood of what such players actually do.
        assert none_yet.params["p"] > 0.12

    def test_goal_line_history_still_moves_the_answer(self):
        """Shrinkage must not flatten the signal into a position average."""
        without = project_anytime_td(
            self._row(position_group="WR", games_played=6,
                      goal_line_opportunities=0, goal_line_tds=0,
                      open_field_opportunities=30, open_field_tds=2),
            {}, self._league_with_rates(),
        )
        heavy = project_anytime_td(
            self._row(position_group="WR", games_played=6,
                      goal_line_opportunities=9, goal_line_tds=3,
                      open_field_opportunities=30, open_field_tds=2),
            {}, self._league_with_rates(),
        )
        assert without is not None and heavy is not None
        assert heavy.params["p"] > without.params["p"] * 1.15

    def test_more_games_without_opportunity_lowers_the_estimate(self):
        """Four games with no goal-line work is thin evidence; twelve is real
        evidence of a role that does not include the goal line."""
        early = project_anytime_td(
            self._row(position_group="WR", games_played=4,
                      goal_line_opportunities=0, goal_line_tds=0,
                      open_field_opportunities=20, open_field_tds=1),
            {}, self._league_with_rates(),
        )
        late = project_anytime_td(
            self._row(position_group="WR", games_played=12,
                      goal_line_opportunities=0, goal_line_tds=0,
                      open_field_opportunities=60, open_field_tds=3),
            {}, self._league_with_rates(),
        )
        assert early is not None and late is not None
        assert late.params["p"] < early.params["p"]


class TestProjectionObject:
    def test_probability_over_delegates_to_the_distribution(self):
        projection = Projection(
            market_key="pass_yards",
            distribution="normal",
            params={"mu": 250.0, "sigma": 60.0},
            mean=250.0,
        )
        assert projection.probability_over(250.0) == pytest.approx(0.5)


class TestBlendedSd:
    """Width is blended and shrunk, not read off the current season alone.

    Phase 6a found `_sd` reading `{stat}_sd` — a column that is NULL until a
    player has two games — so weeks 1 and 2 fell to `MIN_RELATIVE_SD`, the
    narrowest width the model can produce, on the thinnest evidence of the year.
    """

    @staticmethod
    def _row(**overrides):
        # Carries a current-season level as well as a prior one, deliberately at
        # a DIFFERENT scale (80 vs 60), so a test that projects at some third
        # mean can tell which one the width was taken from.
        row = {
            "position_group": "WR",
            "games_played": 0,
            "rec_yards_pg": 80.0,
            "rec_yards_sd": 40.0,
            "prior_games_played": 12,
            "prior_weight": 0.5,
            "prior_rec_yards_pg": 60.0,
            "prior_rec_yards_sd": 42.0,  # cv 0.70, the measured WR figure
        }
        row.update(overrides)
        return row

    def test_week_one_reaches_the_prior_season_instead_of_the_floor(self):
        """THE 6a defect, pinned. Replayed by dropping the prior_ columns."""
        sd = _blended_sd(self._row(), "rec_yards", {"rec_yards_cv": 0.70}, 50.0)
        assert sd > MIN_RELATIVE_SD * 50.0 * 1.5
        assert sd == pytest.approx(0.70 * 50.0, rel=0.05)

    def test_the_prior_seasons_own_spread_moves_the_answer(self):
        """Not just 'a prior exists' — the prior's WIDTH has to be what is read.

        Both rows have zero current-season games and an identical baseline, so
        the only thing separating them is `prior_rec_yards_sd`.
        """
        baselines = {"rec_yards_cv": 0.50}
        steady = _blended_sd(
            self._row(prior_rec_yards_sd=18.0), "rec_yards", baselines, 50.0
        )
        volatile = _blended_sd(
            self._row(prior_rec_yards_sd=60.0), "rec_yards", baselines, 50.0
        )
        assert volatile > steady * 1.5

    def test_with_no_prior_and_no_current_it_falls_to_the_baseline_cv(self):
        bare = {"position_group": "WR", "games_played": 0, "prior_games_played": 0}
        assert _blended_sd(bare, "rec_yards", {"rec_yards_cv": 0.80}, 50.0) == (
            pytest.approx(0.80 * 50.0)
        )

    def test_it_scales_with_the_projected_mean_not_the_raw_one(self):
        """A receiver shrunk from 80 to 45 must not keep an 80-yard spread.

        The row's own `rec_yards_pg` is 80. Reading the width off THAT instead of
        off the projected mean is the pre-6b behaviour, and it makes the SD
        constant in the projected mean — which is what this pins against.
        """
        row = self._row(games_played=6)
        wide = _blended_sd(row, "rec_yards", {"rec_yards_cv": 0.70}, 80.0)
        narrow = _blended_sd(row, "rec_yards", {"rec_yards_cv": 0.70}, 45.0)
        assert wide == pytest.approx(narrow * 80.0 / 45.0, rel=1e-6)
        assert narrow < wide

    def test_a_full_current_season_pulls_the_width_toward_the_player(self):
        """Width sharpens as the season accumulates, exactly as the mean does."""
        baselines = {"rec_yards_cv": 0.70}
        # The same player, seen in week 1 and again with 11 games on the board.
        # Their own record says cv 0.20; prior and baseline both say 0.70.
        week_one = _blended_sd(self._row(), "rec_yards", baselines, 50.0)
        established = _blended_sd(
            self._row(
                games_played=11, rec_yards_pg=50.0, rec_yards_sd=10.0,
                prior_weight=0.13,
            ),
            "rec_yards", baselines, 50.0,
        )
        assert established < week_one
        assert 0.20 * 50.0 < established < 0.70 * 50.0

    def test_the_relative_floor_still_applies(self):
        row = self._row(
            games_played=11, rec_yards_pg=50.0, rec_yards_sd=0.01, prior_weight=0.0,
            prior_games_played=0,
        )
        sd = _blended_sd(row, "rec_yards", {"rec_yards_cv": 0.01}, 50.0)
        assert sd == pytest.approx(MIN_RELATIVE_SD * 50.0)

    def test_position_baselines_expose_a_cv(self):
        baselines = position_baselines(
            _rows("WR", "rec_yards", [40, 45, 50, 55, 60, 65, 70, 75, 80], 30.0)
        )["WR"]
        assert "rec_yards_cv" in baselines
        assert baselines["rec_yards_cv"] == pytest.approx(30.0 / 60.0, rel=0.2)


class TestLocationLift:
    """A widened right-skewed family must not report a negative median.

    Phase 5 recorded 140 rows whose stored p50 was below zero, worst -32.8.
    `rescale` sends `loc` to mean*(1-scale), which is fine until the gamma's
    shape collapses — measured on the live table, every negative-median row had
    shape below 0.617 against 4.305 for the rest.
    """

    @staticmethod
    def _collapsed_gamma(mean=28.0, sd=None):
        # sd/mean of 1.4 puts shape near 0.5, the regime that broke.
        return gamma_params(mean, sd if sd is not None else 1.4 * mean, 0.0)

    def test_the_broken_condition_reproduces_without_the_lift(self):
        """Replay: the raw location-scale result really does go negative."""
        mean = 28.0
        params = self._collapsed_gamma(mean)
        scale = 2.17  # the fitted rec_yards@thin width scale
        raw = {
            "shape": params["shape"],
            "scale": params["scale"] * scale,
            "loc": scale * params["loc"] + mean * (1.0 - scale),
        }
        assert distribution_median("gamma", raw) < 0.0

    def test_rescale_no_longer_produces_a_negative_median(self):
        mean = 28.0
        projection = Projection(
            market_key="rec_yards", distribution="gamma",
            params=self._collapsed_gamma(mean), mean=mean,
        )
        widened = rescale(projection, 2.17)
        assert distribution_median("gamma", widened.params) >= 0.0
        assert widened.quantiles["p50"] >= 0.0

    def test_it_preserves_the_mean(self):
        mean = 28.0
        projection = Projection(
            market_key="rec_yards", distribution="gamma",
            params=self._collapsed_gamma(mean), mean=mean,
        )
        widened = rescale(projection, 2.17)
        assert widened.mean == pytest.approx(mean)
        p = widened.params
        assert p["loc"] + p["shape"] * p["scale"] == pytest.approx(mean, rel=1e-6)

    def test_it_is_a_no_op_when_the_median_was_already_fine(self):
        """The location-scale identity must survive untouched on healthy rows."""
        mean = 70.0
        params = gamma_params(mean, 0.4 * mean, 0.0)
        projection = Projection(
            market_key="rec_yards", distribution="gamma", params=params, mean=mean,
        )
        widened = rescale(projection, 1.6)
        assert widened.params["shape"] == pytest.approx(params["shape"])
        assert widened.params["scale"] == pytest.approx(params["scale"] * 1.6)
        assert widened.params["loc"] == pytest.approx(mean * (1.0 - 1.6))

    def test_it_keeps_as_much_negative_tail_as_it_can(self):
        """Minimal intervention: not clamped all the way to zero."""
        mean = 28.0
        projection = Projection(
            market_key="rec_yards", distribution="gamma",
            params=self._collapsed_gamma(mean), mean=mean,
        )
        assert rescale(projection, 2.17).params["loc"] < 0.0

    def test_lognormal_is_handled_too(self):
        mean = 20.0
        params = lognormal_params(mean, 1.6 * mean, 0.0)
        projection = Projection(
            market_key="rec_yards", distribution="lognormal",
            params=params, mean=mean,
        )
        widened = rescale(projection, 2.2)
        assert distribution_median("lognormal", widened.params) >= 0.0
        assert widened.mean == pytest.approx(mean)


class TestBaselinesWithoutACurrentSeason:
    """Entering week 1 the frame is all roster rows and no current season.

    Before Phase 6b.2 `build_feature_frame` returned nothing at all in that
    state, so none of this could arise. Now it can, and a baseline is what a
    projection with no evidence of its own shrinks toward — if it comes out
    empty the whole opening-weekend board silently produces zero projections.
    """

    @staticmethod
    def _week_one_rows(n: int = 12):
        return [
            {
                "position_group": "WR",
                "games_played": 0,
                "prior_rec_yards_pg": 20.0 + i * 5,
                "prior_rec_yards_sd": (20.0 + i * 5) * 0.7,
                "prior_receptions_pg": 2.0 + i * 0.3,
                "prior_receptions_sd": 1.5,
            }
            for i in range(n)
        ]

    def test_a_week_one_frame_still_produces_a_baseline(self):
        baselines = position_baselines(self._week_one_rows())["WR"]
        assert baselines["rec_yards_pg"] == pytest.approx(47.5)
        assert baselines["receptions_pg"] == pytest.approx(3.65)

    def test_it_produces_a_cv_baseline_too(self):
        """Without this the week-1 width falls to MIN_RELATIVE_SD — the 6a defect."""
        baselines = position_baselines(self._week_one_rows())["WR"]
        assert baselines["rec_yards_cv"] == pytest.approx(0.70, abs=0.01)

    def test_a_row_with_no_current_season_does_not_vote_on_the_current_median(self):
        """The rule that keeps established weeks byte-identical.

        A roster row knows nothing about this season, so letting it into the
        current-season median would move every established week's baselines the
        moment the universe widened.
        """
        # At least MIN_BASELINE_SAMPLES of them, or the prior fallback fires for
        # a different and legitimate reason and the test proves nothing.
        played = _rows("WR", "rec_yards", [40.0 + i for i in range(10)], 20.0)
        # The roster rows carry an explicit ZERO, not a null. Today the frame
        # gives newcomers nulls, which `_value` skips anyway — so this pins the
        # guard against the day someone fills them with 0.0 instead and silently
        # collapses every position baseline toward the bench.
        with_roster = played + [
            {
                "position_group": "WR",
                "games_played": 0,
                "rec_yards_pg": 0.0,
                "prior_rec_yards_pg": 5.0,
            }
            for _ in range(50)
        ]
        assert (
            position_baselines(with_roster)["WR"]["rec_yards_pg"]
            == position_baselines(played)["WR"]["rec_yards_pg"]
        )

    def test_an_established_position_ignores_the_prior_fallback(self):
        """The prior only fills a gap; it never overrides a real sample."""
        rows = _rows("WR", "rec_yards", [40.0] * 10, 12.0)
        for row in rows:
            row["prior_rec_yards_pg"] = 999.0
        assert position_baselines(rows)["WR"]["rec_yards_pg"] == pytest.approx(40.0)

    def test_the_league_mean_of_a_defensive_allowance_survives(self):
        """REGRESSION, and a silent one.

        `defensive_ratio` divides an opponent's allowance by the league mean of
        the same quantity, and reads that mean out of this dict. Filtering the
        `_allowed_pg` columns out as clutter left every matchup multiplier at
        1.0 — the position-split signal CLAUDE.md §5 calls the core of the model,
        switched off with no error and a perfectly plausible-looking board.
        """
        rows = [
            {
                "position_group": "WR",
                "games_played": 6,
                "rec_yards_pg": 60.0,
                "adj_rec_yards_allowed_pg": 150.0 + i,
            }
            for i in range(10)
        ]
        assert "adj_rec_yards_allowed_pg" in position_baselines(rows)["WR"]
