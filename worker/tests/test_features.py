"""Lookahead tests for point-in-time feature assembly.

THE POINT OF THIS FILE
----------------------
Every other Phase 3 failure is visible. Lookahead is not: it produces a model
that looks excellent and is worthless, and it would invalidate the calibration
report that gates the client review (CLAUDE.md §4).

`worker/core/features.py` guards itself — every aggregate reports its own
high-water mark and raises `LookaheadError` before returning. That guard is only
as good as the predicate that produced the number it checks, so these tests
**re-derive the bound independently**: they go back to the source tables and ask
"what is the latest week anything in this result could possibly have come from",
without reusing the module's own SQL. A predicate and its check must not share a
single point of failure.

Pure-logic tests run anywhere. The database tests skip without SUPABASE_DB_URL.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from worker.core.features import (
    CHANGED_TEAM_PRIOR_MULTIPLIER,
    PRIOR_GAMES_EQUIVALENT,
    PRIOR_SCORING_WEEKS,
    SKILL_POSITIONS,
    AsOf,
    LookaheadError,
    _assert_no_lookahead,
    _assert_snapshot_cutoff,
    build_feature_frame,
    player_goal_line_usage,
    prior_goal_line_column_names,
    prior_goal_line_usage,
    prior_weight,
    roster_universe,
)

psycopg = pytest.importorskip("psycopg")

from psycopg.rows import dict_row  # noqa: E402

from worker.config import ConfigError, get_settings  # noqa: E402


@pytest.fixture
def conn():
    try:
        url = get_settings().database_url
    except ConfigError:
        pytest.skip("SUPABASE_DB_URL not set — integration tests need a database")

    with psycopg.connect(url, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def populated_week(conn):
    """A (season, week) with real ingested data, chosen from the database.

    Picked at runtime rather than hardcoded: pinning 2024 week 8 would turn a
    change in backfill scope into a mysterious test failure. Week >= 4 so there
    is genuine prior-week history to leak.
    """
    row = conn.execute(
        """
        select g.season, g.week, count(*) as games
          from games g
         where g.week >= 4
           -- full-ingest seasons only. EXISTS, not a join: joining games to
           -- plays on season alone multiplies each game by every play in that
           -- season, which is 300k+ rows of cartesian product.
           and exists (select 1 from plays p where p.season = g.season)
         group by g.season, g.week
         order by count(*) desc, g.season desc
         limit 1
        """
    ).fetchone()
    if not row:
        pytest.skip("no ingested games to test against")
    return AsOf(season=int(row["season"]), week=int(row["week"]))


# =============================================================================
# Pure logic — no database
# =============================================================================
class TestAsOf:
    def test_rejects_week_zero(self):
        with pytest.raises(ValueError, match="as-of week"):
            AsOf(season=2025, week=0)

    def test_rejects_implausible_season(self):
        with pytest.raises(ValueError, match="implausible season"):
            AsOf(season=25, week=3)

    def test_prior_season_is_the_one_before(self):
        assert AsOf(season=2025, week=3).prior_season == 2024

    def test_is_hashable_and_frozen(self):
        # Used as a dict key when caching feature frames per cutoff.
        assert len({AsOf(2025, 3), AsOf(2025, 3)}) == 1
        # FrozenInstanceError specifically: a bare Exception would also pass if
        # AsOf lost its @dataclass and the assignment failed for some other reason.
        with pytest.raises(FrozenInstanceError):
            AsOf(2025, 3).week = 4  # type: ignore[misc]


class TestLookaheadGuard:
    def test_rejects_a_row_from_the_prediction_week(self):
        """week == as_of_week is already too late: that week's results."""
        with pytest.raises(LookaheadError, match="week 6"):
            _assert_no_lookahead(
                [{"player_id": 1, "max_source_week": 6}], AsOf(2025, 6), "t"
            )

    def test_rejects_a_row_from_after_the_prediction_week(self):
        with pytest.raises(LookaheadError):
            _assert_no_lookahead(
                [{"player_id": 1, "max_source_week": 9}], AsOf(2025, 6), "t"
            )

    def test_accepts_the_week_immediately_before(self):
        rows = [{"player_id": 1, "max_source_week": 5}]
        assert _assert_no_lookahead(rows, AsOf(2025, 6), "t") == rows

    def test_null_high_water_is_not_a_violation(self):
        # A player with no games yet aggregates to NULL, which is legitimate.
        rows = [{"player_id": 1, "max_source_week": None}]
        assert _assert_no_lookahead(rows, AsOf(2025, 6), "t") == rows

    def test_error_names_the_cutoff_and_the_offending_week(self):
        with pytest.raises(LookaheadError) as exc:
            _assert_no_lookahead(
                [{"player_id": 42, "max_source_week": 7}], AsOf(2025, 6), "player_usage"
            )
        message = str(exc.value)
        assert "player_usage" in message
        assert "2025w6" in message

    def test_snapshot_cutoff_must_match_exactly(self):
        # A LARGER as_of_week imports later knowledge; a smaller one is stale.
        with pytest.raises(LookaheadError):
            _assert_snapshot_cutoff([{"as_of_week": 7}], AsOf(2025, 6), "t")
        with pytest.raises(LookaheadError):
            _assert_snapshot_cutoff([{"as_of_week": 5}], AsOf(2025, 6), "t")
        rows = [{"as_of_week": 6}]
        assert _assert_snapshot_cutoff(rows, AsOf(2025, 6), "t") == rows


