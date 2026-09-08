"""N5a -- NFL snap counts.

Scope: the resolution decisions, not the SQL. A snap count that attaches to the
wrong player is invisible downstream -- it becomes a usage number that looks
entirely ordinary -- so the tests here are about who a row resolves to and who
it refuses to resolve to.

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
from worker.adapters.nflverse.ingest_reference import NflReferenceCounts
from worker.adapters.nflverse.ingest_snaps import SNAP_COLUMN, build_pairs


def frame(rows: list[dict]) -> pl.DataFrame:
    base = {
        "game_id": "2025_01_ARI_NO",
        "pfr_player_id": "BankKe01",
        "position": "WR",
        SNAP_COLUMN: 50,
    }
    return pl.DataFrame([{**base, **r} for r in rows])


GAMES = {"2025_01_ARI_NO": 900, "2025_02_NO_SF": 901}
PLAYERS = {"BankKe01": 10, "SmitJo01": 11}


class TestTheAssetIsRegistered:
    def test_snap_counts_has_a_url_template(self):
        assert "snap_counts" in ASSETS
        url = asset_url("snap_counts", 2025)
        assert url.endswith("/snap_counts/snap_counts_2025.csv")

    def test_the_season_is_in_the_url_so_one_file_is_one_season(self):
        assert "snap_counts" in SEASON_IN_URL

    def test_the_season_must_be_present_in_what_comes_back(self):
        """The frozen-legacy-asset trap: a 200 carrying the wrong season."""
        assert "snap_counts" in REQUIRE_SEASON_PRESENT


class TestResolution:
    def test_a_resolvable_row_becomes_a_pair(self):
        counts = NflReferenceCounts()
        pairs = build_pairs(frame([{}]), GAMES, PLAYERS, counts)
        assert pairs == [(10, 900, 50)]
        assert not counts.skipped

    def test_an_unknown_player_is_skipped_not_guessed(self):
        counts = NflReferenceCounts()
        pairs = build_pairs(
            frame([{"pfr_player_id": "NobdyXx99"}]), GAMES, PLAYERS, counts
        )
        assert pairs == []
        assert counts.skipped == {"snaps: player not resolved": 1}

    def test_an_unknown_game_is_skipped_not_guessed(self):
        counts = NflReferenceCounts()
        pairs = build_pairs(
            frame([{"game_id": "2025_18_XX_YY"}]), GAMES, PLAYERS, counts
        )
        assert pairs == []
        assert counts.skipped == {"snaps: game not resolved": 1}

    def test_a_blank_snap_count_is_absence_not_zero(self):
        counts = NflReferenceCounts()
        pairs = build_pairs(frame([{SNAP_COLUMN: None}]), GAMES, PLAYERS, counts)
        assert pairs == []
        assert counts.skipped == {"snaps: blank offense_snaps": 1}

    def test_zero_snaps_is_a_real_measurement_and_is_kept(self):
        """Distinct from blank: dressed, took no offensive snap."""
        counts = NflReferenceCounts()
        pairs = build_pairs(frame([{SNAP_COLUMN: 0}]), GAMES, PLAYERS, counts)
        assert pairs == [(10, 900, 0)]

    def test_two_rows_for_one_player_and_game_are_summed(self):
        """Snaps are additive; letting one win would understate usage.

        The floor rejects players below a usage threshold, so the one direction
        this must not be wrong in is downwards.
        """
        counts = NflReferenceCounts()
        pairs = build_pairs(
            frame([{SNAP_COLUMN: 30}, {SNAP_COLUMN: 25}]), GAMES, PLAYERS, counts
        )
        assert pairs == [(10, 900, 55)]

    def test_the_same_player_in_two_games_stays_two_pairs(self):
        counts = NflReferenceCounts()
        pairs = build_pairs(
            frame([
                {"game_id": "2025_01_ARI_NO", SNAP_COLUMN: 30},
                {"game_id": "2025_02_NO_SF", SNAP_COLUMN: 40},
            ]),
            GAMES, PLAYERS, counts,
        )
        assert sorted(pairs) == [(10, 900, 30), (10, 901, 40)]


class TestAmbiguousPfrIdsAreExcluded:
    """Migration 0053: `pfr_id` is indexed, NOT unique, and this is why.

    Measured over the 2023-2026 rosters, `YounBy01` covers two different
    players. Resolving it to whichever row came back first would attach one
    player's snaps to another and never say so.
    """

    def test_load_pfr_index_drops_an_id_covering_two_players(self, monkeypatch):
        from worker.adapters.nflverse import ingest_snaps

        monkeypatch.setattr(
            ingest_snaps, "fetch_all",
            lambda *a, **k: [
                {"id": 1, "pfr_id": "YounBy01"},
                {"id": 2, "pfr_id": "YounBy01"},
                {"id": 3, "pfr_id": "BankKe01"},
            ],
        )
        counts = NflReferenceCounts()
        index = ingest_snaps.load_pfr_index(counts)

        assert index == {"BankKe01": 3}
        assert counts.skipped == {"snaps: ambiguous pfr_id": 1}

    def test_the_same_player_listed_twice_is_not_ambiguous(self, monkeypatch):
        """One player on two roster rows is duplication, not a collision."""
        from worker.adapters.nflverse import ingest_snaps

        monkeypatch.setattr(
            ingest_snaps, "fetch_all",
            lambda *a, **k: [
                {"id": 7, "pfr_id": "BankKe01"},
                {"id": 7, "pfr_id": "BankKe01"},
            ],
        )
        counts = NflReferenceCounts()
        assert ingest_snaps.load_pfr_index(counts) == {"BankKe01": 7}
        assert not counts.skipped


class TestSnapsOnly:
    """`--snaps-only` loads snaps over box scores already stored.

    The flag exists because the box score upsert is an unconditional DO UPDATE:
    re-running it purely to reach the snap pass rewrites every row it touches
    and costs a dead tuple apiece for no change. Its hazard is the mirror of
    that saving -- skipping the load means nothing guarantees the rows are
    there, and a snap pass with no rows to match reports a clean zero.
    """

    def test_skip_snaps_and_snaps_only_cannot_both_be_asked_for(self, capsys):
        """They are opposites; taking both would silently mean one of them."""
        import pytest

        from worker.jobs import nfl_ingest_stats

        with pytest.raises(SystemExit):
            nfl_ingest_stats.main(["--seasons", "2024", "--skip-snaps", "--snaps-only"])
        assert "not allowed with" in capsys.readouterr().err

    def test_a_season_with_no_box_scores_is_refused(self, monkeypatch):
        """Not a clean zero -- that reads as "nflverse has no snaps for 2024"."""
        import pytest

        from worker.jobs import nfl_ingest_stats

        monkeypatch.setattr(
            nfl_ingest_stats, "fetch_one", lambda *a, **k: {"n": 0}
        )
        with pytest.raises(ValueError, match="no NFL box scores stored for 2024"):
            nfl_ingest_stats._require_box_scores(2024)

    def test_a_season_with_box_scores_passes(self, monkeypatch):
        from worker.jobs import nfl_ingest_stats

        monkeypatch.setattr(
            nfl_ingest_stats, "fetch_one", lambda *a, **k: {"n": 14_252}
        )
        nfl_ingest_stats._require_box_scores(2024)
