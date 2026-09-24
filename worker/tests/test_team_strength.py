"""Team strength (CLAUDE.md §11, G2) — the point-in-time cutoff and the adjustment.

Offline. The property that matters most is the cutoff: a game played in week
N or later must not move a single number in the as_of_week = N row. That is
the lookahead rule of CLAUDE.md §4, and a leak here would make every game
model backtest look better than the model is.
"""

from __future__ import annotations

import pytest

from worker.core.team_strength import (
    GameSide,
    is_garbage_time,
    is_success,
    ratings_as_of,
)


@pytest.mark.parametrize(
    ("down", "distance", "gained", "expected"),
    [
        (1, 10, 5, True),
        (1, 10, 4, False),
        (2, 10, 7, True),
        (2, 10, 6, False),
        (3, 4, 4, True),
        (4, 1, 0, False),
        (None, 10, 5, None),
        (1, 0, 5, None),
    ],
)
def test_success_rule(down, distance, gained, expected):
    assert is_success(down, distance, gained) is expected


def test_garbage_time_by_quarter():
    assert not is_garbage_time(1, 50)
    assert is_garbage_time(2, 39) and not is_garbage_time(2, 38)
    assert is_garbage_time(3, -29)
    assert is_garbage_time(4, 23) and not is_garbage_time(4, 22)
    assert not is_garbage_time(5, 40)
    assert not is_garbage_time(None, 40)


def side(game, week, off, deff, points, ppa=None):
    values = {"points": float(points)}
    if ppa is not None:
        values["ppa"] = ppa
    return GameSide(game_id=game, week=week, offense_id=off, defense_id=deff, values=values)


def both(game, week, a, b, a_pts, b_pts):
    return [side(game, week, a, b, a_pts), side(game, week, b, a, b_pts)]


SEASON = [
    *both(1, 1, 1, 2, 35, 10),
    *both(2, 1, 3, 4, 21, 17),
    *both(3, 2, 1, 3, 28, 24),
    *both(4, 2, 2, 4, 14, 13),
    *both(5, 3, 1, 4, 70, 0),  # a blowout that must not reach week 3's ratings
]


def by_team(rows):
    return {r["team_id"]: r for r in rows}


def test_a_later_game_cannot_move_an_earlier_week():
    week3_all = by_team(ratings_as_of(SEASON, 2026, 3))
    # Same fit with the week-3 game deleted entirely: must be identical.
    week3_without = by_team(
        ratings_as_of([s for s in SEASON if s.week < 3], 2026, 3)
    )
    assert week3_all == week3_without
    assert week3_all[1]["games_included"] == 2
    # And it DOES move the next week, or the test above proves nothing.
    week4 = by_team(ratings_as_of(SEASON, 2026, 4))
    assert week4[1]["off_points_pg"] != week3_all[1]["off_points_pg"]


def test_no_games_before_week_two_means_no_rows_for_week_one():
    assert ratings_as_of(SEASON, 2026, 1) == []


def test_adjustment_credits_a_harder_schedule():
    # Teams 10 and 20 both score 30 every game. 10 plays only a defense (30)
    # that holds everyone else to 10; 20 plays only a defense (40) that gives
    # up 50 to everyone else. Adjusted, 10's offense must rate higher.
    sides = [
        *both(1, 1, 10, 30, 30, 0),
        *both(2, 1, 20, 40, 30, 0),
        *both(3, 1, 50, 30, 10, 0),
        *both(4, 1, 60, 40, 50, 0),
    ]
    rows = by_team(ratings_as_of(sides, 2026, 2))
    assert rows[10]["off_points_pg"] > rows[20]["off_points_pg"]
    # Defense orientation: higher allowed is worse. 40 allowed more than 30.
    assert rows[40]["def_points_pg"] > rows[30]["def_points_pg"]


def test_a_side_without_efficiency_still_counts_for_points():
    sides = [side(1, 1, 1, 2, 24), side(1, 1, 2, 1, 20, ppa=0.1)]
    rows = by_team(ratings_as_of(sides, 2026, 2))
    assert rows[1]["off_points_pg"] is not None
    assert rows[1]["off_ppa"] is None
    assert rows[2]["off_ppa"] is not None