class TestPriorWeight:
    def test_starts_at_the_ceiling_before_any_games(self):
        assert prior_weight(0, changed_team=False, ceiling=0.5) == pytest.approx(0.5)

    def test_decays_monotonically_as_games_accumulate(self):
        weights = [
            prior_weight(n, changed_team=False, ceiling=0.5)
            for n in (0, 1, 2, 4, 8, 12)
        ]
        assert weights == sorted(weights, reverse=True)
        assert weights[-1] < weights[0]

    def test_half_the_ceiling_after_one_games_equivalent(self):
        weight = prior_weight(
            int(PRIOR_GAMES_EQUIVALENT), changed_team=False, ceiling=0.5
        )
        assert weight == pytest.approx(0.25)

    def test_changing_school_halves_it(self):
        """Transfer portal: prior production says less about the new role."""
        stayed = prior_weight(3, changed_team=False, ceiling=0.5)
        moved = prior_weight(3, changed_team=True, ceiling=0.5)
        assert moved == pytest.approx(stayed * CHANGED_TEAM_PRIOR_MULTIPLIER)

    def test_never_exceeds_the_ceiling(self):
        for n in range(0, 20):
            for changed in (True, False):
                assert prior_weight(n, changed_team=changed, ceiling=0.5) <= 0.5

    def test_zero_ceiling_disables_priors_entirely(self):
        assert prior_weight(0, changed_team=False, ceiling=0.0) == 0.0


# =============================================================================
# Against the real database
# =============================================================================
# Marked per class, not per module. `pytestmark` is the only name pytest looks
# for, and this file used to assign `pytestmark_db` — which pytest ignores
# entirely, so `-m "not integration"` still collected every database test below
# and quietly went to Supabase for them. Renaming it to `pytestmark` would swing
# the other way and mark the pure tests above as integration too, so each class
# that takes a database fixture carries the marker itself.


