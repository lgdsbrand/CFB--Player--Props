"""N5b -- prior weighting differs by sport, and the config that says how.

CLAUDE.md §6 down-weights prior seasons because of the transfer portal and NIL:
in college, last year's production often happened at another school. That
reasoning is college-specific, and migration 0054 measured how specific.

    prior/current per-game correlation, >= 6 games each season, prod 2026-09-07
                  CFB 2022-25            NFL 2023-25
                r all  stay  move      r all  stay  move
    RB rush     0.476 0.554 0.255      0.629 0.644 0.628
    WR rec      0.440 0.569 0.206      0.672 0.693 0.535
    TE rec      0.494 0.510 0.635      0.727 0.770 0.274
    QB pass     0.377 0.404 0.346      0.373 0.542 0.119

No database. The values live in app_config; what is tested here is that the
resolution rules and the arithmetic are right, not what the numbers happen to be.
"""

from __future__ import annotations

import pytest

from worker import db
from worker.core.features import (
    CHANGED_TEAM_PRIOR_MULTIPLIER,
    PRIOR_GAMES_EQUIVALENT,
    prior_weight,
)

CFB_CEILING, CFB_MULTIPLIER = 0.5, 0.5
NFL_CEILING, NFL_MULTIPLIER = 0.75, 0.8


class TestSportSuffixedConfig:
    """`{key}_{sport}` wins; the bare key is the fallback, not the college row."""

    @pytest.fixture
    def config(self, monkeypatch):
        def apply(rows: dict[str, object]):
            monkeypatch.setattr(db, "get_config_value", lambda k: rows.get(k))
        return apply

    def test_a_suffixed_row_overrides_the_base(self, config):
        config({"prior_season_weight_max": 0.5, "prior_season_weight_max_nfl": 0.75})
        assert db.get_config_value_for_sport("prior_season_weight_max", "nfl") == 0.75

    def test_a_sport_with_no_row_falls_back_to_the_base(self, config):
        """College has no suffixed row on purpose -- the base key IS its value."""
        config({"prior_season_weight_max": 0.5, "prior_season_weight_max_nfl": 0.75})
        assert db.get_config_value_for_sport("prior_season_weight_max", "cfb") == 0.5

    def test_a_key_with_no_sport_variants_resolves_for_every_sport(self, config):
        config({"devig_method": "shin"})
        for sport in ("cfb", "nfl"):
            assert db.get_config_value_for_sport("devig_method", sport) == "shin"

    def test_a_missing_key_is_none_rather_than_an_error(self, config):
        config({})
        assert db.get_config_value_for_sport("nope", "nfl") is None

    def test_a_falsy_override_is_still_an_override(self, config):
        """0.0 means "no priors at all" and must not fall through to the base."""
        config({"prior_season_weight_max": 0.5, "prior_season_weight_max_nfl": 0.0})
        assert db.get_config_value_for_sport("prior_season_weight_max", "nfl") == 0.0


class TestPriorWeightBySport:
    def test_the_nfl_leans_harder_on_a_players_own_history(self):
        """Its priors measured r 0.63-0.73 against college's 0.44-0.49."""
        for games in (0, 1, 2, 4, 8):
            college = prior_weight(
                games, changed_team=False, ceiling=CFB_CEILING,
                changed_team_multiplier=CFB_MULTIPLIER,
            )
            nfl = prior_weight(
                games, changed_team=False, ceiling=NFL_CEILING,
                changed_team_multiplier=NFL_MULTIPLIER,
            )
            assert nfl > college

    def test_changing_team_barely_dents_an_nfl_prior(self):
        """Free agency moves a role; a transfer moves school, scheme and tier."""
        stayed = prior_weight(
            3, changed_team=False, ceiling=NFL_CEILING,
            changed_team_multiplier=NFL_MULTIPLIER,
        )
        moved = prior_weight(
            3, changed_team=True, ceiling=NFL_CEILING,
            changed_team_multiplier=NFL_MULTIPLIER,
        )
        assert moved == pytest.approx(stayed * NFL_MULTIPLIER)
        assert moved > stayed * CFB_MULTIPLIER

    def test_a_moved_nfl_player_still_outranks_a_settled_college_one(self):
        """0.600 against 0.500 entering week 1 -- the measurement's whole point."""
        nfl_moved = prior_weight(
            0, changed_team=True, ceiling=NFL_CEILING,
            changed_team_multiplier=NFL_MULTIPLIER,
        )
        cfb_stayed = prior_weight(
            0, changed_team=False, ceiling=CFB_CEILING,
            changed_team_multiplier=CFB_MULTIPLIER,
        )
        assert nfl_moved == pytest.approx(0.6)
        assert cfb_stayed == pytest.approx(0.5)
        assert nfl_moved > cfb_stayed

    def test_the_default_multiplier_is_still_college(self):
        """Every existing caller keeps its behaviour unchanged."""
        explicit = prior_weight(
            3, changed_team=True, ceiling=0.5,
            changed_team_multiplier=CHANGED_TEAM_PRIOR_MULTIPLIER,
        )
        defaulted = prior_weight(3, changed_team=True, ceiling=0.5)
        assert explicit == defaulted

    def test_decay_speed_is_NOT_sport_dependent(self):
        """0054 measured how much a prior is worth, not how fast it decays.

        Both sports halve at one games-equivalent. Inventing a second k from
        evidence that does not speak to it would be guessing dressed as
        calibration -- see the docstring on `prior_weight`.
        """
        for ceiling, mult in ((CFB_CEILING, CFB_MULTIPLIER), (NFL_CEILING, NFL_MULTIPLIER)):
            opening = prior_weight(
                0, changed_team=False, ceiling=ceiling, changed_team_multiplier=mult
            )
            halved = prior_weight(
                int(PRIOR_GAMES_EQUIVALENT), changed_team=False,
                ceiling=ceiling, changed_team_multiplier=mult,
            )
            assert halved == pytest.approx(opening / 2)

    def test_the_ceiling_still_binds_for_both_sports(self):
        for ceiling, mult in ((CFB_CEILING, CFB_MULTIPLIER), (NFL_CEILING, NFL_MULTIPLIER)):
            for n in range(0, 20):
                for changed in (True, False):
                    weight = prior_weight(
                        n, changed_team=changed, ceiling=ceiling,
                        changed_team_multiplier=mult,
                    )
                    assert 0.0 <= weight <= ceiling


class TestCalibrationProvenance:
    """A borrowed calibration must be recorded, never silently applied."""

    def test_it_carries_the_sport_it_was_measured_on(self):
        from worker.core.calibration import StoredCalibration

        snapshot = {"mean": {"a": 1.0}, "width": {"b": 2.0}}
        assert StoredCalibration(snapshot, sport="cfb").sport == "cfb"
        assert StoredCalibration(snapshot, sport="nfl").sport == "nfl"

    def test_it_defaults_to_college(self):
        """Every backtest stored before 0054 is college and none of them said so."""
        from worker.core.calibration import StoredCalibration

        assert StoredCalibration({"mean": {"a": 1.0}}).sport == "cfb"
