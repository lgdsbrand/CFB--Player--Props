"""FTN defensive charting (migration 0072).

Scope: the aggregation decisions, which are the ones a future edit could undo
without anything failing. The rank's orientation and the point-in-time cutoff
are asserted against real rows by `audit_data` instead, because a mock cannot
tell you that production got them right.

No network, no database.
"""

from __future__ import annotations

import polars as pl

from worker.adapters.nflverse.assets import (
    ASSETS,
    REQUIRE_SEASON_PRESENT,
    SEASON_IN_URL,
    asset_url,
)
from worker.adapters.nflverse.ingest_charting import (
    BLITZ_MIN,
    DROPBACK_MIN_RUSHERS,
    HEAVY_BOX_MIN,
    aggregate,
)
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.core.charting import MIN_BOX_PLAYS_TO_RATE, MIN_DROPBACKS_TO_RATE

GAME_KEY = "2026_01_NE_SEA"
#: (game key, play id) -> (defense_team_id, game_id, week)
INDEX = {
    (GAME_KEY, 1): (900, 77, 1),
    (GAME_KEY, 2): (900, 77, 1),
    (GAME_KEY, 3): (900, 77, 1),
    (GAME_KEY, 4): (901, 77, 1),
}


def frame(rows: list[dict]) -> pl.DataFrame:
    base = {
        "nflverse_game_id": GAME_KEY,
        "nflverse_play_id": 1,
        "week": 1,
        "n_blitzers": 0,
        "n_pass_rushers": 4,
        "n_defense_box": 6,
    }
    return pl.DataFrame([{**base, **r} for r in rows])


class TestTheAssetIsRegistered:
    def test_ftn_charting_has_a_url_template(self):
        assert "ftn_charting" in ASSETS
        assert asset_url("ftn_charting", 2026).endswith(
            "/ftn_charting/ftn_charting_2026.csv"
        )

    def test_one_file_is_one_season(self):
        assert "ftn_charting" in SEASON_IN_URL

    def test_the_season_must_be_present_in_what_comes_back(self):
        """The frozen-legacy-asset trap: a 200 carrying the wrong season."""
        assert "ftn_charting" in REQUIRE_SEASON_PRESENT


class TestTheTwoDenominators:
    """Blitz rate and box rate are charted on DIFFERENT plays.

    Measured on 2025: 46.5% of charted rows carry a pass rusher. Dividing
    blitzes by every charted play would report the league blitzing on 14% of
    snaps instead of 30%.
    """

    def test_a_run_is_not_a_dropback(self):
        counts = NflReferenceCounts()
        rows = aggregate(
            frame([{"n_pass_rushers": 0, "n_defense_box": 8}]), INDEX, counts
        )
        assert rows[0]["dropbacks"] == 0
        # ...but it still counts toward the BOX denominator, which is the whole
        # reason the two are stored separately.
        assert rows[0]["box_plays"] == 1
        assert rows[0]["heavy_box_plays"] == 1

    def test_a_dropback_is_any_play_with_a_pass_rusher(self):
        counts = NflReferenceCounts()
        rows = aggregate(
            frame([{"n_pass_rushers": DROPBACK_MIN_RUSHERS}]), INDEX, counts
        )
        assert rows[0]["dropbacks"] == 1

    def test_an_uncharted_box_does_not_inflate_the_box_denominator(self):
        # Special teams chart as 0 in the box. Counting them would drag every
        # heavy-box rate down by the share of the game that is kicking.
        counts = NflReferenceCounts()
        rows = aggregate(
            frame([{"n_pass_rushers": 0, "n_defense_box": 0}]), INDEX, counts
        )
        assert rows[0]["box_plays"] == 0


