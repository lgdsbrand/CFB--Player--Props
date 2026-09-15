"""`grade_vs_book`'s line selection, against the real schema.

    pytest tests/test_grade_vs_book_queries.py -v

Skipped when SUPABASE_DB_URL is unset, and everything runs inside one
transaction that is rolled back at teardown. Fixture rows carry negative
`cfbd_id`s and a season no real data uses.

WHY THIS EXISTS. On 2026-09-15 the first NFL grade was about to run off the live
six-hourly captures, and the query would have produced a confident, wrong
number three ways at once: it pooled college lines of the same week (the table
carries no sport), it counted each of ~16 snapshots per book as its own price
and every number a book moved off as its own bet, and it kept prices captured
after kickoff. Each is a property of a SQL string that reads plausibly either
way, so only an engine can settle it.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.rows import dict_row  # noqa: E402

from worker.config import ConfigError, get_settings  # noqa: E402
from worker.jobs import grade_vs_book  # noqa: E402

pytestmark = pytest.mark.integration

SEASON = 2031
WEEK = 1
ADAPTER = "theoddsapi"
KICKOFF = "2031-09-14 17:00:00+00"


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
def db(conn, monkeypatch):
    """Run the job's SQL, unchanged, inside the rolled-back transaction."""

    def fetch_all(sql, params=None):
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    monkeypatch.setattr(grade_vs_book, "fetch_all", fetch_all)
    return conn


def _insert(cur, sql: str, params: tuple) -> int:
    cur.execute(sql + " returning id", params)
    return cur.fetchone()["id"]


@pytest.fixture
def slate(db):
    """An NFL game and a college game in the same season and week, one player
    projected and box-scored in each, and two test books."""
    cur = db.cursor()
    cur.execute(
        "insert into model_runs (run_type, model_version, season, week, as_of_week) "
        "values ('weekly', 'test-grade', %s, %s, %s) returning id",
        (SEASON, WEEK, WEEK),
    )
    run_id = cur.fetchone()["id"]

    books = {
        key: _insert(
            cur,
            "insert into sportsbooks (key, display_name, priority) values (%s, %s, 999)",
            (key, key),
        )
        for key in ("gradetesta", "gradetestb")
    }

    games = {}
    for n, sport in enumerate(("nfl", "cfb")):
        # `nfl_abbr` is required on NFL teams (teams_nfl_requires_abbr).
        home = _insert(
            cur,
            "insert into teams (cfbd_id, school, abbreviation, nfl_abbr, sport) "
            "values (%s, %s, %s, %s, %s)",
            (-951 - 2 * n, f"Grade Home {sport}", f"GH{n}",
             f"GH{n}" if sport == "nfl" else None, sport),
        )
        away = _insert(
            cur,
            "insert into teams (cfbd_id, school, abbreviation, nfl_abbr, sport) "
            "values (%s, %s, %s, %s, %s)",
            (-952 - 2 * n, f"Grade Away {sport}", f"GA{n}",
             f"GA{n}" if sport == "nfl" else None, sport),
        )
        game = _insert(
            cur,
            "insert into games (cfbd_id, season, week, start_date, home_team_id, "
            "away_team_id, sport) values (%s, %s, %s, %s, %s, %s, %s)",
            (-951 - n, SEASON, WEEK, KICKOFF, home, away, sport),
        )
        player = _insert(
            cur,
            "insert into players (name, position_group, sport) values (%s, 'RB', %s)",
            (f"Gradeline Tester {sport}", sport),
        )
        cur.execute(
            "insert into projections (model_run_id, player_id, game_id, team_id, "
            "opponent_team_id, market_key, season, week, as_of_week, distribution, "
            "params) values (%s, %s, %s, %s, %s, 'rush_yards', %s, %s, %s, 'normal', "
            "'{\"mean\": 60, \"sd\": 20}')",
            (run_id, player, game, home, away, SEASON, WEEK, WEEK),
        )
        cur.execute(
            "insert into player_game_stats (player_id, game_id, team_id, "
            "opponent_team_id, season, week, position_group, is_home, rush_yards) "
            "values (%s, %s, %s, %s, %s, %s, 'RB', true, 71)",
            (player, game, home, away, SEASON, WEEK),
        )
        games[sport] = {"game": game, "player": player}

    return {"cur": cur, "books": books, "games": games}


def _line(slate, sport, book, line, captured_at, *, closing=False):
    g = slate["games"][sport]
    slate["cur"].execute(
        "insert into player_prop_lines (game_id, player_id, market_key, "
        "sportsbook_id, season, week, line, over_price, under_price, "
        "source_adapter, captured_at, is_closing) "
        "values (%s, %s, 'rush_yards', %s, %s, %s, %s, -110, -110, %s, %s, %s)",
        (g["game"], g["player"], slate["books"][book], SEASON, WEEK, line,
         ADAPTER, captured_at, closing),
    )


def _load(sport, *, closing_only):
    return grade_vs_book.load_gradeable(
        sport, SEASON, WEEK, ADAPTER, closing_only=closing_only
    )


def test_each_book_counts_once_at_its_last_pre_kickoff_quote(slate):
    _line(slate, "nfl", "gradetesta", 60.5, "2031-09-13 11:00:00+00")  # stale
    _line(slate, "nfl", "gradetesta", 62.5, "2031-09-14 15:00:00+00")  # last pre-kick
    _line(slate, "nfl", "gradetesta", 40.5, "2031-09-14 18:00:00+00")  # in-play
    _line(slate, "nfl", "gradetestb", 62.5, "2031-09-14 14:00:00+00")

    rows = _load("nfl", closing_only=False)

    assert [float(r["line"]) for r in rows] == [62.5]
    assert sorted(p["sportsbook_key"] for p in rows[0]["prices"]) == [
        "gradetesta", "gradetestb",
    ]
    assert float(rows[0]["lead_hours"]) == pytest.approx(2.0)


def test_the_other_sport_of_the_same_week_is_not_pooled(slate):
    _line(slate, "nfl", "gradetesta", 62.5, "2031-09-14 15:00:00+00")
    _line(slate, "cfb", "gradetesta", 88.5, "2031-09-14 15:00:00+00")

    nfl = _load("nfl", closing_only=False)
    cfb = _load("cfb", closing_only=False)

    assert [float(r["line"]) for r in nfl] == [62.5]
    assert [float(r["line"]) for r in cfb] == [88.5]


def test_closing_only_reads_only_closing_rows(slate):
    _line(slate, "nfl", "gradetesta", 62.5, "2031-09-14 15:00:00+00")
    _line(slate, "nfl", "gradetestb", 63.5, "2031-09-14 16:30:00+00", closing=True)

    rows = _load("nfl", closing_only=True)

    assert [float(r["line"]) for r in rows] == [63.5]
    assert [p["sportsbook_key"] for p in rows[0]["prices"]] == ["gradetestb"]


def test_line_age_is_described_from_the_rows():
    rows = [{"lead_hours": 2.0}, {"lead_hours": 30.0}, {"lead_hours": None}]
    assert grade_vs_book.describe_line_age(rows) == (
        "line age at kickoff: median 2.0h, oldest 30.0h"
    )
    assert grade_vs_book.describe_line_age([]) is None
