"""Tests for the historical coverage probe's event selection (Phase 5a).

These exist because the probe drew a WRONG CONCLUSION and committed it. It took
the first event in the historical snapshot, which happened to be Eastern
Michigan @ Miami (OH) — a game that had kicked two hours before the snapshot
timestamp — got an empty response because books pull player props at kickoff,
and wrote "historical player props: FAIL" into docs/odds-coverage-probe.md. The
plan carries them: a game still upcoming at the same snapshot returned four
books and five markets.

That is the same shape as every other silent defect in this project — the call
succeeded, returned nothing, and nothing raised. So the properties below are
stated against behaviour, not implementation, and each was replayed against the
old `events[0]` rule to confirm it bites.

All offline. No credits are spent by this file.
"""

from __future__ import annotations

from typing import Any

from worker.adapters.odds.base import QuotaSnapshot
from worker.adapters.odds.theoddsapi import ParseDiagnostics
from worker.jobs.probe_odds import (
    HISTORICAL_EVENTS_PROBED,
    _commences_after,
    _merge_diagnostics,
    probe_historical,
)

SNAPSHOT = "2025-10-18T18:00:00Z"

# A payload shaped like the real one, carrying two of our mapped markets.
PROPS_PAYLOAD: dict[str, Any] = {
    "data": {
        "id": "evt_props",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "outcomes": [
                            {"name": "Over", "description": "Drew Allar",
                             "price": -110, "point": 245.5},
                            {"name": "Under", "description": "Drew Allar",
                             "price": -110, "point": 245.5},
                        ],
                    },
                    {
                        "key": "player_anytime_td",
                        "outcomes": [
                            {"name": "Yes", "description": "Nick Singleton",
                             "price": 120},
                        ],
                    },
                ],
            }
        ],
    }
}

EMPTY_PAYLOAD: dict[str, Any] = {"data": {"id": "evt_empty", "bookmakers": []}}


def _event(event_id: str, commence: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "commence_time": commence,
        "home_team": "Home",
        "away_team": "Away",
    }


class FakeAdapter:
    """Records which events were asked about, and what each one cost.

    Bills like the provider does: per market RETURNED, so an event with no
    props costs nothing. That is what makes probing several events affordable,
    and a test that got the billing backwards would justify the wrong bound.
    """

    def __init__(self, events: list[dict], payloads: dict[str, dict]) -> None:
        self._events = events
        self._payloads = payloads
        self.asked: list[str] = []
        self.quota = QuotaSnapshot(remaining=16000, used=4000, last_cost=0)

    def historical_events(self, iso_timestamp: str) -> dict[str, Any]:
        return {"data": self._events}

    def historical_props_raw(
        self, event_id: str, iso_timestamp: str
    ) -> dict[str, Any]:
        self.asked.append(event_id)
        payload = self._payloads.get(event_id, EMPTY_PAYLOAD)
        markets = sum(
            len(b.get("markets") or [])
            for b in (payload.get("data") or {}).get("bookmakers") or []
        )
        cost = markets * 10
        self.quota = QuotaSnapshot(
            remaining=self.quota.remaining - cost,
            used=self.quota.used + cost,
            last_cost=cost,
        )
        return payload


def _props_finding(findings: list) -> Any:
    return next(f for f in findings if f.name == "historical player props")


