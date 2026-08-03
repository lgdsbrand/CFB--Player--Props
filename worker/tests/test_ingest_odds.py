"""Tests for odds ingest resolution and reporting (Phase 5a).

Scope: the decisions, not the SQL. Matching a provider event onto one of our
games is where a wrong answer is invisible — the row lands, the board renders,
and a line sits against the wrong fixture. The write path was proved separately
against the real schema (110 rows, 24 players, 5 markets, 4 books, from a real
2025 historical slate).

No network, no database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from worker.core.name_match import TeamResolver
from worker.jobs.ingest_odds import (
    KICKOFF_TOLERANCE_HOURS,
    IngestReport,
    match_event_to_game,
)

KICK = datetime(2025, 10, 18, 23, 30, tzinfo=UTC)

TEAMS = [
    {"id": 1, "school": "Florida State", "mascot": "Seminoles", "alt_name": None},
    {"id": 2, "school": "Stanford", "mascot": "Cardinal", "alt_name": None},
    {"id": 3, "school": "Buffalo", "mascot": "Bulls", "alt_name": None},
    {"id": 4, "school": "UAlbany", "mascot": "Great Danes", "alt_name": None},
    {"id": 5, "school": "Albany State GA", "mascot": "Golden Rams", "alt_name": None},
    {"id": 6, "school": "Georgia", "mascot": "Bulldogs", "alt_name": None},
]

RESOLVER = TeamResolver(TEAMS)


class Event:
    def __init__(self, away: str, home: str, commence: datetime | None = KICK):
        self.event_id = "evt"
        self.away_team = away
        self.home_team = home
        self.commence_time = commence


def _game(id_: int, home: int, away: int, start: datetime, week: int = 8) -> dict:
    return {
        "id": id_, "season": 2025, "week": week, "start_date": start,
        "home_team_id": home, "away_team_id": away,
    }


class TestBothTeamsResolve:
    def test_matches_on_the_team_pair(self):
        games = [_game(100, 2, 1, KICK)]
        out = match_event_to_game(
            Event("Florida State Seminoles", "Stanford Cardinal"), games, RESOLVER
        )
        assert out is not None
        game, how = out
        assert game["id"] == 100
        assert how == "both-teams"

    def test_pair_matches_regardless_of_home_away_orientation(self):
        """The provider and CFBD can disagree about which side is home for a
        neutral-site game; the fixture is still the same fixture."""
        games = [_game(100, 1, 2, KICK)]
        out = match_event_to_game(
            Event("Florida State Seminoles", "Stanford Cardinal"), games, RESOLVER
        )
        assert out is not None and out[0]["id"] == 100

    def test_kickoff_drift_does_not_break_the_match(self):
        """TV windows move. The pair is the key; kickoff is only a tiebreak."""
        games = [_game(100, 2, 1, KICK + timedelta(hours=48))]
        out = match_event_to_game(
            Event("Florida State Seminoles", "Stanford Cardinal"), games, RESOLVER
        )
        assert out is not None and out[0]["id"] == 100

    def test_a_rematch_is_split_by_kickoff(self):
        """Same pair twice in one season — a conference title rematch."""
        games = [
            _game(100, 2, 1, KICK, week=8),
            _game(200, 2, 1, KICK + timedelta(days=49), week=15),
        ]
        out = match_event_to_game(
            Event("Florida State Seminoles", "Stanford Cardinal"), games, RESOLVER
        )
        assert out is not None and out[0]["id"] == 100

    def test_no_such_fixture_is_not_matched(self):
        games = [_game(100, 6, 3, KICK)]
        assert match_event_to_game(
            Event("Florida State Seminoles", "Stanford Cardinal"), games, RESOLVER
        ) is None


class TestOneTeamResolves:
    """The provider sends a bare school with no mascot; we hold two Albanys."""

    def test_a_unique_fixture_still_resolves(self):
        games = [_game(100, 3, 4, KICK)]
        out = match_event_to_game(Event("Albany", "Buffalo Bulls"), games, RESOLVER)
        assert out is not None
        game, how = out
        assert game["id"] == 100
        assert how == "one-team+kickoff"

    def test_two_fixtures_in_the_window_resolve_to_neither(self):
        """THE SAFETY CATCH. One-sided matching is only allowed to fire when
        the answer is unique — otherwise it is guessing which game a line
        belongs to, and both candidates render identically."""
        games = [
            _game(100, 3, 4, KICK),
            _game(101, 3, 6, KICK + timedelta(hours=2)),
        ]
        assert match_event_to_game(
            Event("Albany", "Buffalo Bulls"), games, RESOLVER
        ) is None

    def test_outside_the_kickoff_window_does_not_match(self):
        games = [_game(100, 3, 4, KICK + timedelta(hours=KICKOFF_TOLERANCE_HOURS + 2))]
        assert match_event_to_game(
            Event("Albany", "Buffalo Bulls"), games, RESOLVER
        ) is None

    def test_without_a_kickoff_there_is_nothing_to_disambiguate_with(self):
        games = [_game(100, 3, 4, KICK)]
        assert match_event_to_game(
            Event("Albany", "Buffalo Bulls", commence=None), games, RESOLVER
        ) is None

    def test_neither_team_resolving_is_not_matched(self):
        games = [_game(100, 3, 4, KICK)]
        assert match_event_to_game(
            Event("Albany", "Nowhere Tech"), games, RESOLVER
        ) is None


class TestReportUnits:
    def test_quotes_and_rows_are_reported_separately(self):
        """THE REGRESSION. Rows are quotes x books, so printing them as one
        ratio produced "110/37 written" — more written than seen."""
        report = IngestReport(quotes_seen=37, quotes_resolved=37, rows_written=110)
        text = report.render()
        assert "37/37 resolved" in text
        assert "110 book row(s) written" in text
        assert "110/37" not in text

    def test_unresolved_players_are_named_not_just_counted(self):
        report = IngestReport(quotes_seen=2, quotes_resolved=1)
        report.players_unresolved["Ghost Player"] += 1
        assert "Ghost Player" in report.render()

    def test_ambiguous_players_are_reported_apart_from_unresolved(self):
        """They call for different responses: a duplicate row to reconcile
        versus a name we have never seen."""
        report = IngestReport()
        report.players_ambiguous["Johntay Cook"] += 1
        text = report.render()
        assert "AMBIGUOUS" in text and "Johntay Cook" in text

    def test_unmatched_events_are_named(self):
        report = IngestReport(events_seen=1, events_matched=0)
        report.events_unmatched.append("Albany @ Buffalo Bulls")
        assert "Albany @ Buffalo Bulls" in report.render()
