"""Tests for the NFL reference ingest (N2).

Concentrated on the three things that would be wrong QUIETLY. A missing team or
a failed download announces itself; these do not:

  * **The kickoff timezone.** The schedule stores local Eastern wall-clock with
    no offset. Reading it as UTC puts every Sunday 13:00 kickoff four hours
    early, which looks plausible all September and then drifts by another hour
    at the November DST boundary. The odds backfill aims its snapshots at
    `kickoff - lead`, so an hour of drift buys the wrong market.

  * **Display names for the four shared-market franchises.** `location` reads
    "Buffalo" for 28 teams and "New York Jets" for the Jets, so it is not a
    city column -- it is a city column with four exceptions in it.

  * **Logo URLs.** CLAUDE.md §7 ships colour chips because real marks are
    trademarked, and the team-metadata file offers six logo fields right beside
    the two colours we do want.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from worker.adapters.nflverse.assets import TEAM_META_LOGO_COLUMNS
from worker.adapters.nflverse.ingest_reference import (
    POSTSEASON_TYPES,
    _team_rows,
    kickoff_utc,
)


class TestKickoffTimezone:
    """The schedule's clock is Eastern. Storing it as UTC is a silent 4-5h error."""

    def test_the_2026_opener_matches_the_odds_api(self):
        # THE CROSS-CHECK THAT SETTLES IT. nfldata says 2026-09-09 20:20 local;
        # The Odds API, read independently, reports the same kickoff as
        # 2026-09-10T00:20:00Z. Two sources, one instant.
        assert kickoff_utc("2026-09-09", "20:20") == datetime(
            2026, 9, 10, 0, 20, tzinfo=UTC
        )

    def test_a_september_sunday_afternoon_is_four_hours_ahead(self):
        # EDT: UTC-4.
        assert kickoff_utc("2026-09-13", "13:00") == datetime(
            2026, 9, 13, 17, 0, tzinfo=UTC
        )

    def test_a_december_sunday_afternoon_is_five_hours_ahead(self):
        # EST: UTC-5. THE POINT OF THE WHOLE TEST CLASS -- a fixed offset would
        # pass the September case and be an hour wrong from November onward,
        # which no September verification could catch.
        assert kickoff_utc("2026-12-13", "13:00") == datetime(
            2026, 12, 13, 18, 0, tzinfo=UTC
        )

    def test_a_missing_time_is_none_rather_than_midnight(self):
        # `games.start_time_tbd` exists for this. Guessing a kickoff is how the
        # odds backfill ended up asking about games that had already started.
        assert kickoff_utc("2026-09-13", None) is None
        assert kickoff_utc(None, "13:00") is None
        assert kickoff_utc("2026-09-13", "") is None

    def test_an_unparsable_time_is_none_rather_than_an_exception(self):
        assert kickoff_utc("2026-09-13", "kickoff") is None
        assert kickoff_utc("not-a-date", "13:00") is None


class TestSeasonTypes:
    def test_every_postseason_round_is_recognised(self):
        # A round mistaken for REG lands in the regular-season week numbering
        # and collides with a real week -- the college build shipped exactly
        # that bug, storing bowl games as week 1.
        assert {"WC", "DIV", "CON", "SB"} <= POSTSEASON_TYPES

    def test_the_regular_season_is_not_in_it(self):
        assert "REG" not in POSTSEASON_TYPES


def _meta(**overrides) -> pl.DataFrame:
    row = {
        "team_abbr": "NYJ",
        "team_name": "New York Jets",
        "team_nick": "Jets",
        "team_conf": "AFC",
        "team_division": "AFC East",
        "team_color": "#125740",
        "team_color2": "#000000",
        "team_logo_espn": "https://a.espncdn.com/i/teamlogos/nfl/500/nyj.png",
        "team_wordmark": "https://example.invalid/NYJ.png",
    }
    row.update(overrides)
    return pl.DataFrame([row])


def _season(**overrides) -> pl.DataFrame:
    row = {
        "season": 2026,
        "team": "NYJ",
        "full": "New York Jets",
        "location": "New York Jets",
        "short_location": "NY Jets",
        "nickname": "Jets",
    }
    row.update(overrides)
    return pl.DataFrame([row])


class TestTeamRows:
    def test_a_shared_market_team_does_not_double_its_nickname(self):
        # THE BUG THIS CAUGHT. `location` is "New York Jets", so school +
        # mascot would have read "New York Jets Jets".
        (row,) = _team_rows(_meta(), _season())

        assert row["school"] == "New York Jets"
        assert row["mascot"] == "Jets"

    def test_an_ordinary_team_gets_its_full_name_too(self):
        # Consistency is the point: one rule for all 32, not 28 plus four.
        (row,) = _team_rows(
            _meta(team_abbr="BUF", team_name="Buffalo Bills", team_nick="Bills"),
            _season(team="BUF", full="Buffalo Bills", location="Buffalo",
                    short_location="Buffalo", nickname="Bills"),
        )

        assert row["school"] == "Buffalo Bills"
        assert row["mascot"] == "Bills"

    def test_colours_come_through(self):
        (row,) = _team_rows(_meta(), _season())

        assert row["color"] == "#125740"
        assert row["alt_color"] == "#000000"

    def test_no_logo_url_reaches_the_row(self):
        # CLAUDE.md §7. Asserted on the VALUES, so adding a new logo column to
        # the source cannot quietly start populating one.
        (row,) = _team_rows(_meta(), _season())

        for value in row.values():
            assert "http" not in str(value or "")
        assert not (set(row) & TEAM_META_LOGO_COLUMNS)

    def test_a_legacy_franchise_code_is_dropped_not_inserted(self):
        # The metadata file carries 36 rows -- the 32 current franchises plus
        # codes like OAK and STL. Driving from the season file is what keeps
        # those out, and canonical_team() is the second line of defence.
        rows = _team_rows(
            _meta(team_abbr="OAK", team_name="Oakland Raiders"),
            _season(team="ZZZ", full="Nonexistent"),
        )

        assert rows == []

    def test_an_aliased_code_still_resolves(self):
        # nflverse says LA for the Rams; other sources say LAR.
        (row,) = _team_rows(
            _meta(team_abbr="LA", team_name="Los Angeles Rams", team_nick="Rams",
                  team_conf="NFC", team_division="NFC West"),
            _season(team="LAR", full="Los Angeles Rams",
                    location="Los Angeles Rams", nickname="Rams"),
        )

        assert row["abbr"] == "LA"
        assert row["school"] == "Los Angeles Rams"