@pytest.mark.integration
class TestNoLookaheadInRealQueries:
    """Re-derive the bound from the source tables, not from features.py's SQL."""

    def test_player_usage_never_touches_the_prediction_week(
        self, conn, populated_week
    ):
        from worker.core.features import player_usage

        rows = player_usage(populated_week)
        assert rows, "expected some usage rows for a populated week"

        # Independent check: the module's own guard column, plus a direct
        # interrogation of the source table using a different formulation.
        assert max(r["max_source_week"] for r in rows) < populated_week.week

        latest = conn.execute(
            """
            select max(week) as w
              from player_game_stats
             where season = %(season)s and week < %(week)s
            """,
            {"season": populated_week.season, "week": populated_week.week},
        ).fetchone()["w"]
        assert latest is not None
        assert latest == populated_week.week - 1, (
            "the week immediately before the cutoff must be present — otherwise "
            "this test would pass trivially on an empty range"
        )

    def test_usage_counts_match_an_independent_count(self, conn, populated_week):
        """games_played must equal a straight count over the same window."""
        from worker.core.features import player_usage

        rows = {r["player_id"]: r for r in player_usage(populated_week)}
        sample = conn.execute(
            """
            select player_id, count(*) as n
              from player_game_stats
             where season = %(season)s
               and week   < %(week)s
               and position_group = any(%(positions)s::position_group[])
             group by player_id
             order by n desc
             limit 25
            """,
            {
                "season": populated_week.season,
                "week": populated_week.week,
                "positions": list(SKILL_POSITIONS),
            },
        ).fetchall()
        assert sample
        for row in sample:
            assert rows[row["player_id"]]["games_played"] == row["n"]

    def test_team_context_never_touches_the_prediction_week(self, populated_week):
        from worker.core.features import team_context

        rows = team_context(populated_week)
        assert rows
        assert max(r["max_source_week"] for r in rows) < populated_week.week

    def test_opponent_defense_reads_only_this_cutoff(self, populated_week):
        from worker.core.features import opponent_defense

        rows = opponent_defense(populated_week)
        if not rows:
            pytest.skip("no defense_position_ratings at this cutoff")
        assert {int(r["as_of_week"]) for r in rows} == {populated_week.week}

    def test_team_elo_reads_only_point_in_time_rows(self, conn, populated_week):
        from worker.core.features import team_elo

        rows = team_elo(populated_week)
        if not rows:
            pytest.skip("no elo snapshots at this cutoff")
        assert {int(r["as_of_week"]) for r in rows} == {populated_week.week}

        # Season-final ratings must be structurally unreachable: they carry a
        # NULL as_of_week, so no as-of join can return them.
        leaked = conn.execute(
            """
            select count(*) as n
              from team_rating_snapshots
             where snapshot_kind = 'season_final'
               and as_of_week is not null
            """
        ).fetchone()["n"]
        assert leaked == 0

    def test_sp_plus_is_not_reachable_as_a_feature(self, conn):
        """SP+/SRS/FPI are season-scoped and must never join on as_of_week."""
        row = conn.execute(
            """
            select count(*) as n
              from team_rating_snapshots
             where source in ('sp_plus', 'srs', 'fpi')
               and snapshot_kind = 'point_in_time'
            """
        ).fetchone()
        assert row["n"] == 0, (
            "SP+/SRS/FPI are only served season-scoped by CFBD; storing one as "
            "point_in_time would make an end-of-season rating readable as if "
            "known in week 3"
        )

    def test_upcoming_slate_does_not_expose_results(self, populated_week):
        """Knowing the fixture list is fine. Knowing the score is not."""
        from worker.core.features import upcoming_slate

        rows = upcoming_slate(populated_week)
        if not rows:
            pytest.skip("no games scheduled at this cutoff")
        forbidden = {"home_points", "away_points", "completed"}
        assert not (forbidden & set(rows[0].keys()))

    def test_a_deliberately_wrong_cutoff_is_caught(self, conn, populated_week):
        """The guard must actually fire — prove it rather than assume it.

        Runs the same aggregate the module runs but with `<=` instead of `<`,
        which is the single most likely way this breaks, and checks that
        features.py's guard rejects the result.
        """
        rows = conn.execute(
            """
            select player_id, max(week) as max_source_week
              from player_game_stats
             where season = %(season)s and week <= %(week)s
             group by player_id
             having max(week) = %(week)s
             limit 5
            """,
            {"season": populated_week.season, "week": populated_week.week},
        ).fetchall()
        if not rows:
            pytest.skip("no rows in the prediction week to leak")
        with pytest.raises(LookaheadError):
            _assert_no_lookahead(list(rows), populated_week, "off_by_one")


