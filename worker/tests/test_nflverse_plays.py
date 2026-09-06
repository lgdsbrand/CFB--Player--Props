"""Tests for the NFL play-by-play loader and its attribution (N4).

No network and no database. Everything here is about the two translations that
would be wrong SILENTLY:

  * **The attribution rules.** `stat_type` is CFBD's vocabulary, read by literal
    in `core/splits.py` and `core/features.py`. A 'Target' emitted on a
    completion, or a missing 'Touchdown' row on the passer, produces splits that
    are plausible and wrong -- nothing raises, the numbers simply shift.
  * **The row filter.** `plays` is stored filtered for NFL and complete for
    college, so the predicate that decides which plays survive is load-bearing
    rather than an optimisation.
"""

from __future__ import annotations

import polars as pl
import pytest

from worker.adapters.nflverse.ingest_plays import (
    PLAY_COLUMNS,
    PLAY_PLAYER_COLUMNS,
    SKILL_ID_COLUMNS,
    SOURCE_COLUMNS,
    _attribution_for,
    select_source_rows,
)


def play(**overrides):
    """One source play, defaulting every column the loader reads."""
    base = {c: None for c in SOURCE_COLUMNS}
    base.update({
        "play_id": 1, "game_id": "2025_01_NE_SEA", "season": 2025, "week": 1,
        "posteam": "NE", "defteam": "SEA", "qtr": 1,
        "quarter_seconds_remaining": 900, "down": 1, "ydstogo": 10,
        "yardline_100": 75, "yards_gained": 0, "play_type": "pass",
        "desc": "", "sp": 0, "epa": 0.0, "posteam_score": 0, "defteam_score": 0,
        "complete_pass": 0, "rush_touchdown": 0, "pass_touchdown": 0,
    })
    base.update(overrides)
    return base


def types_for(row):
    return [(who, what) for who, what, _ in _attribution_for(row)]


class TestTheVocabularyIsTheCoresOwn:
    def test_stat_types_are_the_literals_the_core_filters_on(self):
        # core/splits.py and core/features.py filter on these strings. If this
        # set ever drifts, the split engine silently stops seeing NFL rows.
        emitted = set()
        for row in (
            play(rusher_player_id="R", rushing_yards=4.0),
            play(receiver_player_id="W", passer_player_id="Q", complete_pass=1,
                 receiving_yards=12.0),
            play(receiver_player_id="W", passer_player_id="Q", complete_pass=0),
            play(rusher_player_id="R", rushing_yards=1.0, rush_touchdown=1),
        ):
            emitted.update(what for _, what, _ in _attribution_for(row))
        assert emitted == {
            "Rush", "Reception", "Target", "Touchdown",
            "Completion", "Incompletion",
        }


class TestRushing:
    def test_a_run_credits_the_rusher_with_yards(self):
        rows = _attribution_for(play(rusher_player_id="R", rushing_yards=7.0))
        assert rows == [("R", "Rush", 7.0)]

    def test_a_rushing_touchdown_adds_a_touchdown_row_for_the_same_player(self):
        rows = _attribution_for(
            play(rusher_player_id="R", rushing_yards=2.0, rush_touchdown=1)
        )
        # splits.py counts a rush TD as `has_td and has_rush` on ONE player and
        # play, so both rows must carry the same id.
        assert rows == [("R", "Rush", 2.0), ("R", "Touchdown", None)]

    def test_a_sack_produces_no_rush_row(self):
        # THE COLLEGE ENGINE EXCLUDES SACKS FROM RUSHING DELIBERATELY. Here it
        # comes for free: this source leaves `rushing_yards` null on a sack.
        # Measured on 2025 -- 1,352 sacks, none with a rushing_yards value.
        rows = _attribution_for(
            play(play_type="pass", rusher_player_id="Q", rushing_yards=None,
                 yards_gained=-8)
        )
        assert rows == []


