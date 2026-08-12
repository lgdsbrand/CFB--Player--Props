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
    solve_shin_z,
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
        # THE OUTER TRANSACTION IS OPENED EXPLICITLY, and that is load-bearing.
        #
        # `conn.transaction()` inside a test is only a SAVEPOINT when a
        # transaction is already open; with none open it begins a real one and
        # COMMITS it on the way out. Every test here used to reach `base` first,
        # whose inserts opened the transaction implicitly, so the distinction
        # never showed. A test that touches the database without `base` — the
        # sport tests at the bottom of this file are the first — committed its
        # rows into whichever database SUPABASE_DB_URL pointed at, passed in
        # isolation, and then failed on the NEXT run against its own leftovers.
        #
        # `force_rollback` makes the file's promise ("no row survives") true for
        # any test, not just the ones that happen to use the fixture below it.
        with connection.transaction(force_rollback=True):
            yield connection


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
    """SQL and Python must agree exactly.

    The worker computes picks in Python; the read layer de-vigs live lines in
    SQL. Two implementations of one definition is a deliberate trade (see the
    note atop worker/core/probability.py), and these tests are the thing that
    makes it safe. Note the implementations are deliberately DIFFERENT
    algorithms for Shin — bisection in Python, closed form in SQL — so this is
    a real cross-check rather than a transcription check.
    """

    # Spans heavy favourites to long longshots. The methods agree near -110 and
    # diverge at the extremes, so a narrow set of cases would prove nothing.
    PRICE_CASES = [
        (-110, -110), (-130, 110), (-200, 165), (120, -140),
        (-150, 130), (150, -190), (600, -1100), (-1100, 600),
        (300, -400), (-5000, 2000), (100, -100),
    ]

    @pytest.mark.parametrize("method", ["proportional", "additive", "shin"])
    def test_sql_devig_matches_python_for_every_method(self, conn, method):
        for over, under in self.PRICE_CASES:
            row = conn.execute(
                "select devig_two_way(%s, %s, %s) as fair", (over, under, method)
            ).fetchone()
            expected = devig_two_way(over, under, method)
            if expected is None:
                assert row["fair"] is None, f"{method} at {over}/{under}"
            else:
                assert row["fair"] is not None, f"{method} at {over}/{under}"
                assert float(row["fair"]) == pytest.approx(expected, abs=1e-9), (
                    f"drift at {over}/{under} under {method}"
                )

    def test_sql_shin_equals_sql_additive(self, conn):
        """The two-way identity must hold in SQL too, not just in Python."""
        for over, under in self.PRICE_CASES:
            row = conn.execute(
                """
                select devig_two_way_shin(%(o)s, %(u)s)     as shin,
                       devig_two_way_additive(%(o)s, %(u)s) as additive
                """,
                {"o": over, "u": under},
            ).fetchone()
            assert (row["shin"] is None) == (row["additive"] is None)
            if row["shin"] is not None:
                assert float(row["shin"]) == pytest.approx(
                    float(row["additive"]), abs=1e-12
                )

    def test_sql_shin_z_matches_python(self, conn):
        for over, under in self.PRICE_CASES:
            raw = (
                american_to_implied_probability(over),
                american_to_implied_probability(under),
            )
            if sum(raw) <= 1.0 + 1e-9:
                continue
            row = conn.execute(
                "select devig_shin_z(%s, %s) as z", (over, under)
            ).fetchone()
            assert float(row["z"]) == pytest.approx(solve_shin_z(raw), abs=1e-8), (
                f"z drift at {over}/{under}"
            )

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

    def test_incoherent_market_is_null(self, conn):
        """Implied total below 1 is a data error, not a priceable market."""
        row = conn.execute("select devig_two_way(600, 5000) as fair").fetchone()
        assert row["fair"] is None

    def test_unknown_method_raises_rather_than_returning_null(self, conn):
        """A config typo must be loud. Silent NULL would just erase every edge."""
        with pytest.raises(pg_errors.RaiseException):
            with conn.transaction():
                conn.execute("select devig_two_way(-110, -110, 'wishful')")

    def test_two_arg_form_follows_app_config(self, conn):
        """The configured default drives the view, so it must actually be read."""
        configured = conn.execute(
            "select value #>> '{}' as method from app_config where key = 'devig_method'"
        ).fetchone()["method"]
        assert configured in ("proportional", "additive", "shin")

        row = conn.execute(
            """
            select devig_two_way(600, -1100)             as implicit,
                   devig_two_way(600, -1100, %s)         as explicit
            """,
            (configured,),
        ).fetchone()
        assert float(row["implicit"]) == pytest.approx(float(row["explicit"]))

    def test_configured_default_is_shin(self, conn):
        """Chosen 2026-07-31; migration 0013 carries the reasoning."""
        row = conn.execute(
            "select value #>> '{}' as method from app_config where key = 'devig_method'"
        ).fetchone()
        assert row["method"] == "shin"


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
# CLAUDE.md §3 — the sport dimension (migration 0035)
# =============================================================================
# The client chose one app with a toggle, so NFL rows will share these tables.
# What is tested here is that the schema can actually HOLD a second sport, which
# is the claim the migration makes and the only one worth proving early: by the
# time NFL data exists, a schema that cannot take it is a data migration on a
# live season rather than a DDL change.
class TestSportDimension:
    def test_an_nfl_team_needs_no_cfbd_id(self, conn):
        """The reason `cfbd_id` stopped being NOT NULL.

        CFBD has never heard of the Bengals. Before 0035 an NFL team could only
        be inserted by inventing a CFBD id for it, which would then collide with
        a real college team's the moment CFBD issued that id.
        """
        with conn.transaction():
            row = conn.execute(
                "insert into teams (school, sport) values ('Test NFL', 'nfl') "
                "returning sport, cfbd_id"
            ).fetchone()
        assert row["sport"] == "nfl"
        assert row["cfbd_id"] is None

    def test_a_cfb_team_still_cannot_omit_its_cfbd_id(self, conn):
        """And this is what makes `default 'cfb'` safe rather than a trap.

        An NFL row that forgets to set `sport` defaults to the college one, has
        no CFBD id, and is rejected here — instead of quietly appearing on the
        college board. The default is a convenience; this constraint is what
        stops it becoming a silent mislabelling.
        """
        with pytest.raises(pg_errors.CheckViolation) as excinfo:
            with conn.transaction():
                conn.execute(
                    "insert into teams (school) values ('Test Unlabelled')"
                )
        assert excinfo.value.diag.constraint_name == "teams_cfb_requires_cfbd_id"

    def test_a_cfb_game_still_cannot_omit_its_cfbd_id(self, conn, base):
        with pytest.raises(pg_errors.CheckViolation) as excinfo:
            with conn.transaction():
                conn.execute(
                    "insert into games (season, week, home_team_id, away_team_id) "
                    "values (2026, 1, %s, %s)",
                    (base["offense"], base["defense"]),
                )
        assert excinfo.value.diag.constraint_name == "games_cfb_requires_cfbd_id"

    def test_two_sports_may_share_a_conference_name(self, conn):
        with conn.transaction():
            conn.execute(
                "insert into conferences (name, sport) values ('Test Conf', 'cfb')"
            )
            conn.execute(
                "insert into conferences (name, sport) values ('Test Conf', 'nfl')"
            )
            n = conn.execute(
                "select count(*) as n from conferences where name = 'Test Conf'"
            ).fetchone()["n"]
        assert n == 2

    def test_one_sport_may_not(self, conn):
        with pytest.raises(pg_errors.UniqueViolation) as excinfo:
            with conn.transaction():
                conn.execute(
                    "insert into conferences (name, sport) values ('Test Conf', 'cfb')"
                )
                conn.execute(
                    "insert into conferences (name, sport) values ('Test Conf', 'cfb')"
                )
        assert excinfo.value.diag.constraint_name == "conferences_sport_name_key"

    def test_the_conference_upsert_target_resolves(self, conn):
        """A REGRESSION TEST FOR A BREAK 0035 INTRODUCED, not for the schema.

        `ON CONFLICT (...)` names an INDEX, not a column list. Migration 0035
        replaced the global unique on `conferences.name`, so the reference
        adapter's `on conflict (name)` stopped matching anything and would have
        raised on the next Sunday run — taking the whole weekly chain down with
        it, since conferences are its first step. Nothing in the type system or
        the unit tests could see that; it lives in the gap between a migration
        and a string in an unrelated file.

        The statement below is the one `upsert()` builds for the adapter's
        declared conflict columns. If the constraint is ever reshaped again, this
        fails in a second rather than at 09:00 UTC on a Sunday.
        """
        with conn.transaction():
            conn.execute(
                "insert into conferences (name, sport) values ('Test Conf', 'cfb') "
                "on conflict (sport, name) do update set abbreviation = excluded.abbreviation"
            )

    def test_the_board_carries_the_sport_it_belongs_to(self, conn, base):
        projection = make_projection(conn, base)
        row = conn.execute(
            "select sport from v_board_rows where projection_id = %s",
            (projection,),
        ).fetchone()
        assert row["sport"] == "cfb"


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
