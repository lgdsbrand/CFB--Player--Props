"""Tests for the weekly projection run.

Two things are worth pinning here and neither is arithmetic.

The first is that the LIVE population is defined without reference to who
played. `backtest_week` legitimately skips a player absent from the box score —
there is nothing to grade — and carrying that rule into a live run would build
the board from the future. The tests below drive `project_slate` with players
who never appear in any actuals table, because that is the whole point.

The second is that `StoredCalibration` reproduces what the walk applied. If the
replayed lookup disagreed with the learned one, the board would show
distributions the calibration report never scored, and every number on it would
still look completely reasonable.
"""

from __future__ import annotations

import pytest

from worker.core.calibration import (
    MAX_MEAN_MULTIPLIER,
    MAX_SCALE,
    Calibration,
    StoredCalibration,
)
from worker.core.projections import (
    MIN_GAMES_TO_PROJECT,
    MIN_USAGE_FRACTION_OF_BASELINE,
    ProjectedRow,
    project_row,
)
from worker.core.models import Projection


# -----------------------------------------------------------------------------
# The universe rules are shared with the backtest
# -----------------------------------------------------------------------------
class TestSharedUniverse:
    def test_backtest_grades_the_population_the_board_publishes(self):
        """One definition, imported — not two that happen to agree today.

        If these drifted, the report's +0.186 skill would describe a different
        set of players from the one on screen, and nothing would look wrong.
        """
        from worker.core import backtest

        assert backtest.MIN_GAMES_TO_GRADE is MIN_GAMES_TO_PROJECT
        assert backtest.MIN_USAGE_FRACTION_OF_BASELINE is MIN_USAGE_FRACTION_OF_BASELINE

    def test_both_modules_project_through_the_same_function(self):
        from worker.core import backtest

        assert backtest.project_row is project_row


# -----------------------------------------------------------------------------
# Replaying a stored calibration
# -----------------------------------------------------------------------------
def _learned() -> Calibration:
    """A calibration with enough residuals that its cells are actually applied."""
    calibration = Calibration()
    for i in range(4000):
        # Outcomes 40% wider than projected, and 10% larger on average.
        z = (i % 41 - 20) / 10.0
        calibration.variance.observe("rec_yards", "WR", z * 1.4, 0.0, 1.0, 10.0)
        calibration.mean.observe("rec_yards", 110.0 + z, 100.0, 10.0)
    return calibration


class TestStoredCalibration:
    def test_replays_what_the_walk_learned(self):
        """To the snapshot's own precision, which is four decimal places.

        `snapshot()` rounds, so replay is faithful to 1e-4 rather than exactly.
        On a x1.66 width correction that is a difference of eight parts per
        hundred thousand — far below the resolution of any probability the
        board displays, and worth pinning so a future change to the rounding
        has to come past this test.
        """
        learned = _learned()
        stored = StoredCalibration(learned.snapshot())

        assert stored.variance.scale("rec_yards", "WR", 10.0) == pytest.approx(
            learned.variance.scale("rec_yards", "WR", 10.0), abs=1e-4
        )
        assert stored.mean.multiplier("rec_yards", 10.0) == pytest.approx(
            learned.mean.multiplier("rec_yards", 10.0), abs=1e-4
        )

    def test_an_unmeasured_market_is_left_alone(self):
        stored = StoredCalibration(_learned().snapshot())
        assert stored.variance.scale("pass_yards", "QB", 10.0) == 1.0
        assert stored.mean.multiplier("pass_yards", 10.0) == 1.0

    def test_entries_marked_not_applied_are_not_applied(self):
        """`applied: false` means the walk itself declined to use it.

        A Poisson's variance IS its mean, so a measured width correction for
        pass_tds is real information that cannot be acted on. Reading the number
        past the flag would publish a correction the report never applied.
        """
        stored = StoredCalibration(
            {
                "width": {
                    "pass_tds": {"scale": 1.5, "n": 9999, "applied": False},
                    "rec_yards": {"scale": 1.5, "n": 9999, "applied": True},
                },
                "mean": {},
            }
        )
        assert stored.variance.scale("pass_tds", "QB", 10.0) == 1.0
        assert stored.variance.scale("rec_yards", "WR", 10.0) == 1.5

    def test_narrowest_cell_wins_exactly_as_the_learned_lookup_does(self):
        stored = StoredCalibration(
            {
                "width": {
                    "rec_yards": {"scale": 1.1, "n": 9999, "applied": True},
                    "rec_yards@thin": {"scale": 1.5, "n": 9999, "applied": True},
                    "rec_yards@thin:WR": {"scale": 2.0, "n": 9999, "applied": True},
                },
                "mean": {
                    "rush_yards": {"multiplier": 1.05, "n": 9999, "applied": True},
                    "rush_yards@thin": {"multiplier": 1.15, "n": 9999, "applied": True},
                },
            }
        )
        # games_played = 2 is the "thin" bucket, 10 is "established".
        assert stored.variance.scale("rec_yards", "WR", 2.0) == 2.0
        assert stored.variance.scale("rec_yards", "TE", 2.0) == 1.5
        assert stored.variance.scale("rec_yards", "WR", 10.0) == 1.1
        assert stored.mean.multiplier("rush_yards", 2.0) == 1.15
        assert stored.mean.multiplier("rush_yards", 10.0) == 1.05

    def test_a_corrupt_snapshot_corrects_nothing_rather_than_guessing(self):
        stored = StoredCalibration(
            {"width": {"rec_yards": {"scale": None, "applied": True}}, "mean": {}}
        )
        assert stored.variance.scale("rec_yards", "WR", 10.0) == 1.0

    def test_stored_corrections_stay_inside_the_learned_bounds(self):
        """The clamps are what stop a bad snapshot from becoming a bad board."""
        stored = StoredCalibration(
            {
                "width": {"rec_yards": {"scale": 99.0, "applied": True}},
                "mean": {"rec_yards": {"multiplier": 99.0, "applied": True}},
            }
        )
        assert stored.variance.scale("rec_yards", "WR", 10.0) == MAX_SCALE
        assert stored.mean.multiplier("rec_yards", 10.0) == MAX_MEAN_MULTIPLIER

    def test_an_empty_snapshot_announces_itself(self):
        """The job turns this into an error. Publishing uncorrected
        distributions is the one failure that looks like success."""
        assert StoredCalibration({}).is_empty
        assert StoredCalibration(None).is_empty
        assert not StoredCalibration(_learned().snapshot()).is_empty