class TestReceiving:
    def test_a_completion_credits_receiver_and_passer_differently(self):
        rows = _attribution_for(play(
            receiver_player_id="W", passer_player_id="Q",
            complete_pass=1, receiving_yards=15.0,
        ))
        assert rows == [("W", "Reception", 15.0), ("Q", "Completion", None)]

    def test_target_is_emitted_only_on_an_incompletion(self):
        # splits.py reads targets as receptions + Target rows. A Target on a
        # completion would count that target twice.
        assert types_for(play(
            receiver_player_id="W", passer_player_id="Q", complete_pass=1,
            receiving_yards=9.0,
        )) == [("W", "Reception"), ("Q", "Completion")]

        assert types_for(play(
            receiver_player_id="W", passer_player_id="Q", complete_pass=0,
        )) == [("W", "Target"), ("Q", "Incompletion")]

    def test_a_passing_touchdown_marks_both_receiver_and_passer(self):
        rows = types_for(play(
            receiver_player_id="W", passer_player_id="Q",
            complete_pass=1, pass_touchdown=1,
        ))
        # The passer's Touchdown row mirrors CFBD on purpose: core/features.py
        # excludes it by requiring a Rush or Reception on the same play, and
        # that compensation has to have something to exclude.
        assert rows == [
            ("W", "Reception"), ("W", "Touchdown"),
            ("Q", "Completion"), ("Q", "Touchdown"),
        ]

    def test_an_incompletion_never_carries_a_touchdown(self):
        rows = types_for(play(
            receiver_player_id="W", passer_player_id="Q",
            complete_pass=0, pass_touchdown=1,
        ))
        assert ("W", "Touchdown") not in rows
        assert ("Q", "Touchdown") not in rows

    def test_a_sack_still_credits_the_passer_with_an_incompletion(self):
        # No receiver, so no Target -- but the QB's play still reaches the
        # `plays` denominator that splits.py counts per position.
        assert types_for(play(passer_player_id="Q", complete_pass=0)) == [
            ("Q", "Incompletion")
        ]


class TestTheRowFilter:
    def frame(self, rows):
        return pl.DataFrame(
            [{c: r.get(c) for c in SOURCE_COLUMNS} for r in rows],
            schema={c: pl.Utf8 for c in SOURCE_COLUMNS},
        )

    def test_plays_with_no_skill_id_are_dropped(self):
        kept = select_source_rows(self.frame([
            play(rusher_player_id="R"),
            play(play_type="kickoff"),
            play(play_type="punt"),
            play(receiver_player_id="W"),
            play(passer_player_id="Q"),
        ]))
        assert kept.height == 3

    def test_a_renamed_source_column_fails_loudly(self):
        # The whole reason SOURCE_COLUMNS is named rather than taking the frame
        # as it comes: a renamed upstream column must fail here, not arrive as
        # a column of nulls three steps later.
        frame = self.frame([play(rusher_player_id="R")]).drop("yardline_100")
        with pytest.raises(ValueError, match="missing expected column"):
            select_source_rows(frame)

    def test_losing_every_skill_column_is_refused(self):
        frame = self.frame([play()]).drop(list(SKILL_ID_COLUMNS))
        with pytest.raises(ValueError, match="carries none of"):
            select_source_rows(frame)


class TestTheCopyContract:
    def test_plays_columns_omit_cfbd_id(self):
        # An NFL play has no CFBD identifier; the column is nullable so this
        # list can leave it out rather than writing a null into a unique index.
        assert "cfbd_id" not in PLAY_COLUMNS
        assert "nflverse_play_id" in PLAY_COLUMNS

    def test_attribution_columns_match_the_college_loader(self):
        # Same table, written the same way. Drift here means one sport's rows
        # are shaped differently from the other's in a table with no sport
        # column to tell them apart.
        from worker.adapters.cfbd.ingest_stats import (
            PLAY_PLAYER_COLUMNS as CFBD_COLUMNS,
        )

        assert PLAY_PLAYER_COLUMNS == CFBD_COLUMNS
