"""First-half and first-quarter game lines (capture_game_periods).

No network, no database: the parser against a payload in the provider's shape,
and `run_periods` with a fake adapter and stubbed reads, so what is asked about,
skipped and written can be pinned.
"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from worker.adapters.odds.base import OddsEvent
from worker.adapters.odds.theoddsapi import PERIOD_MARKETS, parse_game_odds
from worker.core.name_match import TeamResolver
from worker.jobs import capture_game_periods
from worker.jobs import ingest_game_odds as job

KICK = datetime(2026, 10, 10, 19, 30, tzinfo=UTC)
RESOLVER = TeamResolver([
    {"id": 1, "school": "Florida State", "mascot": "Seminoles", "alt_name": None},
    {"id": 2, "school": "Stanford", "mascot": "Cardinal", "alt_name": None},
])
HOME, AWAY = "Stanford Cardinal", "Florida State Seminoles"


def _payload() -> dict:
    def market(key, outcomes):
        return {"key": key, "last_update": "2026-10-10T12:00:00Z", "outcomes": outcomes}

    return {
        "id": "evt1", "sport_key": "americanfootball_ncaaf",
        "commence_time": "2026-10-10T19:30:00Z", "home_team": HOME, "away_team": AWAY,
        "bookmakers": [{
            "key": "draftkings", "title": "DraftKings",
            "markets": [
                market("spreads_h1", [
                    {"name": HOME, "price": -110, "point": -3.5},
                    {"name": AWAY, "price": -110, "point": 3.5},
                ]),
                market("totals_q1", [
                    {"name": "Over", "price": -115, "point": 10.5},
                    {"name": "Under", "price": -105, "point": 10.5},
                ]),
                market("h2h_q1", [
                    {"name": HOME, "price": -150}, {"name": AWAY, "price": 125},
                ]),
                market("alternate_spreads_h1", []),
            ],
        }],
    }


def test_period_keys_cover_three_markets_in_two_periods():
    assert set(PERIOD_MARKETS) == {
        "h2h_h1", "spreads_h1", "totals_h1", "h2h_q1", "spreads_q1", "totals_q1",
    }


def test_period_markets_parse_to_their_base_market_and_period():
    (event,) = parse_game_odds([_payload()])
    got = {(m.period, m.market) for m in event.markets}
    assert got == {("h1", "spreads"), ("q1", "totals"), ("q1", "h2h")}


def test_full_game_markets_still_parse_as_full():
    payload = _payload()
    payload["bookmakers"][0]["markets"] = [{
        "key": "totals", "outcomes": [
            {"name": "Over", "price": -110, "point": 52.5},
            {"name": "Under", "price": -110, "point": 52.5},
        ],
    }]
    (event,) = parse_game_odds([payload])
    assert [(m.period, m.market) for m in event.markets] == [("full", "totals")]


class _Quota:
    def summary(self) -> str:
        return "n/a"


class _FakeAdapter:
    quota = _Quota()

    def __init__(self) -> None:
        self.asked: list[str] = []

    def list_events(self):
        return [OddsEvent("evt1", "americanfootball_ncaaf", KICK, HOME, AWAY, {})]

    def fetch_period_odds(self, event_id):
        self.asked.append(event_id)
        return parse_game_odds([_payload()])


def _run(monkeypatch, *, now, held=()):
    adapter = _FakeAdapter()
    written: list = []
    monkeypatch.setattr(job, "connect", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(job, "load_teams", lambda conn, sport: RESOLVER)
    monkeypatch.setattr(job, "load_games", lambda conn, season, week, sport: [{
        "id": 100, "season": 2026, "week": 7, "start_date": KICK,
        "home_team_id": 2, "away_team_id": 1,
    }])
    monkeypatch.setattr(job, "games_with_period_odds", lambda conn, ids: set(held))
    monkeypatch.setattr(
        job, "write_changed", lambda conn, oriented, now, report: written.extend(oriented)
    )
    # The bet slip's current offers (migration 0089) are a second write in the
    # same run; nothing here is about them, and the fake connection has no cursor.
    monkeypatch.setattr(job, "write_game_offers", lambda *args, **kwargs: None)
    report = job.run_periods(season=2026, adapter=adapter, now=now)
    return adapter, report, written


def test_a_game_with_no_period_lines_gets_its_opening_capture(monkeypatch):
    adapter, report, written = _run(monkeypatch, now=KICK - timedelta(days=1))
    assert adapter.asked == ["evt1"]
    assert {(r.period, r.market) for _, r in written} == {
        ("h1", "spreads"), ("q1", "totals"), ("q1", "h2h"),
    }


def test_rows_are_oriented_to_our_home_team(monkeypatch):
    _, _, written = _run(monkeypatch, now=KICK - timedelta(days=1))
    spread = next(r for _, r in written if r.market == "spreads")
    assert spread.line == -3.5 and spread.home_price == -110


def test_a_held_game_is_not_rebought_before_its_close(monkeypatch):
    adapter, report, written = _run(
        monkeypatch, now=KICK - timedelta(minutes=61), held={100}
    )
    assert adapter.asked == [] and written == []
    assert report.events_already_captured == 1


def test_a_held_game_is_bought_again_for_its_close(monkeypatch):
    adapter, _, _ = _run(monkeypatch, now=KICK - timedelta(minutes=60), held={100})
    assert adapter.asked == ["evt1"]


def test_a_started_game_is_never_asked_about(monkeypatch):
    adapter, report, _ = _run(monkeypatch, now=KICK, held={100})
    assert adapter.asked == [] and report.events_started == 1


def test_the_cron_runs_hourly_with_a_window_equal_to_its_period():
    text = (Path(__file__).resolve().parents[2] / "render.yaml").read_text(encoding="utf-8")
    block = next(
        b for b in text.split("- type: cron") if "worker.jobs.capture_game_periods" in b
    )
    schedule = re.search(r'schedule:\s*"([^"]+)"', block).group(1)
    minute, hour, *rest = schedule.split()
    assert minute.isdigit() and hour == "*" and rest == ["*", "*", "*"], schedule
    assert capture_game_periods.WINDOW_MINUTES == 60