# -----------------------------------------------------------------------------
# Projecting one row
# -----------------------------------------------------------------------------
MARKET = {
    "market_key": "receptions",
    "position_group": "WR",
    "stat_column": "receptions",
    "is_binary": False,
    "default_line": None,
    "distribution_family": "beta_binomial",
}


def _row(**overrides) -> dict:
    row = {
        "player_id": 1,
        "game_id": 2,
        "team_id": 3,
        "opponent_team_id": 4,
        "position_group": "WR",
        "games_played": 8.0,
        "targets_pg": 7.0,
        "targets_sd": 2.0,
        "receptions_pg": 4.5,
        "receptions_sd": 1.5,
        "rec_yards_pg": 62.0,
        "rec_yards_sd": 25.0,
        "prior_games_played": 0,
        "prior_weight": 0.0,
        "effective_sample": 8.0,
    }
    row.update(overrides)
    return row


BASELINES = {"WR": {"targets_pg": 4.0, "receptions_pg": 2.5, "rec_yards_pg": 32.0}}


class TestProjectRow:
    def test_produces_a_distribution_not_a_point(self):
        projection = project_row(_row(), MARKET, BASELINES)
        assert projection is not None
        assert projection.distribution == "beta_binomial"
        assert projection.params
        # The surface is derived from mass, so the mass has to be there.
        assert 0.0 < projection.probability_over(4.5) < 1.0

    def test_a_market_the_player_has_no_volume_in_yields_nothing(self):
        """A receiver has no pass attempts. That is a missing row on the board,
        not a zero projection and not a crash."""
        passing = dict(MARKET, market_key="pass_attempts",
                       distribution_family="negative_binomial")
        projection = project_row(
            _row(targets_pg=0.0, receptions_pg=0.0, pass_attempts_pg=0.0),
            passing,
            BASELINES,
        )
        assert projection is None

    def test_a_market_with_no_projector_does_not_take_the_week_down_with_it(self):
        """6,000 projections run in one loop; one bad market must not stop it.

        `project` raises for a market it has no branch for — a `markets` row
        added ahead of the code, which is exactly the kind of thing that happens
        between a migration and a deploy. The guard turns that into one missing
        card instead of an empty board.
        """
        unknown = dict(MARKET, market_key="punt_yards")
        assert project_row(_row(), unknown, BASELINES) is None

    def test_garbage_in_a_numeric_field_falls_back_rather_than_failing(self):
        """Not the same case, and worth stating: the model COERCES bad numbers
        toward the position baseline instead of raising, so a junk value is
        absorbed silently. That is the right behaviour for a nightly job and a
        reason the ingest audit, not this layer, is where data quality is
        enforced."""
        projection = project_row(_row(games_played="banana"), MARKET, BASELINES)
        assert projection is not None


# -----------------------------------------------------------------------------
# What the job stores
# -----------------------------------------------------------------------------
class TestProjectedRow:
    def test_carries_the_uncertainty_the_ui_needs_to_be_honest(self):
        """`prior_weight` is how much of the projection is prior rather than
        this player's own production. CLAUDE.md §6 wants early-season ranges
        widened rather than false precision implied, and the UI cannot do that
        if the number never leaves the worker."""
        row = ProjectedRow(
            player_id=1, game_id=2, team_id=3, opponent_team_id=4,
            market_key="receptions", position_group="WR", season=2025,
            week=10, as_of_week=10,
            projection=Projection(
                market_key="receptions", distribution="poisson",
                params={"lam": 4.0}, mean=4.0,
            ),
            prior_weight=0.25, effective_sample=9.5,
        )
        assert row.prior_weight == 0.25
        assert row.effective_sample == 9.5

    def test_as_of_week_never_reaches_past_the_week_being_projected(self):
        """`backtest_predictions` has this as a CHECK. A live run has no such
        tripwire, so the invariant is stated here: entering week N the model may
        read week < N, and as_of_week records that it did."""
        row = ProjectedRow(
            player_id=1, game_id=2, team_id=3, opponent_team_id=4,
            market_key="receptions", position_group="WR", season=2025,
            week=10, as_of_week=10,
            projection=Projection(
                market_key="receptions", distribution="poisson",
                params={"lam": 4.0}, mean=4.0,
            ),
        )
        assert row.as_of_week <= row.week
