"""Tests for box-score parsing.

player_game_stats is the single home for ACTUALS — every hit rate, backtest
grade and calibration number resolves against it. A parsing error here does not
raise; it produces a plausible-looking wrong number, so the awkward cases get
covered explicitly.

No network and no database.
"""

from __future__ import annotations

import pytest

from worker.adapters.cfbd.boxscore import (
    BoxScoreParser,
    _to_int,
    parse_completions_attempts,
)


def _game(category, types):
    """Build a minimal /games/players payload for one team."""
    return {
        "id": 1,
        "teams": [
            {
                "team": "Temple",
                "homeAway": "home",
                "categories": [{"name": category, "types": types}],
            }
        ],
    }


def _athlete(stat, athlete_id="4431387", name="Evan Simon"):
    return {"id": athlete_id, "name": name, "stat": stat}


# ------------------------------------------------------------ C/ATT composite


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("19/30", (19, 30)), ("0/1", (0, 1)), ("31/48", (31, 48))],
)
def test_completions_attempts_split(raw, expected):
    assert parse_completions_attempts(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "19", "bad"])
def test_completions_attempts_handles_junk(raw):
    assert parse_completions_attempts(raw) == (None, None)


def test_passing_block_produces_both_completions_and_attempts():
    parser = BoxScoreParser()
    rows = parser.parse_game(
        _game("passing", [
            {"name": "C/ATT", "athletes": [_athlete("19/30")]},
            {"name": "YDS", "athletes": [_athlete("224")]},
            {"name": "TD", "athletes": [_athlete("2")]},
            {"name": "INT", "athletes": [_athlete("1")]},
        ])
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["pass_completions"] == 19
    assert row["pass_attempts"] == 30
    assert row["pass_yards"] == 224
    assert row["pass_tds"] == 2
    assert row["interceptions"] == 1


# ----------------------------------------------------------------- value junk


def test_negative_rushing_yards_are_preserved():
    """NCAA charges sacks as rushing losses, so negatives are legitimate.

    Coercing them to 0 or None would silently inflate QB rushing.
    """
    assert _to_int("-31") == -31


def test_thousands_separators_and_placeholders():
    assert _to_int("1,234") == 1234
    assert _to_int("--") is None
    assert _to_int("") is None
    assert _to_int(None) is None


# -------------------------------------------------------------- accumulation


def test_one_row_per_athlete_across_categories():
    """A player rushing AND receiving must produce a single merged row."""
    parser = BoxScoreParser()
    payload = {
        "id": 1,
        "teams": [
            {
                "team": "Boise State",
                "homeAway": "away",
                "categories": [
                    {"name": "rushing", "types": [
                        {"name": "CAR", "athletes": [_athlete("28", "1", "Ashton Jeanty")]},
                        {"name": "YDS", "athletes": [_athlete("192", "1", "Ashton Jeanty")]},
                        {"name": "TD", "athletes": [_athlete("3", "1", "Ashton Jeanty")]},
                    ]},
                    {"name": "receiving", "types": [
                        {"name": "REC", "athletes": [_athlete("2", "1", "Ashton Jeanty")]},
                        {"name": "YDS", "athletes": [_athlete("15", "1", "Ashton Jeanty")]},
                    ]},
                ],
            }
        ],
    }
    rows = parser.parse_game(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row["rush_attempts"] == 28
    assert row["rush_yards"] == 192
    assert row["rush_tds"] == 3
    assert row["receptions"] == 2
    # Both categories carry a "YDS" type; they must land in different columns.
    assert row["rec_yards"] == 15


def test_home_away_is_captured():
    parser = BoxScoreParser()
    rows = parser.parse_game(
        _game("rushing", [{"name": "YDS", "athletes": [_athlete("50")]}])
    )
    assert rows[0]["home_away"] == "home"
    assert rows[0]["team"] == "Temple"


def test_string_athlete_ids_become_ints():
    """CFBD sends athlete ids as strings here, as it does on /roster."""
    parser = BoxScoreParser()
    rows = parser.parse_game(
        _game("rushing", [{"name": "YDS", "athletes": [_athlete("50", "4431387")]}])
    )
    assert rows[0]["cfbd_athlete_id"] == 4431387
    assert isinstance(rows[0]["cfbd_athlete_id"], int)


# ------------------------------------------------------------------- unknowns


def test_unrecognised_stat_types_are_recorded():
    """A new label must be reported, not silently dropped."""
    parser = BoxScoreParser()
    parser.parse_game(
        _game("rushing", [{"name": "BRAND_NEW", "athletes": [_athlete("5")]}])
    )
    assert parser.unknown_types["rushing.BRAND_NEW"] == 1


def test_known_ignored_types_are_not_flagged():
    """AVG/LONG are derivable and ignored on purpose; they are not 'unknown'."""
    parser = BoxScoreParser()
    parser.parse_game(
        _game("rushing", [{"name": "AVG", "athletes": [_athlete("6.9")]}])
    )
    assert not parser.unknown_types


def test_defensive_categories_are_skipped():
    parser = BoxScoreParser()
    rows = parser.parse_game(
        _game("defensive", [{"name": "TOT", "athletes": [_athlete("9")]}])
    )
    assert rows == []
    assert not parser.unknown_types


def test_empty_payload_is_safe():
    assert BoxScoreParser().parse_game({}) == []
    assert BoxScoreParser().parse_game({"teams": []}) == []