@pytest.mark.integration
class TestFeatureFrame:
    def test_frame_builds_and_carries_the_cutoff(self, populated_week):
        from worker.core.features import build_feature_frame

        frame = build_feature_frame(populated_week)
        if frame.is_empty():
            pytest.skip("no feature rows at this cutoff")

        assert frame["as_of_week"].unique().to_list() == [populated_week.week]
        assert frame["week"].unique().to_list() == [populated_week.week]

    def test_every_row_has_a_distinct_opponent(self, populated_week):
        from worker.core.features import build_feature_frame

        frame = build_feature_frame(populated_week)
        if frame.is_empty():
            pytest.skip("no feature rows at this cutoff")
        assert (frame["team_id"] != frame["opponent_team_id"]).all()

    def test_prior_weight_is_within_its_ceiling(self, populated_week):
        from worker.core.features import build_feature_frame

        ceiling = 0.5
        frame = build_feature_frame(
            populated_week, prior_season_weight_max=ceiling
        )
        if frame.is_empty():
            pytest.skip("no feature rows at this cutoff")
        assert frame["prior_weight"].min() >= 0.0
        assert frame["prior_weight"].max() <= ceiling

    def test_effective_sample_is_at_least_games_played(self, populated_week):
        from worker.core.features import build_feature_frame

        frame = build_feature_frame(populated_week)
        if frame.is_empty():
            pytest.skip("no feature rows at this cutoff")
        assert (frame["effective_sample"] >= frame["games_played"]).all()

    def test_schema_does_not_depend_on_how_much_history_exists(self, conn):
        """A frame's columns must depend on the cutoff, never on ingest scope.

        Regression: 2024 (no 2023 ingested) produced 90 columns while 2025
        produced 134, because the prior-season join contributed nothing in the
        first case. A backtest walking from 2024 into 2025 would have silently
        changed feature set mid-run.
        """
        from worker.core.features import build_feature_frame, prior_column_names

        # FULL-INGEST seasons only. A prior-season backfill loads box scores and
        # nothing else (`ingest_stats --box-scores-only`), so it has no defensive
        # ratings, no Elo and no weather — and correspondingly no columns for
        # them. That is not schema drift, it is a season we never project: it
        # exists to supply prior-year features to the season after it. The
        # guarantee that matters is across the seasons the backtest walks.
        seasons = [
            int(r["season"])
            for r in conn.execute(
                "select distinct season from plays order by season"
            ).fetchall()
        ]
        if len(seasons) < 2:
            pytest.skip("need two full-ingest seasons to compare schemas")

        frames = {}
        for season in seasons:
            frame = build_feature_frame(AsOf(season=season, week=8))
            if not frame.is_empty():
                frames[season] = frame
        if len(frames) < 2:
            pytest.skip("not enough non-empty frames")

        column_sets = {s: set(f.columns) for s, f in frames.items()}
        first, *rest = column_sets.values()
        for other in rest:
            assert first == other, (
                f"schema drift between seasons: {first ^ other}"
            )

        # The earliest season has no prior season at all, yet must still carry
        # the full prior-season schema.
        earliest = frames[min(frames)]
        for column in prior_column_names():
            assert column in earliest.columns

    def test_prior_season_features_actually_populate(self, conn, populated_week):
        """A season with a prior season loaded must exercise the prior blend.

        The whole point of the 2023 box-score backfill: before it, the 2024 half
        of the backtest ran with prior_weight = 0 on every row, so CLAUDE.md §6's
        college weighting rules — down-weight prior production, halve it again
        for transfer-portal moves — were never exercised on that season at all.
        """
        from worker.core.features import build_feature_frame

        has_prior = conn.execute(
            "select 1 from player_game_stats where season = %s limit 1",
            (populated_week.prior_season,),
        ).fetchone()
        if not has_prior:
            pytest.skip("no prior season ingested for this cutoff")

        frame = build_feature_frame(populated_week)
        if frame.is_empty():
            pytest.skip("no feature rows at this cutoff")

        assert frame["prior_games_played"].max() > 0, (
            "prior season is ingested but no row picked up any prior games"
        )
        assert frame["prior_weight"].max() > 0
        assert frame["changed_team"].sum() > 0, (
            "transfer-portal detection found nobody, which is implausible in "
            "college football"
        )

    def test_no_column_name_collisions_from_joins(self, populated_week):
        """Polars silently suffixes collisions with _right.

        Regression: team_context returned `games_played`, colliding with the
        per-player one and producing a `games_played_right` column that no
        model would ever have thought to read.
        """
        from worker.core.features import build_feature_frame

        frame = build_feature_frame(populated_week)
        if frame.is_empty():
            pytest.skip("no feature rows at this cutoff")
        suffixed = [c for c in frame.columns if c.endswith("_right")]
        assert not suffixed, f"join collision produced {suffixed}"

    def test_weather_can_be_excluded(self, populated_week):
        """The switch exists so the report can MEASURE the observed-vs-forecast
        bias rather than assert it is negligible."""
        from worker.core.features import build_feature_frame

        with_weather = build_feature_frame(populated_week, include_weather=True)
        without = build_feature_frame(populated_week, include_weather=False)
        if with_weather.is_empty():
            pytest.skip("no feature rows at this cutoff")
        assert with_weather.height == without.height
        assert "temperature_f" not in without.columns


