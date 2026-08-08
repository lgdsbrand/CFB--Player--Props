"""The odds write path, against the real schema.

    pytest tests/test_odds_write_path.py -v

`test_ingest_odds.py` is deliberately database-free and covers the resolution
decisions. This file covers the half that only a real engine can answer: what
`ingest_event` leaves behind in `player_prop_lines`.

Everything runs inside one transaction that is rolled back at teardown, and the
fixture rows carry negative `cfbd_id`s the way `test_schema_constraints.py`
marks its synthetic rows, so nothing survives and nothing collides with real
data.

WHY THIS EXISTS. Synthetic development lines and real bought lines shared a
player and market for three days in August 2026: week 8 of 2025 was seeded with
synthetic quotes on the 4th, the real closing lines landed on the 5th, and 556
board picks stayed priced off the fake one — whose -110/-110 de-vigs to exactly
0.500, so it reads as a book disagreeing with us at maximum confidence.
`audit_data` had a check for it and the check was right; it just needed somebody
to run it. The eviction below makes the collision impossible instead.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.rows import dict_row  # noqa: E402

from worker.adapters.odds import SYNTHETIC_ADAPTER  # noqa: E402
from worker.adapters.odds.base import BookPrice, PropQuote  # noqa: E402
from worker.config import ConfigError, get_settings  # noqa: E402
from worker.jobs.ingest_odds import IngestReport, ingest_event  # noqa: E402

REAL_ADAPTER = "theoddsapi"
SEASON = 2025
WEEK = 5


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
def slate(conn):
    """One game, one player on it, and a synthetic line already written."""
    cur = conn.cursor()

    cur.execute(
        "insert into teams (cfbd_id, school, abbreviation) "
        "values (-901, 'Write Path Offense', 'WPO') returning id"
    )
    offense = cur.fetchone()["id"]
    cur.execute(
        "insert into teams (cfbd_id, school, abbreviation) "
        "values (-902, 'Write Path Defense', 'WPD') returning id"
    )
    defense = cur.fetchone()["id"]

    cur.execute(
        "insert into games (cfbd_id, season, week, home_team_id, away_team_id) "
        "values (-901, %s, %s, %s, %s) returning id",
        (SEASON, WEEK, offense, defense),
    )
    game_id = cur.fetchone()["id"]

    # A distinctive name so the resolver cannot reasonably match anything else.
    cur.execute(
        "insert into players (name, position_group) "
        "values ('Quillon Writepath', 'RB') returning id"
    )
    player_id = cur.fetchone()["id"]
    cur.execute(
        "insert into player_team_seasons (player_id, team_id, season, position_group) "
        "values (%s, %s, %s, 'RB')",
        (player_id, offense, SEASON),
    )

    cur.execute(
        "insert into sportsbooks (key, display_name, priority) "
        "values ('devwrite', 'DEV (synthetic)', 999) returning id"
    )
    synthetic_book = cur.fetchone()["id"]

    cur.execute(
        """
        insert into player_prop_lines
          (game_id, player_id, market_key, sportsbook_id, season, week,
           line, over_price, under_price, source_adapter)
        values (%s, %s, 'rush_yards', %s, %s, %s, 62.5, -110, -110, %s)
        """,
        (game_id, player_id, synthetic_book, SEASON, WEEK, SYNTHETIC_ADAPTER),
    )

    return {
        "game": {
            "id": game_id,
            "season": SEASON,
            "week": WEEK,
            "home_team_id": offense,
            "away_team_id": defense,
        },
        "player_id": player_id,
    }


def _quote(market: str = "rush_yards", line: float = 58.5) -> PropQuote:
    return PropQuote(
        event_id="evt-write-path",
        market_key=market,
        player_name="Quillon Writepath",
        prices=[
            BookPrice(
                sportsbook_key="draftkings",
                sportsbook_name="DraftKings",
                line=line,
                over_price=-115,
                under_price=-105,
            )
        ],
    )


def _lines(conn, game_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "select source_adapter, market_key, line from player_prop_lines "
        "where game_id = %s order by source_adapter, market_key",
        (game_id,),
    )
    return cur.fetchall()


@pytest.mark.integration
def test_a_real_quote_evicts_the_synthetic_line_it_replaces(conn, slate):
    """The collision that actually happened, prevented at the source."""
    report = IngestReport()
    ingest_event(
        conn, [_quote()], slate["game"], REAL_ADAPTER, report, dry_run=False
    )

    rows = _lines(conn, slate["game"]["id"])
    adapters = {r["source_adapter"] for r in rows}

    assert adapters == {REAL_ADAPTER}, (
        f"expected the synthetic row to be gone, found {adapters}"
    )
    assert report.rows_written == 1
    assert report.synthetic_displaced == 1


@pytest.mark.integration
def test_eviction_is_scoped_to_the_player_and_market_that_was_replaced(conn, slate):
    """A real receiving line must not evict a synthetic rushing line.

    The eviction keys on (game, player, market) precisely so that a book pricing
    one of a player's markets does not silently strip the development lines that
    let the board render his others.
    """
    cur = conn.cursor()
    cur.execute(
        """
        insert into player_prop_lines
          (game_id, player_id, market_key, sportsbook_id, season, week,
           line, over_price, under_price, source_adapter)
        select %s, %s, 'receptions', sportsbook_id, %s, %s, 3.5, -110, -110, %s
          from player_prop_lines where game_id = %s limit 1
        """,
        (
            slate["game"]["id"], slate["player_id"], SEASON, WEEK,
            SYNTHETIC_ADAPTER, slate["game"]["id"],
        ),
    )

    report = IngestReport()
    ingest_event(
        conn, [_quote("rush_yards")], slate["game"], REAL_ADAPTER, report, dry_run=False
    )

    rows = _lines(conn, slate["game"]["id"])
    surviving_synthetic = {
        r["market_key"] for r in rows if r["source_adapter"] == SYNTHETIC_ADAPTER
    }
    assert surviving_synthetic == {"receptions"}
    assert report.synthetic_displaced == 1


@pytest.mark.integration
def test_a_dry_run_evicts_nothing(conn, slate):
    """A dry run reports what it would resolve and writes nothing.

    Deleting on a dry run would be the worst of both: no new rows, and the
    development lines the board was using gone anyway.
    """
    report = IngestReport()
    ingest_event(
        conn, [_quote()], slate["game"], REAL_ADAPTER, report, dry_run=True
    )

    rows = _lines(conn, slate["game"]["id"])
    assert [r["source_adapter"] for r in rows] == [SYNTHETIC_ADAPTER]
    assert report.rows_written == 0
    assert report.synthetic_displaced == 0


@pytest.mark.integration
def test_writing_synthetic_lines_does_not_evict_anything(conn, slate):
    """The guard must not fire on the job that writes synthetic rows itself,
    or `run_projections --synthetic-lines` would delete its own work."""
    report = IngestReport()
    ingest_event(
        conn, [_quote(line=71.5)], slate["game"], SYNTHETIC_ADAPTER, report,
        dry_run=False,
    )

    rows = _lines(conn, slate["game"]["id"])
    assert all(r["source_adapter"] == SYNTHETIC_ADAPTER for r in rows)
    assert report.synthetic_displaced == 0
