"""Integration tests: prove the schema enforces what it claims to enforce.

These require a real database and are SKIPPED when SUPABASE_DB_URL is unset, so
`pytest` still passes on a machine with no Supabase project.

    pytest tests/test_schema_constraints.py -v

Everything runs inside one transaction that is rolled back at teardown, so no
row survives. Failing statements are wrapped in savepoints so an expected error
does not poison the outer transaction.

Why this file exists: applying a migration only proves the DDL parses and runs.
It does not prove that the anti-lookahead constraints actually reject bad data,
that the generated columns compute what the brief requires, or that a player
with no book line still reaches the board. Those are the load-bearing claims in
CLAUDE.md §1, §4, §6 and §7 — so they get tested against a live engine.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg import errors as pg_errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Json  # noqa: E402

from worker.config import ConfigError, get_settings  # noqa: E402
from worker.core.probability import (  # noqa: E402
    american_to_implied_probability,
    devig_two_way,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    # Goes through the worker config loader rather than os.environ directly, so
    # a SUPABASE_DB_URL in the repo-root .env is picked up the same way the jobs
    # pick it up.
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
def base(conn):
    """Minimal referential fixture. Negative source ids mark synthetic rows."""
    cur = conn.cursor()

    cur.execute(
        "insert into teams (cfbd_id, school, abbreviation) "
        "values (-1, 'Test Offense', 'TOF') returning id"
    )
    offense = cur.fetchone()["id"]

    cur.execute(
        "insert into teams (cfbd_id, school, abbreviation) "
        "values (-2, 'Test Defense', 'TDF') returning id"
    )
    defense = cur.fetchone()["id"]

    cur.execute(
        "insert into games (cfbd_id, season, week, home_team_id, away_team_id) "
        "values (-1, 2025, 5, %s, %s) returning id",
        (offense, defense),
    )
    game = cur.fetchone()["id"]

    cur.execute(
        "insert into games (cfbd_id, season, week, home_team_id, away_team_id) "
        "values (-2, 2025, 3, %s, %s) returning id",
        (offense, defense),
    )
    earlier_game = cur.fetchone()["id"]

    cur.execute(
        "insert into players (name, position_group) values ('Test Back', 'RB') returning id"
    )
    player = cur.fetchone()["id"]

    cur.execute(
        "insert into player_team_seasons (player_id, team_id, season, position_group) "
        "values (%s, %s, 2025, 'RB')",
        (player, offense),
    )

    cur.execute(
        "insert into model_runs (run_type, model_version, season, week, as_of_week) "
        "values ('weekly', 'test-0', 2025, 5, 5) returning id"
    )
    model_run = cur.fetchone()["id"]

    return {
        "offense": offense,
        "defense": defense,
        "game": game,
        "earlier_game": earlier_game,
        "player": player,
        "model_run": model_run,
    }


def make_projection(conn, base, market="rush_yards", **overrides):
    params = {
        "model_run_id": base["model_run"],
        "player_id": base["player"],
        "game_id": base["game"],
        "team_id": base["offense"],
        "opponent_team_id": base["defense"],
        "market_key": market,
        "season": 2025,
        "week": 5,
        "as_of_week": 5,
        "distribution": "normal",
        "params": Json({"mu": 82.0, "sigma": 28.0}),
        "p10": 48,
        "p50": 82,
        "p90": 118,
    }
    params.update(overrides)
    cur = conn.cursor()
    cur.execute(
        """
        insert into projections
          (model_run_id, player_id, game_id, team_id, opponent_team_id, market_key,
           season, week, as_of_week, distribution, params, p10, p50, p90)
        values
          (%(model_run_id)s, %(player_id)s, %(game_id)s, %(team_id)s,
           %(opponent_team_id)s, %(market_key)s, %(season)s, %(week)s,
           %(as_of_week)s, %(distribution)s, %(params)s, %(p10)s, %(p50)s, %(p90)s)
        returning id
        """,
        params,
    )
    return cur.fetchone()["id"]


def make_pick(conn, base, projection_id, **overrides):
    params = {
        "projection_id": projection_id,
        "player_id": base["player"],
        "game_id": base["game"],
        "team_id": base["offense"],
        "opponent_team_id": base["defense"],
        "market_key": "rush_yards",
        "season": 2025,
        "week": 5,
        "line": 74.5,
        "side": "over",
        "model_prob_over": 0.62,
        "book_prob_over": 0.55,
    }
    params.update(overrides)
    cur = conn.cursor()
    cur.execute(
        """
        insert into picks
          (projection_id, player_id, game_id, team_id, opponent_team_id, market_key,
           season, week, line, side, model_prob_over, book_prob_over)
        values
          (%(projection_id)s, %(player_id)s, %(game_id)s, %(team_id)s,
           %(opponent_team_id)s, %(market_key)s, %(season)s, %(week)s,
           %(line)s, %(side)s, %(model_prob_over)s, %(book_prob_over)s)
        returning id, side, confidence, edge, has_book_line
        """,
        params,
    )
    return cur.fetchone()


# =============================================================================
# CLAUDE.md §4 — no lookahead
# =============================================================================
class TestLookaheadGuards:
    def test_season_final_rating_cannot_carry_a_week(self, conn, base):
        """A season-final rating has no week at which it was known."""
        with pytest.raises(pg_errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    "insert into team_rating_snapshots "
                    "(team_id, season, source, snapshot_kind, as_of_week, rating) "
                    "values (%s, 2025, 'sp_plus', 'season_final', 12, 18.4)",
                    (base["defense"],),
                )

    def test_point_in_time_rating_requires_a_week(self, conn, base):
        with pytest.raises(pg_errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    "insert into team_rating_snapshots "
                    "(team_id, season, source, snapshot_kind, as_of_week, rating) "
                    "values (%s, 2025, 'sp_plus', 'point_in_time', null, 18.4)",
                    (base["defense"],),
                )

    def test_valid_rows_of_both_kinds_are_accepted(self, conn, base):
        conn.execute(
            "insert into team_rating_snapshots "
            "(team_id, season, source, snapshot_kind, as_of_week, rating) "
            "values (%s, 2025, 'sp_plus', 'point_in_time', 5, 18.4)",
            (base["defense"],),
        )
        conn.execute(
            "insert into team_rating_snapshots "
            "(team_id, season, source, snapshot_kind, rating) "
            "values (%s, 2025, 'sp_plus', 'season_final', 21.9)",
            (base["defense"],),
        )

    def test_feature_query_on_as_of_week_cannot_see_season_final(self, conn, base):
        """The whole point of the constraint, demonstrated end to end."""
        conn.execute(
            "insert into team_rating_snapshots "
            "(team_id, season, source, snapshot_kind, as_of_week, rating) "
            "values (%s, 2025, 'sp_plus', 'point_in_time', 5, 18.4)",
            (base["defense"],),
        )
        conn.execute(
            "insert into team_rating_snapshots "
            "(team_id, season, source, snapshot_kind, rating) "
            "values (%s, 2025, 'sp_plus', 'season_final', 99.9)",
            (base["defense"],),
        )
        rows = conn.execute(
            "select rating from team_rating_snapshots "
            "where team_id = %s and season = 2025 and as_of_week = 5",
            (base["defense"],),
        ).fetchall()
        ratings = [float(r["rating"]) for r in rows]
        assert ratings == [18.4], "season-final rating leaked into an as_of_week query"

    def test_backtest_cannot_predict_with_future_knowledge(self, conn, base):
        cur = conn.cursor()
        cur.execute(
            "insert into backtests (model_run_id, name, seasons, hit_rate_basis) "
            "values (%s, 'test', '{2025}', 'threshold') returning id",
            (base["model_run"],),
        )
        backtest_id = cur.fetchone()["id"]

        with pytest.raises(pg_errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    """
                    insert into backtest_predictions
                      (backtest_id, player_id, game_id, market_key, position_group,
                       season, week, as_of_week, line, side, model_prob_over, confidence)
                    values (%s, %s, %s, 'rush_yards', 'RB', 2025, 5, 6, 74.5,
                            'over', 0.62, 0.62)
                    """,
                    (backtest_id, base["player"], base["game"]),
                )

    def test_splits_function_excludes_the_prediction_week(self, conn, base):
        """defense_position_splits_through uses a strict `week <` cutoff."""
        for game_key, week, yards in (("earlier_game", 3, 120), ("game", 5, 999)):
            conn.execute(
                """
                insert into defense_position_game_splits
                  (game_id, defense_team_id, offense_team_id, season, week,
                   position_group, rush_yards_allowed)
                values (%s, %s, %s, 2025, %s, 'RB', %s)
                """,
                (base[game_key], base["defense"], base["offense"], week, yards),
            )

        rows = conn.execute(
            "select rush_yards_allowed, games_included "
            "from defense_position_splits_through(2025, 5) "
            "where defense_team_id = %s and position_group = 'RB'",
            (base["defense"],),
        ).fetchall()

        assert len(rows) == 1
        assert rows[0]["games_included"] == 1
        assert rows[0]["rush_yards_allowed"] == 120, "week 5 data leaked into a week 5 cutoff"


# =============================================================================
# CLAUDE.md §1 / §6 — the surface derives from the distribution
# =============================================================================
class TestPickDerivation:
    def test_confidence_and_edge_are_generated_on_the_over(self, conn, base):
        projection = make_projection(conn, base)
        pick = make_pick(conn, base, projection)
        assert float(pick["confidence"]) == pytest.approx(0.62)
        assert float(pick["edge"]) == pytest.approx(0.07)
        assert pick["has_book_line"] is False  # no line_id attached

    def test_confidence_and_edge_are_generated_on_the_under(self, conn, base):
        projection = make_projection(conn, base)
        pick = make_pick(
            conn, base, projection, side="under", model_prob_over=0.38, book_prob_over=0.45
        )
        # Under mass is 0.62; book under is 0.55.
        assert float(pick["confidence"]) == pytest.approx(0.62)
        assert float(pick["edge"]) == pytest.approx(0.07)

    def test_side_must_agree_with_the_probability(self, conn, base):
        """The call is the side the majority of the distribution falls on."""
        projection = make_projection(conn, base)
        with pytest.raises(pg_errors.CheckViolation):
            with conn.transaction():
                make_pick(conn, base, projection, side="over", model_prob_over=0.38)

    def test_edge_is_null_without_a_book_probability(self, conn, base):
        """No two-way price means "no edge computable", not zero edge."""
        projection = make_projection(conn, base)
        pick = make_pick(conn, base, projection, book_prob_over=None)
        assert pick["edge"] is None
        assert float(pick["confidence"]) == pytest.approx(0.62)

    def test_probabilities_outside_zero_one_rejected(self, conn, base):
        projection = make_projection(conn, base)
        with pytest.raises(pg_errors.CheckViolation):
            with conn.transaction():
                make_pick(conn, base, projection, model_prob_over=1.4)


class TestOddsMath:
    def test_sql_devig_matches_python(self, conn):
        """The SQL and Python implementations must not drift apart."""
        cases = [(-110, -110), (-130, 110), (-200, 165), (120, -140)]
        for over, under in cases:
            row = conn.execute(
                "select devig_two_way(%s, %s) as fair", (over, under)
            ).fetchone()
            assert float(row["fair"]) == pytest.approx(
                devig_two_way(over, under), abs=1e-9
            ), f"drift at {over}/{under}"

    def test_sql_implied_probability_matches_python(self, conn):
        for price in (-250, -110, 100, 145, 400):
            row = conn.execute(
                "select american_to_implied_probability(%s) as p", (price,)
            ).fetchone()
            assert float(row["p"]) == pytest.approx(
                american_to_implied_probability(price), abs=1e-9
            )

    def test_one_sided_price_is_null_not_zero(self, conn):
        row = conn.execute("select devig_two_way(-110, null) as fair").fetchone()
        assert row["fair"] is None


class TestAnytimeTdOutcome:
    def test_offensive_tds_excludes_passing_and_return_tds(self, conn, base):
        cur = conn.cursor()
        cur.execute(
            """
            insert into player_game_stats
              (player_id, game_id, team_id, opponent_team_id, season, week,
               position_group, is_home, pass_tds, rush_tds, rec_tds, return_tds)
            values (%s, %s, %s, %s, 2025, 5, 'RB', true, 3, 1, 0, 1)
            returning offensive_tds
            """,
            (base["player"], base["game"], base["offense"], base["defense"]),
        )
        # 3 passing + 1 return TD must not count; 1 rushing must.
        assert cur.fetchone()["offensive_tds"] == 1

    def test_anytime_td_market_is_a_line_of_half(self, conn):
        row = conn.execute(
            "select stat_column, default_line, is_binary from markets where key = 'anytime_td'"
        ).fetchone()
        assert row["stat_column"] == "offensive_tds"
        assert float(row["default_line"]) == 0.5
        assert row["is_binary"] is True


# =============================================================================
# CLAUDE.md §7 — late lines
# =============================================================================
class TestBoardBehaviour:
    def test_projection_without_a_line_still_reaches_the_board(self, conn, base):
        projection = make_projection(conn, base)
        row = conn.execute(
            "select has_call, has_book_line, projected_median, side, confidence "
            "from v_board_rows where projection_id = %s",
            (projection,),
        ).fetchone()

        assert row is not None, "a player with no posted line vanished from the board"
        assert row["has_call"] is False
        assert row["has_book_line"] is False
        assert float(row["projected_median"]) == pytest.approx(82)
        assert row["side"] is None
        assert row["confidence"] is None

    def test_call_fills_in_once_a_pick_exists(self, conn, base):
        projection = make_projection(conn, base)
        make_pick(conn, base, projection)
        row = conn.execute(
            "select has_call, side, confidence, edge from v_board_rows "
            "where projection_id = %s",
            (projection,),
        ).fetchone()

        assert row["has_call"] is True
        assert row["side"] == "over"
        assert float(row["confidence"]) == pytest.approx(0.62)
        assert float(row["edge"]) == pytest.approx(0.07)

    def test_board_is_one_row_per_projection(self, conn, base):
        projection = make_projection(conn, base)
        make_pick(conn, base, projection)
        count = conn.execute(
            "select count(*) as n from v_board_rows where projection_id = %s",
            (projection,),
        ).fetchone()["n"]
        assert count == 1


# =============================================================================
# Security posture
# =============================================================================
class TestRowLevelSecurity:
    @staticmethod
    def _anon_exists(conn) -> bool:
        return (
            conn.execute(
                "select 1 as ok from pg_roles where rolname = 'anon'"
            ).fetchone()
            is not None
        )

    def test_anon_can_read_public_reference_data(self, conn):
        if not self._anon_exists(conn):
            pytest.skip("no 'anon' role — not a Supabase database")
        with conn.transaction():
            conn.execute("set local role anon")
            n = conn.execute("select count(*) as n from markets").fetchone()["n"]
            assert n > 0

    def test_anon_cannot_read_play_level_data(self, conn):
        if not self._anon_exists(conn):
            pytest.skip("no 'anon' role — not a Supabase database")
        with pytest.raises(pg_errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("set local role anon")
                conn.execute("select count(*) from play_player_stats")

    def test_anon_cannot_write(self, conn):
        """There are no write policies anywhere in the schema."""
        if not self._anon_exists(conn):
            pytest.skip("no 'anon' role — not a Supabase database")
        with pytest.raises(
            (pg_errors.InsufficientPrivilege, pg_errors.RaiseException)
        ):
            with conn.transaction():
                conn.execute("set local role anon")
                conn.execute(
                    "insert into teams (cfbd_id, school) values (-99, 'Hacked')"
                )

    def test_every_table_has_rls_enabled(self, conn):
        rows = conn.execute(
            """
            select c.relname
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public'
               and c.relkind = 'r'
               and not c.relrowsecurity
             order by c.relname
            """
        ).fetchall()
        assert rows == [], f"tables without RLS: {[r['relname'] for r in rows]}"
