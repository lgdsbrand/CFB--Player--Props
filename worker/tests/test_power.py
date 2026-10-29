"""Power ratings and game score (worker/core/power.py): pure functions only."""

from __future__ import annotations

import pytest

from worker.core.power import (
    MARGIN_CAP,
    Final,
    PowerSettings,
    game_score,
    home_field,
    ranks,
    ratings,
    season_rows,
)

SETTINGS = PowerSettings(last_season_games=3, margin_sd=13.0)


def final(week: int, home: int, away: int, margin: float, *, season: int = 2026,
          neutral: bool = False) -> Final:
    return Final(season, week, home, away, margin, neutral)


def game(gid: int, week: int, home: int, away: int, home_pts: int | None, away_pts: int | None,
         *, season: int = 2026, season_type: str = "regular", neutral: bool = False) -> dict:
    return {"id": gid, "season": season, "week": week, "season_type": season_type,
            "neutral_site": neutral, "home_team_id": home, "away_team_id": away,
            "home_points": home_pts, "away_points": away_pts, "completed": home_pts is not None}


def test_the_team_that_beats_everyone_rates_first_and_ratings_centre_on_zero() -> None:
    games = [final(1, 1, 2, 10), final(2, 3, 1, -7), final(3, 2, 3, 3), final(4, 3, 2, 3)]
    rating, _ = ratings(games, [], 3, {1, 2, 3})
    assert ranks(rating, {1, 2, 3})[1] == 1
    assert sum(rating.values()) == pytest.approx(0.0, abs=1e-9)


def test_margins_are_capped_so_a_blowout_buys_nothing_past_four_touchdowns() -> None:
    a, _ = ratings([final(1, 1, 2, MARGIN_CAP, neutral=True)], [], 3, {1, 2})
    b, _ = ratings([final(1, 1, 2, 70, neutral=True)], [], 3, {1, 2})
    assert a == pytest.approx(b)


def test_home_field_comes_from_games_between_ranked_teams_only() -> None:
    # FBS hosting FCS by 40 is not home field.
    games = [final(1, 1, 2, 3), final(1, 3, 9, 40)]
    assert home_field(games, {1, 2, 3}) == pytest.approx(3.0)


def test_a_two_team_league_settles_instead_of_swinging() -> None:
    games = [final(1, 1, 2, 7, neutral=True), final(2, 2, 1, 0, neutral=True)]
    rating, _ = ratings(games, [], 3, {1, 2})
    assert rating[1] == pytest.approx(1.75, abs=1e-3)
    assert rating[2] == pytest.approx(-1.75, abs=1e-3)


def test_last_season_counts_as_three_games_per_team() -> None:
    # Last season: team 2 beat team 1 by 20, ten times. This season, one game:
    # team 1 by 10. Three games' worth of last season outweigh it.
    last = [final(w, 2, 1, 20, season=2025, neutral=True) for w in range(1, 11)]
    rating, _ = ratings([final(1, 1, 2, 10, neutral=True)], last, 3, {1, 2})
    assert rating[2] > rating[1]
    rating, _ = ratings([final(w, 1, 2, 10, neutral=True) for w in range(1, 9)], last, 3, {1, 2})
    assert rating[1] > rating[2]


def test_game_score_prefers_top_teams_in_a_close_game() -> None:
    best = game_score(1, 2, 32, 0.5)
    assert best > game_score(1, 32, 32, 0.9)
    assert best > game_score(31, 32, 32, 0.5)
    assert game_score(1, 1, 32, 1.0) == 60


def test_week_n_is_rated_only_on_games_before_week_n() -> None:
    games = [
        game(1, 1, 1, 2, 30, 0, neutral=True), game(2, 2, 2, 3, 30, 0, neutral=True),
        game(3, 3, 3, 1, 50, 0, neutral=True),  # week 3: must not move week 3's own ratings
        game(4, 3, 1, 2, None, None),  # week 3's upcoming game
    ]
    teams, rows = season_rows(2026, games, {1, 2, 3}, SETTINGS)
    week3 = {r["team_id"]: r for r in teams if r["as_of_week"] == 3}
    week4 = {r["team_id"]: r for r in teams if r["as_of_week"] == 4}
    assert week3[1]["rating"] > week3[3]["rating"]          # before 3 beat 1 by 50
    assert week4[3]["rating"] > week3[3]["rating"]          # the postseason row has it
    assert week3[1]["games_included"] == 1
    upcoming = next(r for r in rows if r["game_id"] == 4)
    assert upcoming["as_of_week"] == 3
    assert 0 <= upcoming["game_score"] <= 100


def test_postseason_games_are_rated_on_the_whole_regular_season() -> None:
    games = [game(1, 1, 1, 2, 21, 14), game(2, 2, 2, 1, 10, 7),
             game(3, 1, 1, 2, None, None, season_type="postseason", neutral=True)]
    _, rows = season_rows(2026, games, {1, 2}, SETTINGS)
    bowl = next(r for r in rows if r["game_id"] == 3)
    assert bowl["as_of_week"] == 3


def test_a_team_outside_the_ranked_league_is_rated_but_has_no_rank() -> None:
    games = [game(1, 1, 1, 9, 50, 0), game(2, 1, 2, 3, 7, 3), game(3, 2, 1, 2, None, None)]
    teams, rows = season_rows(2026, games, {1, 2, 3}, SETTINGS)
    week2 = {r["team_id"]: r for r in teams if r["as_of_week"] == 2}
    assert week2[9]["power_rank"] is None and week2[9]["rating"] < 0
    assert sorted(r["power_rank"] for t, r in week2.items() if t != 9) == [1, 2, 3]
