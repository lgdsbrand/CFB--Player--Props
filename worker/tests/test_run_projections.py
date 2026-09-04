"""Integration tests for which weeks the live job publishes (Phase 6c/6e).

Skipped when SUPABASE_DB_URL is unset, like the other integration modules here.
Read-only: nothing in this file writes.

WHY THIS FILE EXISTS AT ALL. `projectable_weeks` is four lines of SQL and had no
test through the phase that changed what it means. Both of the properties it
now carries are invisible to the type checker and to review:

1. **The season starts at week 1.** The floor that used to sit here was removed
   in Phase 6c, and re-adding one would empty the opening weekends off the board
   while every other test in the suite passed — `is_projectable` would still be
   correct, the walk would still grade weeks 1-2, and nobody would publish them.
   That is the exact failure Phase 6 exists to close, arriving by a different
   door.

2. **`season_type = 'regular'` is now the ONLY thing keeping bowls off the
   board.** Before 6c a mislabelled postseason game sitting at week 1 was
   excluded twice over — by the predicate and by the floor. The floor is gone.
   Migration 0020 offsets postseason weeks past the regular season precisely
   because CFBD once numbered a December bowl as week 1, and that bug produced
   real lookahead into every earlier week before it was found.

Both tests derive their expectation from `games` with a different formulation
than the function uses, so they check the query rather than restate it.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.rows import dict_row  # noqa: E402

from worker.config import ConfigError, get_settings  # noqa: E402
from worker.jobs.run_projections import (  # noqa: E402
    LIVE_WEEK_HORIZON_DAYS,
    live_weeks,
    projectable_weeks,
)

pytestmark = pytest.mark.integration


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
def season_with_a_schedule(conn):
    row = conn.execute(
        """
        select season from games
         where season_type = 'regular'
         group by season
        having min(week) = 1
         order by season desc
         limit 1
        """
    ).fetchone()
    if not row:
        pytest.skip("no season with a regular-season week 1 on the schedule")
    return int(row["season"])


def test_the_season_starts_at_week_one(season_with_a_schedule):
    """Phase 6c's deliverable, stated as the property rather than the diff."""
    weeks = projectable_weeks(season_with_a_schedule)
    assert weeks, "a season with a schedule must have projectable weeks"
    assert min(weeks) == 1
    assert 2 in weeks


@pytest.fixture
def season_with_postseason(conn):
    """A season that actually has bowls, which the newest one does not.

    Deliberately a separate fixture. Sharing `season_with_a_schedule` picked the
    latest season — the one whose postseason has not been played yet — and the
    postseason test skipped itself on every run while reporting green.
    """
    row = conn.execute(
        """
        select season from games
         where season_type <> 'regular'
         group by season
         order by season desc
         limit 1
        """
    ).fetchone()
    if not row:
        pytest.skip("no postseason games ingested in any season")
    return int(row["season"])


def test_no_postseason_week_reaches_the_board(conn, season_with_postseason):
    """Bowls are a different regime and the backtest excludes them, so the
    published population would stop being the graded one."""
    postseason = {
        int(r["week"])
        for r in conn.execute(
            """
            select distinct week from games
             where season = %(season)s and season_type <> 'regular'
            """,
            {"season": season_with_postseason},
        ).fetchall()
    }
    assert postseason, "the fixture guarantees this season has postseason games"

    weeks = set(projectable_weeks(season_with_postseason))
    assert weeks.isdisjoint(postseason), (
        f"postseason weeks {sorted(weeks & postseason)} would be published; "
        "season_type is the only guard left since the week floor was removed"
    )


def test_every_scheduled_regular_week_is_offered(conn, season_with_a_schedule):
    """No week is silently dropped — the complement of the test above.

    Written because a predicate tightened to keep bowls out could just as
    easily take November with it, and a board missing week 12 looks like a
    quiet slate rather than a bug.
    """
    expected = {
        int(r["week"])
        for r in conn.execute(
            """
            select distinct week from games
             where season = %(season)s and season_type = 'regular'
            """,
            {"season": season_with_a_schedule},
        ).fetchall()
    }
    assert set(projectable_weeks(season_with_a_schedule)) == expected


# -----------------------------------------------------------------------------
# live_weeks — what the daily cron re-prices
# -----------------------------------------------------------------------------
# These derive their expectation from `games` rather than restating the query,
# for the same reason the tests above do. The property that matters is not "the
# SQL is the SQL" but "a week nobody can still bet never gets re-priced, and a
# week they can always does".


@pytest.fixture
def season_with_upcoming_games(conn):
    row = conn.execute(
        """
        select season from games
         where season_type = 'regular' and start_date > now()
         group by season
         order by season desc
         limit 1
        """
    ).fetchone()
    if not row:
        pytest.skip("no unplayed regular-season games — nothing is live")
    return int(row["season"])


def test_live_weeks_are_a_subset_of_projectable_weeks(season_with_upcoming_games):
    """The daily run must never publish something the weekly run would not.

    `--current-week` narrows the population; it does not widen it. If this ever
    fails, the two crons disagree about what belongs on the board and which one
    ran last decides what a reader sees.
    """
    season = season_with_upcoming_games
    assert set(live_weeks(season)) <= set(projectable_weeks(season))


def test_every_live_week_still_has_a_game_to_play(conn, season_with_upcoming_games):
    """A week whose games have all kicked is finished, and re-pricing it would
    put closed markets back on the board."""
    season = season_with_upcoming_games
    for week in live_weeks(season):
        row = conn.execute(
            """
            select count(*) as n from games
             where season = %(season)s and week = %(week)s
               and season_type = 'regular' and start_date > now()
            """,
            {"season": season, "week": week},
        ).fetchone()
        assert int(row["n"]) > 0, f"week {week} is live but has no unplayed game"


def test_live_weeks_excludes_games_beyond_the_horizon(conn, season_with_upcoming_games):
    """The horizon is the point of the flag — without it this is --all-weeks.

    Derived from the data rather than asserted as a week number, because which
    weeks are near depends entirely on when the suite runs.
    """
    season = season_with_upcoming_games
    beyond = {
        int(r["week"])
        for r in conn.execute(
            """
            select distinct week from games
             where season = %(season)s and season_type = 'regular'
             group by week
            having min(start_date) > now() + make_interval(days => %(days)s)
            """,
            {"season": season, "days": LIVE_WEEK_HORIZON_DAYS},
        ).fetchall()
    }
    assert set(live_weeks(season)).isdisjoint(beyond), (
        "a week whose earliest game is past the horizon was returned"
    )


def test_a_wider_horizon_never_returns_less(season_with_upcoming_games):
    """Monotonicity — cheap, and it catches an inverted comparison.

    A `>` written where `<` belonged would still return plausible-looking weeks
    on any single run, and this is the property that notices.
    """
    season = season_with_upcoming_games
    near = set(live_weeks(season, horizon_days=1))
    far = set(live_weeks(season, horizon_days=60))
    assert near <= far


def test_the_horizon_covers_the_following_weekend(season_with_upcoming_games):
    """Eight days, not seven, and the reason is the late-line behaviour.

    College books post props Thursday or Friday (CLAUDE.md §7). A seven-day
    window evaluated on a Friday ends the following Friday, dropping the next
    weekend on the very day its lines first appear.
    """
    assert LIVE_WEEK_HORIZON_DAYS >= 8