class TestWhatCountsAsABlitz:
    def test_n_blitzers_is_already_the_extra_rushers(self):
        """FTN counts defenders rushing BEYOND the standard four.

        So the test is `>= 1` and no arithmetic on `n_pass_rushers` is needed.
        Deriving it as `n_pass_rushers > 4` instead would disagree with the
        source on every play where a defense rushed three and dropped eight.
        """
        counts = NflReferenceCounts()
        rows = aggregate(
            frame([
                {"nflverse_play_id": 1, "n_blitzers": BLITZ_MIN, "n_pass_rushers": 5},
                {"nflverse_play_id": 2, "n_blitzers": 0, "n_pass_rushers": 4},
                # Three rushers, nobody extra: a drop-eight look, not a blitz.
                {"nflverse_play_id": 3, "n_blitzers": 0, "n_pass_rushers": 3},
            ]),
            INDEX, counts,
        )
        assert rows[0]["dropbacks"] == 3
        assert rows[0]["blitz_plays"] == 1

    def test_heavy_box_is_seven_or_more(self):
        # Six is the base look against most personnel, so seven is the first
        # count that means an extra man was committed to the run.
        counts = NflReferenceCounts()
        rows = aggregate(
            frame([
                {"nflverse_play_id": 1, "n_defense_box": HEAVY_BOX_MIN - 1},
                {"nflverse_play_id": 2, "n_defense_box": HEAVY_BOX_MIN},
            ]),
            INDEX, counts,
        )
        assert rows[0]["box_plays"] == 2
        assert rows[0]["heavy_box_plays"] == 1


class TestTheJoin:
    def test_an_unresolved_play_is_skipped_not_guessed(self):
        """`plays` does not store special teams; FTN charts them.

        About 27% of charted rows have no stored play, and that gap is ours and
        correct. What must never happen is a charted play being attributed to
        whichever defense was handy.
        """
        counts = NflReferenceCounts()
        rows = aggregate(frame([{"nflverse_play_id": 999}]), INDEX, counts)
        assert rows == []
        assert counts.skipped == {"charting: play not resolved": 1}

    def test_a_row_with_no_play_key_is_skipped(self):
        counts = NflReferenceCounts()
        rows = aggregate(frame([{"nflverse_play_id": None}]), INDEX, counts)
        assert rows == []
        assert counts.skipped == {"charting: no play key": 1}

    def test_each_defense_gets_its_own_row(self):
        counts = NflReferenceCounts()
        rows = aggregate(
            frame([
                {"nflverse_play_id": 1, "n_blitzers": 1},
                {"nflverse_play_id": 4, "n_blitzers": 0},
            ]),
            INDEX, counts,
        )
        by_defense = {r["defense_team_id"]: r for r in rows}
        assert set(by_defense) == {900, 901}
        assert by_defense[900]["blitz_plays"] == 1
        assert by_defense[901]["blitz_plays"] == 0

    def test_a_missing_column_fails_loudly_rather_than_aggregating_nothing(self):
        """An upstream rename would otherwise produce a clean run of zeroes."""
        counts = NflReferenceCounts()
        bad = frame([{}]).drop("n_blitzers")
        try:
            aggregate(bad, INDEX, counts)
        except ValueError as exc:
            assert "n_blitzers" in str(exc)
        else:
            raise AssertionError("a missing charting column must raise")


class TestTheRatingFloors:
    def test_a_single_game_clears_the_dropback_floor(self):
        """Week 2 is the first week this can say anything, and it must.

        FTN publishes two to three days after a week is played, so a week-2
        board reads week 1 alone -- about 35 dropbacks per defense. A floor
        above that would leave the column empty for the first month.
        """
        assert MIN_DROPBACKS_TO_RATE <= 35

    def test_the_box_floor_is_looser_because_its_denominator_is_bigger(self):
        # Box counts are charted on runs too, so one game supplies roughly twice
        # as many box plays as dropbacks.
        assert MIN_BOX_PLAYS_TO_RATE > MIN_DROPBACKS_TO_RATE


class TestABrokenJoinIsNotAnEmptySeason:
    def test_write_leaves_existing_rows_alone_when_handed_nothing(self):
        """`write` returning early on an empty list is deliberate.

        A season nflverse has not published yet must not wipe the season that is
        there. The fault case is caught one level up, in
        `run_nfl_charting_ingest`, which raises when the FILE had plays and none
        of them resolved — otherwise a broken play key reads as a clean run with
        last week's blitz rates still on the board.
        """
        from worker.adapters.nflverse.ingest_charting import write

        assert write(2026, []) == 0  # no database touched: it returns first
