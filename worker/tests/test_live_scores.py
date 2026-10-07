"""College live scores (migration 0088) — offline.

The entry below is CFBD's real /scoreboard payload for Southern Miss @ Troy,
2026-10-07, trimmed to the fields read. What is pinned: a vendor enum status
is read as its value, home and away are OURS even when CFBD has them the
other way round, possession is resolved to a side or left NULL, and an
unknown status writes nothing.
"""

from __future__ import annotations

import cfbd

from worker.adapters.cfbd.live_scores import score_row

TROY, USM = 2653, 2572
GAME = {"id": 6877, "cfbd_id": 401871090, "home_cfbd": TROY, "away_cfbd": USM}


def entry(**overrides):
    base = {
        "id": 401871090,
        "status": cfbd.GameStatus("completed"),
        "period": None,
        "clock": None,
        "situation": None,
        "possession": None,
        "lastPlay": "Timeout Southern Miss, clock 00:18",
        "homeTeam": {"id": TROY, "name": "Troy Trojans", "points": 55,
                     "lineScores": [17, 14, 10, 14]},
        "awayTeam": {"id": USM, "name": "Southern Miss Golden Eagles", "points": 34,
                     "lineScores": [0, 7, 20, 7]},
    }
    return {**base, **overrides}


def test_a_final_maps_with_the_enum_status_read_as_its_value():
    row = score_row(entry(), GAME)
    assert row == {
        "game_id": 6877,
        "status": "completed",
        "period": None,
        "clock": None,
        "home_points": 55,
        "away_points": 34,
        "home_line_scores": [17, 14, 10, 14],
        "away_line_scores": [0, 7, 20, 7],
        "possession": None,
        "situation": None,
        "last_play": "Timeout Southern Miss, clock 00:18",
    }


def test_cfbd_home_and_away_reversed_are_put_back_as_ours():
    flipped = entry(homeTeam=entry()["awayTeam"], awayTeam=entry()["homeTeam"])
    row = score_row(flipped, GAME)
    assert (row["home_points"], row["away_points"]) == (55, 34)
    assert row["home_line_scores"] == [17, 14, 10, 14]


def test_possession_resolves_to_a_side_by_word_name_or_id_else_null():
    live = dict(status="in_progress", period=3, clock="08:42",
                situation="2nd & 7 at TROY 34")
    assert score_row(entry(**live, possession="home"), GAME)["possession"] == "home"
    assert score_row(entry(**live, possession="Southern Miss Golden Eagles"), GAME)[
        "possession"] == "away"
    assert score_row(entry(**live, possession=str(TROY)), GAME)["possession"] == "home"
    assert score_row(entry(**live, possession="somebody"), GAME)["possession"] is None
    row = score_row(entry(**live, possession="home"), GAME)
    assert (row["status"], row["period"], row["clock"], row["situation"]) == (
        "in_progress", 3, "08:42", "2nd & 7 at TROY 34")


def test_an_unknown_status_writes_nothing():
    assert score_row(entry(status="postponed"), GAME) is None
