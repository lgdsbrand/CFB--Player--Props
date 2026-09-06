"""Tests for the nflverse adapter skeleton (N1).

The NFL half of the sport seam (CLAUDE.md §3). These concentrate on the two
things that can go wrong QUIETLY, because everything else about reading a CSV
off a CDN fails loudly on its own:

  * **A release asset that answers 200 with the wrong season in it.** nflverse
    keeps a frozen legacy weekly-stats file beside the current one and it still
    returns 134,470 well-formed rows that stop at 2024. Nothing about that
    response is malformed. A 2026 model built on it would train on 2024 and
    look entirely fine.
  * **A team abbreviation that does not resolve.** `teams.nfl_abbr` is unique
    (migration 0049), so an unrecognised code either collides with a real
    franchise or invents a 33rd. The failure is not an error; it is a team
    silently missing from a slate.
"""

from __future__ import annotations

import polars as pl
import pytest

from worker.adapters.nflverse.assets import asset_url
from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.mapping import (
    CURRENT_TEAMS,
    MODELLED_POSITIONS,
    SPORT,
    PositionMapper,
    canonical_team,
    int_or_none,
    text_or_none,
)


class TestTheSeamIsDeclared:
    def test_sport_is_set_explicitly(self):
        # The column defaults to 'cfb'. Saying 'nfl' here means never relying on
        # teams_cfb_requires_cfbd_id firing to catch a mislabelled row.
        assert SPORT == "nfl"

    def test_only_the_four_markets_positions_are_modelled(self):
        assert MODELLED_POSITIONS == {"QB", "RB", "WR", "TE"}


class TestAssetUrls:
    def test_a_seasonal_asset_interpolates_the_season(self):
        url = asset_url("weekly_stats", 2025)

        assert url.endswith("/stats_player/stats_player_week_2025.csv")

    def test_a_seasonless_asset_ignores_the_season(self):
        assert asset_url("players").endswith("/players/players.csv")

    def test_an_unknown_asset_raises_rather_than_building_a_404(self):
        # A typo should fail here, not as an empty ingest three steps later.
        with pytest.raises(KeyError):
            asset_url("touchdowns_by_vibes", 2025)

    def test_a_seasonal_asset_without_a_season_raises(self):
        with pytest.raises(ValueError):
            asset_url("weekly_stats")


class TestTheStaleAssetGuard:
    """THE ONE THAT MATTERS. A 200 carrying the wrong season is not data."""

    @staticmethod
    def _frame(*seasons: int) -> pl.DataFrame:
        return pl.DataFrame({"season": list(seasons), "player_id": ["x"] * len(seasons)})

    def test_a_frozen_legacy_asset_is_refused(self):
        # The real shape: asked for 2026, handed a file that stops at 2024.
        with pytest.raises(NflverseError) as exc:
            NflverseClient._assert_season_present(
                "weekly_stats", 2026, self._frame(2023, 2024), "http://example/x.csv"
            )

        assert "no rows for season 2026" in str(exc.value)
        assert "newest present: 2024" in str(exc.value)

    def test_the_right_season_passes(self):
        NflverseClient._assert_season_present(
            "weekly_stats", 2025, self._frame(2025), "http://example/x.csv"
        )

    def test_a_seasonless_asset_is_not_checked(self):
        # players.csv has no season dimension; asserting one would reject it.
        NflverseClient._assert_season_present(
            "players", None, self._frame(), "http://example/x.csv"
        )

    def test_the_guard_is_wired_into_fetch_not_merely_present(self, monkeypatch):
        # A guard nothing calls is decoration. This proves the path.
        client = NflverseClient()
        payload = b"season,player_id\n2024,x\n"
        monkeypatch.setattr(
            NflverseClient, "_download", lambda self, url, dest: payload
        )
        monkeypatch.setattr(
            "worker.adapters.nflverse.client.cache_enabled", lambda: False
        )

        with pytest.raises(NflverseError):
            client.fetch("weekly_stats", 2026, max_age=None)

    def test_fetch_will_not_guess_a_freshness_rule(self):
        # max_age is keyword-only with no default on purpose: None is wrong for
        # the live season and a number is wrong for the 26 completed ones.
        client = NflverseClient()

        with pytest.raises(TypeError):
            client.fetch("players")  # type: ignore[call-arg]


class TestPositions:
    def test_a_fullback_is_a_running_back(self):
        assert PositionMapper().group_for("FB") == "RB"

    def test_case_and_padding_do_not_matter(self):
        assert PositionMapper().group_for("  wr ") == "WR"

    def test_an_unknown_position_is_bucketed_and_counted(self):
        mapper = PositionMapper()

        assert mapper.group_for("ATHLETE") == "OTHER"
        assert mapper.unmapped["ATHLETE"] == 1

    def test_a_missing_position_is_counted_separately(self):
        # Distinguishable in the log from a position we simply do not know.
        mapper = PositionMapper()

        assert mapper.group_for(None) == "OTHER"
        assert mapper.unmapped["<missing>"] == 1


class TestTeams:
    def test_the_rams_resolve_from_either_spelling(self):
        # nflverse says LA; The Odds API and ESPN say LAR. Both are the Rams.
        assert canonical_team("LAR") == "LA"
        assert canonical_team("LA") == "LA"

    def test_relocated_franchises_resolve_to_their_current_code(self):
        assert canonical_team("OAK") == "LV"
        assert canonical_team("SD") == "LAC"
        assert canonical_team("STL") == "LA"

    def test_an_unrecognised_code_is_none_not_itself(self):
        # Returning the input would create a 33rd franchise on a unique column.
        assert canonical_team("XXX") is None
        assert canonical_team("") is None
        assert canonical_team(None) is None

    def test_there_are_exactly_thirty_two_franchises(self):
        assert len(CURRENT_TEAMS) == 32

    def test_every_alias_targets_a_real_franchise(self):
        # An alias pointing at a code that is not current would resolve to None
        # and drop the team, which is the silent failure this table exists to
        # prevent.
        from worker.adapters.nflverse.mapping import TEAM_ALIASES

        assert set(TEAM_ALIASES.values()) <= CURRENT_TEAMS


class TestScalars:
    def test_an_integer_written_through_a_float_column_parses(self):
        # nflverse does this routinely; int('17.0') would raise on real data.
        assert int_or_none("17.0") == 17

    def test_blank_and_na_are_absence(self):
        assert int_or_none("") is None
        assert int_or_none("NA") is None
        assert text_or_none("   ") is None

    def test_junk_is_absence_rather_than_an_exception(self):
        assert int_or_none("abc") is None