@pytest.mark.integration
class TestOpeningWeekFrame:
    """Week 1 must produce a frame. Before Phase 6b.2 it produced nothing.

    `player_usage` can only see players who have already appeared, so entering
    week 1 it returns an empty list, and `build_feature_frame` used to take that
    as "nothing to do" and return an empty frame. `run_projections` then exited
    0 with an empty board — the defect this whole phase exists to close, and one
    that no alert fired on because the pipeline genuinely succeeded.
    """

    @pytest.fixture
    def opening_week(self, conn):
        row = conn.execute(
            """
            select g.season
              from games g
             where g.week = 1
               and exists (
                     select 1 from player_game_stats s
                      where s.season = g.season - 1
                   )
               and exists (
                     select 1 from player_team_seasons r where r.season = g.season
                   )
             group by g.season
             order by g.season desc
             limit 1
            """
        ).fetchone()
        if not row:
            pytest.skip("no season with a week 1, a prior season and a roster")
        return AsOf(season=int(row["season"]), week=1)

    def test_a_week_one_frame_is_not_empty(self, opening_week):
        frame = build_feature_frame(opening_week)
        assert not frame.is_empty()
        assert frame.height > 100

    def test_every_week_one_row_has_no_current_season_games(self, opening_week):
        """There is no week before week 1, so nothing may claim otherwise."""
        frame = build_feature_frame(opening_week)
        assert frame["games_played"].max() == 0

    def test_every_week_one_row_carries_prior_season_evidence(self, opening_week):
        """A roster place alone is not a reason to appear on a board."""
        frame = build_feature_frame(opening_week)
        assert frame["prior_games_played"].min() > 0

    def test_the_universe_is_far_narrower_than_the_roster(self, opening_week):
        """2025 has 15,601 roster rows against 2,963 players who ever played."""
        frame = build_feature_frame(opening_week)
        roster = len(roster_universe(opening_week))
        assert frame["player_id"].n_unique() < roster * 0.75

    def test_the_scoring_columns_exist_even_with_no_plays_behind_them(
        self, opening_week
    ):
        """A frame's schema may depend on the cutoff and never on how much
        history happens to be ingested, or a walk changes feature set as it
        crosses a season boundary."""
        frame = build_feature_frame(opening_week)
        for column in prior_goal_line_column_names():
            assert column in frame.columns
        assert frame["goal_line_opportunities"].max() == 0


@pytest.mark.integration
class TestPriorScoringRecord:
    """The prior season's goal-line record, which is all week 1 has.

    `player_goal_line_usage` reads the current season, so entering week 1 every
    opportunity count is zero and `project_anytime_td` returned None for the
    whole board — losing the market Phase 6a measured as holding up best across
    the cold start.
    """

    def test_the_weeks_that_read_it_are_the_weeks_that_can_use_it(self):
        """`features` cannot import the universe rule without a cycle, so the
        two constants are held in step here instead. Setting the feature-side
        one lower would strip anytime TD off the opening board, and nothing else
        would complain."""
        from worker.core.projections import LAST_OPENING_WEEK

        assert PRIOR_SCORING_WEEKS == LAST_OPENING_WEEK

    @pytest.fixture
    def season_with_prior_plays(self, conn):
        row = conn.execute(
            """
            select g.season
              from games g
             where exists (select 1 from plays p where p.season = g.season - 1)
             group by g.season
             order by g.season desc
             limit 1
            """
        ).fetchone()
        if not row:
            pytest.skip("no season whose prior season has play-by-play")
        return int(row["season"])

    def test_it_reads_the_whole_prior_season(self, season_with_prior_plays):
        """The completed season, not a week-bounded slice of it — which is what
        distinguishes this from every other feature query in this module."""
        season = season_with_prior_plays
        prior = prior_goal_line_usage(AsOf(season=season, week=1))
        whole = player_goal_line_usage(AsOf(season=season - 1, week=99))
        assert prior
        assert {r["player_id"] for r in prior} == {r["player_id"] for r in whole}
        assert sum(r["prior_goal_line_opportunities"] for r in prior) == sum(
            r["goal_line_opportunities"] for r in whole
        )

    def test_it_does_not_move_with_the_cutoff_week(self, season_with_prior_plays):
        """A completed season is knowable in full before this one kicks off, so
        the week we are predicting cannot change it."""
        season = season_with_prior_plays
        early = prior_goal_line_usage(AsOf(season=season, week=1))
        late = prior_goal_line_usage(AsOf(season=season, week=10))
        assert early == late

    def test_every_column_is_namespaced(self, season_with_prior_plays):
        """Or it would collide with the current-season column of the same name
        on the join, and the model could not tell the two apart."""
        rows = prior_goal_line_usage(AsOf(season=season_with_prior_plays, week=1))
        assert rows
        assert set(rows[0]) == {"player_id", *prior_goal_line_column_names()}
