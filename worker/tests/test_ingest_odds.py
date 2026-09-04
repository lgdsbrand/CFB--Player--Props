"""Tests for odds ingest resolution and reporting (Phase 5a).

Scope: the decisions, not the SQL. Matching a provider event onto one of our
games is where a wrong answer is invisible — the row lands, the board renders,
and a line sits against the wrong fixture. The write path was proved separately
against the real schema (110 rows, 24 players, 5 markets, 4 books, from a real
2025 historical slate).

No network, no database.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from worker.config import ConfigError, Settings, env_names_containing
from worker.core.name_match import TeamResolver
from worker.db import _deploy_marker
from worker.jobs import ingest_odds
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


def _settings(*, paid: str | None, free: str | None) -> Settings:
    return Settings(
        database_url="postgresql:///test",
        cfbd_api_key=None,
        supabase_url=None,
        supabase_service_role_key=None,
        odds_api_key=paid,
        odds_api_key_free=free,
    )


class TestOddsKeySelection:
    """Which of the client's two allowances a run bills.

    Untested until 2026-08-24, which is the wrong shape of gap: the paid pool
    is SHARED with the client's other three models and has already hit zero
    mid-month once. A wrong answer here does not raise — it just quietly
    spends someone else's budget.
    """

    def test_defaults_to_the_paid_key(self):
        assert _settings(paid="paid", free="free").odds_key() == "paid"

    def test_free_is_opt_in(self):
        settings = _settings(paid="paid", free="free")
        assert settings.odds_key(prefer_free=True) == "free"

    def test_free_falls_back_to_paid_when_unset(self):
        """`--free` degrades to "run it anyway" rather than refusing.

        Deliberate, but it means the flag alone never proves which pool got
        spent — which is why ingest_odds logs the pool by name and warns on
        exactly this fallback.
        """
        settings = _settings(paid="paid", free=None)
        assert settings.odds_key(prefer_free=True) == "paid"

    def test_no_key_at_all_is_none_not_an_error(self):
        """Loading config must not fail without an odds key; the null adapter
        is a real selectable state and the board degrades to model leans."""
        assert _settings(paid=None, free=None).odds_key() is None
        assert _settings(paid=None, free=None).odds_key(prefer_free=True) is None


def _settings_free(**kw) -> Settings:
    base = dict(
        database_url="postgresql:///test",
        cfbd_api_key=None,
        supabase_url=None,
        supabase_service_role_key=None,
        odds_api_key="paid",
        odds_api_key_free="free",
    )
    base.update(kw)
    return Settings(**base)


class TestPreferFreeFlagEnv:
    """ODDS_PREFER_FREE, the deployment-time half of `--free`.

    It exists because render.yaml cannot express a one-shot: the odds cron's
    command is fixed in the blueprint, and any dow-restricted schedule reads as
    a 168h period to test_monitor's guards, which would force ingest_odds's
    18h max_age past 193h and blind the monitor for eight days. So the
    opening-weekend fallback is flipped from the Render dashboard instead.
    """

    def test_defaults_off(self):
        assert _settings_free().odds_prefer_free is False
        assert _settings_free().odds_key() == "paid"

    def test_set_selects_the_free_key(self):
        settings = _settings_free(odds_prefer_free=True)
        assert settings.odds_key(prefer_free=settings.odds_prefer_free) == "free"


class TestFlagParsing:
    """A typo here bills the pool three other models share, and does it
    silently. Ambiguity is a ConfigError, never a falsy default."""

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " True "])
    def test_truthy(self, raw, monkeypatch):
        from worker.config import _flag
        monkeypatch.setenv("ODDS_PREFER_FREE", raw)
        assert _flag("ODDS_PREFER_FREE") is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
    def test_falsy(self, raw, monkeypatch):
        from worker.config import _flag
        monkeypatch.setenv("ODDS_PREFER_FREE", raw)
        assert _flag("ODDS_PREFER_FREE") is False

    def test_unset_is_false(self, monkeypatch):
        from worker.config import _flag
        monkeypatch.delenv("ODDS_PREFER_FREE", raising=False)
        assert _flag("ODDS_PREFER_FREE") is False

    @pytest.mark.parametrize("raw", ["ture", "maybe", "2", "y"])
    def test_ambiguous_raises_rather_than_billing_the_paid_pool(
        self, raw, monkeypatch
    ):
        from worker.config import _flag
        monkeypatch.setenv("ODDS_PREFER_FREE", raw)
        with pytest.raises(ConfigError):
            _flag("ODDS_PREFER_FREE")


def _clear_odds_env(monkeypatch) -> None:
    """Remove every ODDS-ish variable the ambient environment may hold.

    `get_settings()` loads `.env`, so a developer's real ODDS_API_KEY can be in
    `os.environ` by the time these run. Without this the "absent" case passes
    or fails depending on whose machine it is.
    """
    for name in list(os.environ):
        if "ODDS" in name.upper():
            monkeypatch.delenv(name, raising=False)


class TestEnvNearMissDetector:
    """`env_names_containing` — names only, and that is the security property.

    The failed-run error text is copied into the monitor's alert body and sent
    to a webhook, so a value leaking through here leaves the system.
    """

    def test_finds_a_misnamed_variable(self, monkeypatch):
        _clear_odds_env(monkeypatch)
        monkeypatch.setenv("ODDS_API_VARIABLE", "super-secret-value")
        assert env_names_containing("ODDS") == ["ODDS_API_VARIABLE"]

    def test_never_returns_a_value(self, monkeypatch):
        _clear_odds_env(monkeypatch)
        monkeypatch.setenv("ODDS_API_VARIABLE", "super-secret-value")
        assert "super-secret-value" not in " ".join(env_names_containing("ODDS"))

    def test_a_lowercase_fragment_still_matches_and_results_are_sorted(
        self, monkeypatch
    ):
        """Case-insensitivity is asserted on the FRAGMENT, not the name.

        Windows upper-cases `os.environ` keys and Linux does not, so a test
        that set `ZZ_odds_trailing` and expected it back verbatim passed on
        Render and failed here. The names are echoed exactly as the platform
        holds them; only the match is case-folded.
        """
        _clear_odds_env(monkeypatch)
        monkeypatch.setenv("ZZ_ODDS_TRAILING", "x")
        monkeypatch.setenv("AA_ODDS_LEADING", "x")
        assert env_names_containing("odds") == ["AA_ODDS_LEADING", "ZZ_ODDS_TRAILING"]

    def test_empty_when_nothing_matches(self, monkeypatch):
        _clear_odds_env(monkeypatch)
        assert env_names_containing("ODDS") == []


class TestMissingKeyDiagnosis:
    """The message must distinguish ABSENT from MISNAMED.

    Nine consecutive scheduled runs on 2026-09-03 reported only "ODDS_API_KEY
    is not set" while the key sat in Render as `ODDS_API_VARIABLE`. The bare
    message is equally true in both cases, which is exactly why it cost days.
    """

    def _raise(self, monkeypatch):
        monkeypatch.setattr(
            ingest_odds,
            "get_settings",
            lambda: _settings_free(odds_api_key=None, odds_api_key_free=None),
        )
        with pytest.raises(ConfigError) as excinfo:
            ingest_odds.run(
                season=2026,
                week=1,
                adapter_name="theoddsapi",
                dry_run=True,
                event_limit=None,
            )
        return str(excinfo.value)

    def test_names_the_misnamed_variable(self, monkeypatch):
        _clear_odds_env(monkeypatch)
        monkeypatch.setenv("ODDS_API_VARIABLE", "super-secret-value")
        message = self._raise(monkeypatch)
        assert "ODDS_API_VARIABLE" in message
        assert "wrong name" in message

    def test_does_not_leak_the_value(self, monkeypatch):
        _clear_odds_env(monkeypatch)
        monkeypatch.setenv("ODDS_API_VARIABLE", "super-secret-value")
        assert "super-secret-value" not in self._raise(monkeypatch)

    def test_absent_says_absent_and_points_at_the_deploy(self, monkeypatch):
        _clear_odds_env(monkeypatch)
        message = self._raise(monkeypatch)
        assert "absent" in message
        assert "redeploys" in message

    def test_an_empty_value_is_not_reported_as_misnamed(self, monkeypatch):
        """Present-but-blank is a third case, and the one that reads worst.

        `_optional` returns None for absent OR empty, so a blank value reaches
        this code looking identical to a missing one — while the dashboard
        shows the variable present and correctly named. Calling that "wrong
        name" sends the reader hunting for something that is not there.
        """
        _clear_odds_env(monkeypatch)
        monkeypatch.setenv("ODDS_API_KEY", "")
        message = self._raise(monkeypatch)
        assert "must be empty or blank" in message
        assert "wrong name" not in message

    def test_still_says_the_original_thing(self, monkeypatch):
        """The first sentence is what the runbook quotes; keep it intact."""
        _clear_odds_env(monkeypatch)
        assert self._raise(monkeypatch).startswith("ODDS_API_KEY is not set")


class TestDeployMarker:
    """`pipeline_runs.metadata` should say which deploy produced the row."""

    def test_empty_off_render(self, monkeypatch):
        for name in ("RENDER_SERVICE_NAME", "RENDER_GIT_COMMIT", "RENDER_INSTANCE_ID"):
            monkeypatch.delenv(name, raising=False)
        assert _deploy_marker() == {}

    def test_records_the_deploy_on_render(self, monkeypatch):
        monkeypatch.setenv("RENDER_SERVICE_NAME", "cfb-props-odds-refresh")
        monkeypatch.setenv("RENDER_GIT_COMMIT", "dd174dd")
        monkeypatch.delenv("RENDER_INSTANCE_ID", raising=False)
        assert _deploy_marker() == {
            "render_service_name": "cfb-props-odds-refresh",
            "render_git_commit": "dd174dd",
        }

    def test_a_jobs_own_metadata_wins(self, monkeypatch):
        """The marker must never mask the more specific fact a job records."""
        monkeypatch.setenv("RENDER_SERVICE_NAME", "cfb-props-odds-refresh")
        merged = {**_deploy_marker(), **{"season": 2026, "render_service_name": "override"}}
        assert merged["render_service_name"] == "override"
        assert merged["season"] == 2026
