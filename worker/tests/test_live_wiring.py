"""Live model wiring (phase L2): scoreboard text, ESPN's scoreboard, the projection step."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from worker.adapters.espn.scoreboard import score_row
from worker.core.live_model import FittedModel, load_fitted
from worker.core.live_state import clock_seconds, elapsed_from, game_state, situation
from worker.jobs import live_projections as lp

NOW = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)


# --- the scoreboard's words ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("8:42", 522),
        ("08:42", 522),
        ("00:08:42", 522),
        ("0:00", 0),
        ("15:00", 900),
        ("12:55.4", 775),
        ("", None),
        (None, None),
        ("Halftime", None),
        ("16:00", None),
    ],
)
def test_clock_text_in_every_shape_a_scoreboard_uses(text, seconds):
    assert clock_seconds(text) == seconds


def test_elapsed_from_period_and_clock_and_a_stopped_clock_is_the_period_end():
    assert elapsed_from(3, "8:42") == 1800 + 900 - 522
    assert elapsed_from(2, None) == 1800  # halftime: no clock in the 2nd
    assert elapsed_from(None, "8:42") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2nd & 7 at TROY 34", (2, 66)),  # own side: 100 - 34
        ("3rd & 4 at USM 34", (3, 34)),  # the defense's side
        ("1st & Goal at USM 3", (1, 3)),
        ("4th & 1 at 50", (4, 50)),
        ("2nd & 10 at WHO 30", (2, None)),  # a name matching neither side
        ("Kickoff", (None, None)),
        (None, (None, None)),
    ],
)
def test_down_and_yards_to_goal_from_the_situation_line(text, expected):
    assert situation(text, ["TROY", "Troy"], ["USM", "Southern Miss"]) == expected


def test_game_state_reads_one_live_row_and_ignores_the_line_without_possession():
    s = game_state(
        period=4,
        clock="2:00",
        home_points=21,
        away_points=17,
        possession="away",
        situation_text="1st & 10 at WSH 37",
        home_names=["SF"],
        away_names=["WAS", "WSH"],
    )
    assert (s.elapsed, s.possession, s.down, s.yards_to_goal) == (3480, -1, 1, 63)
    no_ball = game_state(
        period=4,
        clock="2:00",
        home_points=21,
        away_points=17,
        possession=None,
        situation_text="1st & 10 at WSH 37",
        home_names=["SF"],
        away_names=["WAS"],
    )
    assert (no_ball.possession, no_ball.down, no_ball.yards_to_goal) == (0, None, None)
    assert (
        game_state(
            period=None,
            clock="2:00",
            home_points=0,
            away_points=0,
            possession=None,
            situation_text=None,
            home_names=[],
            away_names=[],
        )
        is None
    )


# --- ESPN ---------------------------------------------------------------------


def espn_event(home="SF", away="WSH", state="in", possession_id="28") -> dict:
    return {
        "competitions": [
            {
                "status": {"period": 4, "displayClock": "12:55", "type": {"state": state}},
                "situation": {
                    "possession": possession_id,
                    "downDistanceText": "2nd & 4 at WSH 37",
                    "lastPlay": {"text": " J.Daniels pass short right. "},
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": "23",
                        "team": {"id": "25", "abbreviation": home},
                        "linescores": [{"value": 13.0}, {"value": 7.0}, {"value": 3.0}],
                    },
                    {
                        "homeAway": "away",
                        "score": "10",
                        "team": {"id": "28", "abbreviation": away},
                        "linescores": [{"value": 0.0}, {"value": 10.0}, {"value": 0.0}],
                    },
                ],
            }
        ],
    }


WINDOW = {("SF", "WAS"): {"id": 16500, "home_abbr": "SF", "away_abbr": "WAS"}}


def test_an_espn_event_maps_to_our_game_with_espn_spellings_resolved():
    row = score_row(espn_event(), WINDOW)
    assert row["game_id"] == 16500
    assert (row["status"], row["period"], row["clock"]) == ("in_progress", 4, "12:55")
    assert (row["home_points"], row["away_points"]) == (23, 10)
    assert row["possession"] == "away"  # team id 28 is the away side
    assert row["home_line_scores"] == [13, 7, 3]
    assert row["situation"] == "2nd & 4 at WSH 37"
    assert row["last_play"] == "J.Daniels pass short right."


def test_espn_with_home_and_away_reversed_is_put_back_as_ours():
    row = score_row(espn_event(home="WSH", away="SF", possession_id="25"), WINDOW)
    # ESPN's "home" WSH scored 23; ours is SF at home, so the sides swap.
    assert (row["home_points"], row["away_points"]) == (10, 23)
    assert row["possession"] == "away"  # team 25 was ESPN's home side, WSH: our away


def test_espn_games_not_ours_or_not_started_write_nothing_live():
    assert score_row(espn_event(home="KC", away="DEN"), WINDOW) is None
    pre = score_row(espn_event(state="pre"), WINDOW)
    assert pre["status"] == "scheduled"
    assert (pre["period"], pre["clock"], pre["possession"], pre["situation"]) == (None,) * 4


# --- the projection step --------------------------------------------------------


def live_row(**over) -> dict:
    row = {
        "game_id": 1,
        "status": "in_progress",
        "period": 3,
        "clock": "7:30",
        "home_points": 17,
        "away_points": 10,
        "possession": "home",
        "situation": "1st & 10 at USM 20",
        "sport": "cfb",
        "h_abbr": "TROY",
        "h_school": "Troy",
        "h_nfl": None,
        "a_abbr": "USM",
        "a_school": "Southern Miss",
        "a_nfl": None,
        "margin_mean": 3.0,
        "margin_p10": -14.0,
        "margin_p90": 20.0,
        "total_mean": 52.0,
        "total_p10": 36.0,
        "total_p90": 68.0,
        "f_margin": None,
        "f_total": None,
        "projected_status": None,
    }
    return row | over


def test_a_college_game_in_progress_is_projected_from_its_game_model_prior():
    live, history = lp.project_row(live_row(), NOW)
    assert live["status"] == "in_progress" and live["prior_source"] == "game_model"
    assert (live["down"], live["yards_to_goal"]) == (1, 20)
    assert live["p_home_win"] > 0.5  # up 7 with the ball in the red zone
    assert live["margin_lo"] < live["margin_mean"] < live["margin_hi"]
    assert live["total_lo"] >= 27  # never below what is on the board
    assert history["elapsed"] == live["elapsed"] == 2700 - 450
    assert live["model"].startswith("A 2023-")


def test_an_nfl_game_uses_the_fantasy_prior_and_the_fitted_spreads():
    row = live_row(
        sport="nfl",
        h_nfl="SF",
        a_nfl="WAS",
        situation="2nd & 4 at WSH 37",
        possession="away",
        margin_mean=None,
        f_margin=6.5,
        f_total=45.0,
    )
    live, _ = lp.project_row(row, NOW)
    assert live["prior_source"] == "fantasy"
    assert live["yards_to_goal"] == 63  # WSH read as WAS's own side
    assert live["prior_margin"] == 6.5


def test_no_pregame_projection_means_no_live_one():
    assert lp.project_row(live_row(margin_mean=None), NOW) is None
    assert lp.project_row(live_row(sport="nfl", f_margin=None, f_total=None), NOW) is None


def test_the_final_write_carries_the_result():
    live, _ = lp.project_row(live_row(status="completed", period=4, clock=None), NOW)
    assert live["status"] == "completed"
    assert live["p_home_win"] == 1.0
    assert (live["margin_mean"], live["total_mean"]) == (7.0, 27.0)


@pytest.mark.parametrize("sport", ["cfb", "nfl"])
def test_the_committed_model_files_load_and_survive_a_round_trip(sport):
    model = load_fitted(sport)
    again = FittedModel.from_json(model.to_json())
    assert again.params.margin.keys() == model.params.margin.keys()
    assert again.ep.values == model.ep.values
    for e, c in model.params.margin.items():
        assert again.params.margin[e].weights == c.weights
        assert again.params.margin[e].df == c.df or (
            math.isinf(c.df) and math.isinf(again.params.margin[e].df)
        )
    assert (model.prior_margin_sd is None) == (sport == "cfb")