class TestEventSelection:
    def test_skips_games_that_already_kicked(self):
        """THE REGRESSION. A kicked game cannot carry props, so asking it
        measures the schedule, not the plan."""
        adapter = FakeAdapter(
            events=[
                _event("kicked", "2025-10-18T16:02:20Z"),   # 2h before snapshot
                _event("evt_props", "2025-10-18T19:00:00Z"),
            ],
            payloads={"evt_props": PROPS_PAYLOAD},
        )
        findings = probe_historical(adapter, SNAPSHOT)

        assert "kicked" not in adapter.asked, (
            "probed a game that had already started — books pull props at "
            "kickoff, so its empty response says nothing about entitlement"
        )
        assert _props_finding(findings).ok is True

    def test_an_empty_slate_of_upcoming_games_is_not_a_verdict(self):
        """Every game already kicked: report why, never 'props unavailable'."""
        adapter = FakeAdapter(
            events=[_event("kicked", "2025-10-18T16:00:00Z")],
            payloads={},
        )
        findings = probe_historical(adapter, SNAPSHOT)

        assert adapter.asked == []
        assert not any(f.name == "historical player props" for f in findings)
        assert any("already kicked" in n for n in findings[0].notes)

    def test_uneven_coverage_does_not_read_as_no_coverage(self):
        """Books skip low-profile games. Keep asking past the empty ones."""
        adapter = FakeAdapter(
            events=[
                _event("g5_a", "2025-10-18T19:00:00Z"),
                _event("g5_b", "2025-10-18T19:30:00Z"),
                _event("evt_props", "2025-10-18T23:30:00Z"),
            ],
            payloads={"evt_props": PROPS_PAYLOAD},
        )
        findings = probe_historical(adapter, SNAPSHOT)

        assert adapter.asked == ["g5_a", "g5_b", "evt_props"]
        assert _props_finding(findings).ok is True

    def test_stops_paying_once_entitlement_is_proven(self):
        """Empty events are free; a second priced one is not."""
        adapter = FakeAdapter(
            events=[
                _event("evt_props", "2025-10-18T19:00:00Z"),
                _event("evt_props2", "2025-10-18T20:00:00Z"),
            ],
            payloads={"evt_props": PROPS_PAYLOAD, "evt_props2": PROPS_PAYLOAD},
        )
        probe_historical(adapter, SNAPSHOT)

        assert adapter.asked == ["evt_props"], (
            "kept spending after the question was answered"
        )

    def test_probe_is_bounded(self):
        adapter = FakeAdapter(
            events=[
                _event(f"e{i}", "2025-10-18T23:00:00Z")
                for i in range(HISTORICAL_EVENTS_PROBED + 5)
            ],
            payloads={},
        )
        probe_historical(adapter, SNAPSHOT)
        assert len(adapter.asked) == HISTORICAL_EVENTS_PROBED


class TestVerdicts:
    def test_no_props_anywhere_is_unresolved_not_failure(self):
        """A 200 carrying nothing is not an entitlement answer.

        Reporting it as FAIL is precisely how the memo came to state that a
        plan lacked historical props while it was serving them.
        """
        adapter = FakeAdapter(
            events=[_event("a", "2025-10-18T19:00:00Z")],
            payloads={},
        )
        finding = _props_finding(probe_historical(adapter, SNAPSHOT))

        assert finding.ok is None, "an empty response was reported as a verdict"
        assert any("UNRESOLVED" in n for n in finding.notes)

    def test_found_props_report_measured_cost_and_backtestability(self):
        adapter = FakeAdapter(
            events=[_event("evt_props", "2025-10-18T19:00:00Z")],
            payloads={"evt_props": PROPS_PAYLOAD},
        )
        finding = _props_finding(probe_historical(adapter, SNAPSHOT))

        assert finding.ok is True
        assert any("EDGE IS BACKTESTABLE" in n for n in finding.notes)
        # Two markets returned, billed at 10 credits each.
        assert any("20 credits" in n for n in finding.notes)


class TestCommencesAfter:
    def test_upcoming_is_askable(self):
        assert _commences_after(_event("x", "2025-10-18T19:00:00Z"), SNAPSHOT)

    def test_kicked_is_not(self):
        assert not _commences_after(_event("x", "2025-10-18T16:00:00Z"), SNAPSHOT)

    def test_exactly_at_kickoff_is_not_askable(self):
        assert not _commences_after(_event("x", SNAPSHOT), SNAPSHOT)

    def test_unreadable_timestamp_is_kept(self):
        """Losing a probe target costs a credit; silently filtering the whole
        slate on a parse quirk looks exactly like 'no historical coverage'."""
        assert _commences_after({"id": "x", "commence_time": "nonsense"}, SNAPSHOT)
        assert _commences_after({"id": "x"}, SNAPSHOT)


class TestMergeDiagnostics:
    def test_markets_union_across_events(self):
        into = ParseDiagnostics(
            bookmakers={"draftkings"}, markets_seen={"player_pass_yds"},
            quotes_two_way=3,
        )
        other = ParseDiagnostics(
            bookmakers={"fanduel"}, markets_seen={"player_rush_yds"},
            quotes_two_way=2, quotes_one_sided=1,
        )
        _merge_diagnostics(into, other)

        assert into.bookmakers == {"draftkings", "fanduel"}
        assert into.markets_seen == {"player_pass_yds", "player_rush_yds"}
        assert into.quotes_two_way == 5
        assert into.quotes_one_sided == 1
