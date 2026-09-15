"""`backfill_odds --markets`: buying a named set, such as the first quarter.

No network, no database. Added 2026-09-15 to buy NFL week 1's first-quarter
lines, which were never captured live.
"""

from __future__ import annotations

from datetime import UTC, datetime

from worker.adapters.odds.base import QuotaSnapshot
from worker.jobs import backfill_odds as job

Q1 = ["q1_pass_yards", "q1_rec_yards", "q1_rush_yards"]
KICKOFF = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)


def _run(monkeypatch, **kw) -> tuple[list[list[str]], list]:
    asked: list[list[str]] = []
    bought_with: list = []

    class Adapter:
        name = "recording"
        quota = QuotaSnapshot(remaining=20_000, used=0, last_cost=1)

        def historical_events(self, iso_timestamp):
            return {"data": []}

        def historical_props_raw(self, event_id, iso_timestamp, market_keys=None):
            asked.append(list(market_keys or []))
            return {"data": {"id": event_id, "bookmakers": []}}

    def already_bought(conn, season, week, adapter, markets=None):
        bought_with.append(markets)
        return set()

    monkeypatch.setattr(job, "load_teams", lambda conn, sport="cfb": object())
    monkeypatch.setattr(
        job, "load_games",
        lambda conn, season, week, sport="cfb": [{
            "id": 1, "season": 2026, "week": 1, "start_date": KICKOFF,
            "home_team_id": 1, "away_team_id": 2,
        }],
    )
    monkeypatch.setattr(job, "already_bought", already_bought)
    monkeypatch.setattr(
        job,
        "match_event_to_game_for",
        lambda g, events, teams: job.OddsEvent(
            event_id="evt-1", sport_key="americanfootball_nfl",
            commence_time=KICKOFF, home_team="H", away_team="A",
        ),
    )

    job.backfill_week(
        conn=None,
        adapter=Adapter(),
        season=2026,
        week=1,
        lead_minutes=60,
        budget=job.CreditBudget(max_credits=10_000, min_remaining=0),
        report=job.BackfillReport(),
        dry_run=False,
        sport="nfl",
        **kw,
    )
    return asked, bought_with


def test_named_markets_are_all_that_is_bought(monkeypatch):
    asked, _ = _run(monkeypatch, markets=Q1)
    assert asked == [Q1]


def test_already_bought_is_asked_about_the_same_markets(monkeypatch):
    """A game with full-game closing lines has not bought its first quarter.
    Keyed on the game alone, the second purchase would skip every game."""
    _, bought_with = _run(monkeypatch, markets=Q1)
    assert bought_with == [Q1]


def test_without_markets_it_is_the_full_game_set_as_before(monkeypatch):
    asked, bought_with = _run(monkeypatch)
    assert asked == [sorted(job.OUR_KEY_TO_PROVIDER)]
    assert bought_with == [sorted(job.OUR_KEY_TO_PROVIDER)]


def test_cli_refuses_first_quarter_for_college_before_settings(monkeypatch):
    def settings_must_not_load():
        raise AssertionError("settings were read before the markets were checked")

    monkeypatch.setattr(job, "get_settings", settings_must_not_load)
    argv = ["--season", "2026", "--weeks", "1"]
    assert job.main([*argv, "--sport", "cfb", "--markets", "q1_rec_yards"]) == 2
    assert job.main([
        *argv, "--sport", "nfl", "--markets", "q1_rec_yards",
        "--exclude-markets", "anytime_td",
    ]) == 2
