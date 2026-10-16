"""Integration tests: prop picks locked at kickoff and graded (migration 0096).

Skipped when SUPABASE_DB_URL is unset, like `test_schema_constraints.py`. Every
test runs inside one transaction that is rolled back, so no row survives; the
job's own writes happen in savepoints inside it.

What is proved here is what the props record rests on: the lock copies the
board's headline book and nothing the board would not grade, a later run only
touches what changed, nothing can be written once the game has kicked off, and
the results view grades each row against the box score.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Json  # noqa: E402

from worker.config import ConfigError, get_settings  # noqa: E402
from worker.jobs.lock_prop_picks import LockReport, lock_game  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    try:
        url = get_settings().database_url
    except ConfigError:
        pytest.skip("SUPABASE_DB_URL not set — integration tests need a database")
    with psycopg.connect(url, row_factory=dict_row) as connection:
        # Opened explicitly so `conn.transaction()` inside the job is a
        # savepoint, not a transaction that commits (see test_schema_constraints).
        with connection.transaction(force_rollback=True):
            yield connection


@pytest.fixture
def slate(conn):
    """One game an hour from kickoff, one back, two books, a projection."""
    one = lambda sql, *args: conn.execute(sql, args).fetchone()["id"]  # noqa: E731
    offense = one("insert into teams (cfbd_id, school, abbreviation) "
                  "values (-1, 'Test Offense', 'TOF') returning id")
    defense = one("insert into teams (cfbd_id, school, abbreviation) "
                  "values (-2, 'Test Defense', 'TDF') returning id")
    game = one("insert into games (cfbd_id, season, week, home_team_id, away_team_id, "
               "start_date) values (-1, 2025, 5, %s, %s, now() + interval '1 hour') "
               "returning id", offense, defense)
    player = one("insert into players (name, position_group) values ('Test Back', 'RB') returning id")
    conn.execute("insert into player_team_seasons (player_id, team_id, season, position_group) "
                 "values (%s, %s, 2025, 'RB')", (player, offense))
    model_run = one("insert into model_runs (run_type, model_version, season, week, as_of_week) "
                    "values ('weekly', 'test-0', 2025, 5, 5) returning id")
    first = one("insert into sportsbooks (key, display_name, priority) "
                "values ('test_first', 'First', 1) returning id")
    second = one("insert into sportsbooks (key, display_name, priority) "
                 "values ('test_second', 'Second', 2) returning id")
    return {"conn": conn, "offense": offense, "defense": defense, "game": game,
            "player": player, "model_run": model_run, "first": first, "second": second}


def projection(s, market="rush_yards"):
    return s["conn"].execute(
        """
        insert into projections
          (model_run_id, player_id, game_id, team_id, opponent_team_id, market_key,
           season, week, as_of_week, distribution, params, p10, p50, p90)
        values (%s, %s, %s, %s, %s, %s, 2025, 5, 5, 'normal', %s, 48, 82, 118)
        returning id
        """,
        (s["model_run"], s["player"], s["game"], s["offense"], s["defense"], market,
         Json({"mu": 82.0, "sigma": 28.0})),
    ).fetchone()["id"]


def pick(s, projection_id, book, *, line=74.5, prob=0.62, market="rush_yards", with_line=True):
    conn = s["conn"]
    line_id = None
    if with_line:
        line_id = conn.execute(
            """
            insert into player_prop_lines
              (game_id, player_id, market_key, sportsbook_id, season, week, line,
               over_price, under_price, source_adapter)
            values (%s, %s, %s, %s, 2025, 5, %s, -115, -105, 'test') returning id
            """,
            (s["game"], s["player"], market, book, line),
        ).fetchone()["id"]
    conn.execute(
        """
        insert into picks
          (projection_id, line_id, sportsbook_id, player_id, game_id, team_id,
           opponent_team_id, market_key, season, week, line, side, model_prob_over,
           book_prob_over, over_price, under_price)
        values (%s, %s, %s, %s, %s, %s, %s, %s, 2025, 5, %s, %s, %s, 0.52, -115, -105)
        """,
        (projection_id, line_id, book, s["player"], s["game"], s["offense"], s["defense"],
         market, line, "over" if prob >= 0.5 else "under", prob),
    )


def lock(s) -> LockReport:
    report = LockReport()
    lock_game(s["conn"], s["game"], report)
    return report


def locks(s):
    return s["conn"].execute(
        "select l.*, sb.key as book from prop_pick_locks l "
        "join sportsbooks sb on sb.id = l.sportsbook_id where l.game_id = %s",
        (s["game"],),
    ).fetchall()


def test_the_lock_copies_the_boards_headline_book(slate):
    proj = projection(slate)
    pick(slate, proj, slate["second"], line=76.5)
    pick(slate, proj, slate["first"], line=74.5)

    report = lock(slate)

    assert sum(report.new.values()) == 1
    [row] = locks(slate)
    assert row["book"] == "test_first"  # priority 1, as the board shows
    assert float(row["line"]) == 74.5
    assert row["side"] == "over"
    assert float(row["edge"]) == pytest.approx(0.10)
    assert (row["over_price"], row["under_price"]) == (-115, -105)


def test_nothing_the_board_would_not_grade_is_locked(slate):
    # No book line yet: the board's lean, not a call against a price.
    pick(slate, projection(slate), slate["first"], with_line=False)
    # The synthetic DEV book.
    dev = slate["conn"].execute(
        "insert into sportsbooks (key, display_name, priority) values ('dev', 'DEV', 999) "
        "on conflict (key) do update set display_name = excluded.display_name returning id"
    ).fetchone()["id"]
    pick(slate, projection(slate, "receptions"), dev, market="receptions", line=3.5)
    # Anytime TD: one-way, so no edge to grade.
    pick(slate, projection(slate, "anytime_td"), slate["first"], market="anytime_td",
         line=0.5, prob=0.40)

    report = lock(slate)

    assert locks(slate) == []
    assert report.skipped["no priced call on the board"] == 1


def test_a_second_run_touches_only_what_changed(slate):
    proj = projection(slate)
    pick(slate, proj, slate["first"])
    assert sum(lock(slate).new.values()) == 1

    again = lock(slate)
    assert again.written == 0

    slate["conn"].execute("update picks set line = 79.5 where projection_id = %s", (proj,))
    moved = lock(slate)
    assert sum(moved.refreshed.values()) == 1
    assert float(locks(slate)[0]["line"]) == 79.5


def test_nothing_can_be_written_once_the_game_kicks_off(slate):
    pick(slate, projection(slate), slate["first"])
    lock(slate)
    conn = slate["conn"]
    conn.execute("update games set start_date = now() - interval '1 minute' where id = %s",
                 (slate["game"],))

    for statement in ("update prop_pick_locks set line = 99.5 where game_id = %s",
                      "delete from prop_pick_locks where game_id = %s"):
        with pytest.raises(psycopg.errors.RaiseException, match="frozen"), conn.transaction():
            conn.execute(statement, (slate["game"],))

    # The job skips the game instead of failing the run.
    conn.execute("update picks set line = 99.5 where game_id = %s", (slate["game"],))
    report = lock(slate)
    assert report.skipped["kicked off during the run"] == 1
    assert float(locks(slate)[0]["line"]) == 74.5


def test_made_at_is_the_server_clock(slate):
    pick(slate, projection(slate), slate["first"])
    lock(slate)
    row = slate["conn"].execute(
        "select made_at = now() as stamped from prop_pick_locks where game_id = %s",
        (slate["game"],),
    ).fetchone()
    assert row["stamped"]


def _finish(s, rush_yards):
    conn = s["conn"]
    conn.execute("update games set start_date = now() - interval '4 hours', completed = true "
                 "where id = %s", (s["game"],))
    conn.execute(
        """
        insert into player_game_stats
          (player_id, game_id, team_id, opponent_team_id, season, week,
           position_group, is_home, rush_yards)
        values (%s, %s, %s, %s, 2025, 5, 'RB', true, %s)
        """,
        (s["player"], s["game"], s["offense"], s["defense"], rush_yards),
    )


def _result(s):
    return s["conn"].execute(
        "select result, actual, units, model_prob from v_prop_pick_results where game_id = %s",
        (s["game"],),
    ).fetchone()


def test_a_locked_pick_is_pending_until_the_box_score_loads(slate):
    pick(slate, projection(slate), slate["first"])
    lock(slate)
    assert _result(slate)["result"] == "pending"


@pytest.mark.parametrize(
    ("rush_yards", "result", "units"),
    [(90, "win", round(100 / 115, 4)), (60, "loss", -1), (None, "void", 0)],
)
def test_the_result_against_the_box_score(slate, rush_yards, result, units):
    pick(slate, projection(slate), slate["first"])
    lock(slate)
    _finish(slate, rush_yards)

    row = _result(slate)
    assert row["result"] == result
    assert float(row["units"]) == pytest.approx(units)
    assert float(row["model_prob"]) == pytest.approx(0.62)


def test_a_stat_on_a_whole_number_line_pushes(slate):
    pick(slate, projection(slate), slate["first"], line=75)
    lock(slate)
    _finish(slate, 75)
    assert _result(slate)["result"] == "push"


def test_an_under_wins_below_the_line(slate):
    pick(slate, projection(slate), slate["first"], prob=0.40)
    lock(slate)
    _finish(slate, 60)
    row = _result(slate)
    assert row["result"] == "win"
    assert float(row["units"]) == pytest.approx(round(100 / 105, 4))
    assert float(row["model_prob"]) == pytest.approx(0.60)
