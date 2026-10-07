"""The coach-season mapping (migration 0084).

The failure worth guarding is quiet: a season CFBD has not attributed yet
arriving as "0-0 at home" rather than "not recorded", which would drag a
coach's split records down on the page without anything looking wrong.
"""

from __future__ import annotations

from worker.adapters.cfbd.ingest_coaches import SPLITS, estimate_calls, season_row

TEAMS = {2653: 77}  # CFBD Troy -> our team id


def _entry(**overrides):
    entry = {
        "games": 14, "wins": 8, "losses": 6, "ties": 0,
        "coach": {"id": 628, "firstName": "Gerad", "lastName": "Parker"},
        "team": {"id": 2653, "school": "Troy"},
        "year": 2025,
        "recordSplits": {
            "conference": {"wins": 6, "losses": 2},
            "postseason": {"wins": 0, "losses": 1},
            "home": {"wins": 4, "losses": 2},
            "away": {"wins": 4, "losses": 3},
            "neutral": {"wins": 0, "losses": 1},
        },
    }
    entry.update(overrides)
    return entry


def test_a_season_maps_its_record_and_every_split():
    row = season_row(_entry(), TEAMS)
    assert row["coach_id"] == 628 and row["season"] == 2025 and row["team_id"] == 77
    assert (row["games"], row["wins"], row["losses"]) == (14, 8, 6)
    assert (row["conf_wins"], row["conf_losses"]) == (6, 2)
    assert (row["post_wins"], row["post_losses"]) == (0, 1)
    assert (row["neutral_wins"], row["neutral_losses"]) == (0, 1)


def test_an_unattributed_season_keeps_its_splits_null_not_zero():
    row = season_row(_entry(year=2026, games=0, wins=0, losses=0, recordSplits=None), TEAMS)
    for prefix in SPLITS.values():
        assert row[f"{prefix}_wins"] is None and row[f"{prefix}_losses"] is None


def test_a_school_we_do_not_hold_still_counts_toward_a_career():
    row = season_row(_entry(team={"id": 99999, "school": "Some D-II School"}), TEAMS)
    assert row["team_id"] is None and row["school"] == "Some D-II School"


def test_a_season_with_no_year_or_school_is_skipped():
    assert season_row(_entry(year=None), TEAMS) is None
    assert season_row(_entry(team={"id": 2653}), TEAMS) is None


def test_the_call_estimate_is_one_listing_plus_one_per_coach():
    assert estimate_calls(138) == 139
