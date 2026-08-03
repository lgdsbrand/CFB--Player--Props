"""Tests for "what week is it" (Phase 5d).

The rule has awkward edges — Tuesday MACtion, a slate still in progress at
midnight UTC, and eight months of offseason — which is why the decision is
separated from the query and tested without a database.

THE OFFSEASON CASE IS THE ONE THAT MATTERS. A monitor that does not know the
season is over sends a stale-data alert every day from January to August, and by
kickoff nobody reads them. That failure is not a crash; it is an alerting system
that has trained its audience to ignore it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from worker.core.schedule import (
    IN_SEASON_WINDOW,
    Slate,
    SlateWeek,
    pick_slate,
)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _week(season: int, week: int, first: datetime, last: datetime | None = None) -> SlateWeek:
    return SlateWeek(season, week, first, last or first + timedelta(hours=10))


# A tidy three-week season: Saturdays 5, 12 and 19 September 2026.
SEASON = [
    _week(2026, 1, _utc(2026, 9, 5, 16), _utc(2026, 9, 6, 3)),
    _week(2026, 2, _utc(2026, 9, 12, 16), _utc(2026, 9, 13, 3)),
    _week(2026, 3, _utc(2026, 9, 19, 16), _utc(2026, 9, 20, 3)),
]


def test_no_weeks_returns_none() -> None:
    # Distinct from "the season is over": nothing is ingested at all, which is a
    # problem, whereas a finished season is not.
    assert pick_slate([], _utc(2026, 9, 10)) is None


def test_midweek_points_at_the_upcoming_slate() -> None:
    slate = pick_slate(SEASON, _utc(2026, 9, 9, 12))  # Wednesday
    assert (slate.season, slate.week) == (2026, 2)
    assert slate.in_season is True
    assert slate.complete is False


def test_a_slate_in_progress_is_the_current_one() -> None:
    slate = pick_slate(SEASON, _utc(2026, 9, 12, 20))  # Saturday evening
    assert slate.week == 2
    assert slate.complete is False


def test_a_slate_stays_current_through_the_trailing_window() -> None:
    # Sunday 09:00 UTC — when the ingest cron fires. It is working on
    # Saturday's games, and a resolver that had already rolled to week 3 would
    # send every job to ingest a week that has not been played.
    slate = pick_slate(SEASON, _utc(2026, 9, 13, 9))
    assert slate.week == 2
    assert slate.complete is True


def test_the_trailing_window_eventually_expires() -> None:
    slate = pick_slate(SEASON, _utc(2026, 9, 14, 12))  # Monday
    assert slate.week == 3


def test_overlapping_windows_prefer_the_later_week() -> None:
    # College plays Tuesday and Wednesday games, so week N+1 can kick off while
    # week N is still inside its trailing window. Sorting by kickoff and taking
    # the last match is what resolves it; taking the first would pin the
    # pipeline to a week that has already been played.
    overlapping = [
        _week(2026, 4, _utc(2026, 9, 26, 16), _utc(2026, 9, 27, 3)),
        _week(2026, 5, _utc(2026, 9, 27, 0), _utc(2026, 10, 4, 3)),
    ]
    slate = pick_slate(overlapping, _utc(2026, 9, 27, 2))
    assert slate.week == 5


def test_after_the_last_game_the_season_is_over_not_missing() -> None:
    slate = pick_slate(SEASON, _utc(2027, 3, 1))
    assert isinstance(slate, Slate)
    assert (slate.season, slate.week) == (2026, 3)
    assert slate.complete is True
    # THE POINT OF THE WHOLE MODULE.
    assert slate.in_season is False


def test_the_offseason_boundary_is_the_window_not_the_calendar() -> None:
    last_kickoff = _utc(2026, 9, 20, 3)
    just_inside = last_kickoff + IN_SEASON_WINDOW - timedelta(hours=1)
    just_outside = last_kickoff + IN_SEASON_WINDOW + timedelta(hours=1)

    assert pick_slate(SEASON, just_inside).in_season is True
    assert pick_slate(SEASON, just_outside).in_season is False


def test_preseason_is_in_season_before_the_first_kickoff() -> None:
    # Ingest and projections run before week 1 is played. Treating the days
    # before the opener as offseason would switch monitoring off during the
    # exact window in which the pipeline is being brought up for the year.
    before = _utc(2026, 9, 5, 16) - timedelta(days=5)
    slate = pick_slate(SEASON, before)
    assert slate.week == 1
    assert slate.in_season is True


def test_naive_timestamps_are_read_as_utc_rather_than_crashing() -> None:
    # Comparing a naive datetime to an aware one raises, and a monitor that
    # crashes on a timezone is a monitor that is not monitoring.
    naive = [
        SlateWeek(2026, 1, datetime(2026, 9, 5, 16), datetime(2026, 9, 6, 3)),
    ]
    slate = pick_slate(naive, _utc(2026, 9, 5, 20))
    assert slate.week == 1
    assert slate.complete is False
