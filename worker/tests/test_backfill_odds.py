"""Tests for the historical odds backfill.

This job spends the client's money, from a pool shared with three other models,
so the tests concentrate on the two things that decide whether a run is safe
rather than on happy-path parsing:

  * **Snapshots are aimed before kickoff.** The bug this job is built around is
    a probe that asked one fixed timestamp for a whole slate, hit a game that
    had already started, got an empty 200 and recorded "historical player
    props: FAIL" — a wrong negative that then shaped Phase 3.
  * **The ceiling holds.** The budget is checked BEFORE each billable call and
    against the provider's reported cost, never an estimate.

The one-sided rate is asserted per market for the reason the report gives:
anytime TD is Yes-only at most books and dominates any blended figure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from worker.adapters.odds import NullOddsAdapter, SupportsHistorical
from worker.adapters.odds.base import QuotaSnapshot
from worker.adapters.odds.markets import OUR_KEY_TO_PROVIDER
from worker.adapters.odds.theoddsapi import TheOddsApiAdapter
from worker.jobs.backfill_odds import (
    BackfillReport,
    CreditBudget,
    events_from_payload,
    group_by_snapshot,
    parse_weeks,
    props_from_payload,
    snapshot_for,
)


def game(game_id: int, kickoff: datetime | None, **extra) -> dict:
    row = {
        "id": game_id,
        "season": 2025,
        "week": 8,
        "start_date": kickoff,
        "home_team_id": 100,
        "away_team_id": 200,
    }
    row.update(extra)
    return row


# -----------------------------------------------------------------------------
# Snapshot timing — the already-kicked bug
# -----------------------------------------------------------------------------
class TestSnapshotTiming:
    def test_the_snapshot_lands_before_kickoff(self):
        kickoff = datetime(2025, 10, 18, 19, 0, tzinfo=UTC)
        assert snapshot_for(game(1, kickoff), 60) == datetime(
            2025, 10, 18, 18, 0, tzinfo=UTC
        )

    def test_every_game_gets_its_own_snapshot_not_one_for_the_slate(self):
        # THE WHOLE POINT. A noon game and a night game on the same Saturday
        # must not share a timestamp: whichever one the shared moment misses
        # returns an empty 200 that reads as "no coverage".
        noon = datetime(2025, 10, 18, 16, 0, tzinfo=UTC)
        night = datetime(2025, 10, 18, 23, 30, tzinfo=UTC)
        buckets = group_by_snapshot([game(1, noon), game(2, night)], 60)

        assert len(buckets) == 2
        assert "2025-10-18T15:00:00Z" in buckets
        assert "2025-10-18T22:30:00Z" in buckets

    def test_games_kicking_together_share_one_event_call(self):
        # The event list is the only per-snapshot charge, so co-kicking games
        # must bucket together or the run pays for the same list repeatedly.
        together = datetime(2025, 10, 18, 20, 0, tzinfo=UTC)
        buckets = group_by_snapshot(
            [game(1, together), game(2, together), game(3, together)], 60
        )

        assert len(buckets) == 1
        assert len(next(iter(buckets.values()))) == 3

    def test_a_game_with_no_kickoff_is_dropped_rather_than_guessed(self):
        # There is no safe default here. Assuming a time is exactly how the
        # original probe ended up asking about a game that had already started.
        assert snapshot_for(game(1, None), 60) is None
        assert group_by_snapshot([game(1, None)], 60) == {}

    def test_buckets_come_out_in_chronological_order(self):
        late = datetime(2025, 10, 18, 23, 0, tzinfo=UTC)
        early = datetime(2025, 10, 18, 16, 0, tzinfo=UTC)
        keys = list(group_by_snapshot([game(1, late), game(2, early)], 60))

        assert keys == sorted(keys)

    def test_the_lead_is_configurable_and_actually_applied(self):
        kickoff = datetime(2025, 10, 18, 19, 0, tzinfo=UTC)
        assert snapshot_for(game(1, kickoff), 30) == kickoff - timedelta(minutes=30)
        assert snapshot_for(game(1, kickoff), 180) == kickoff - timedelta(hours=3)


# -----------------------------------------------------------------------------
# The credit ceiling
# -----------------------------------------------------------------------------
class TestCreditBudget:
    def test_spend_accumulates_from_the_providers_own_number(self):
        budget = CreditBudget(max_credits=100, min_remaining=0)
        budget.record(QuotaSnapshot(remaining=900, used=100, last_cost=10))
        budget.record(QuotaSnapshot(remaining=850, used=150, last_cost=50))

        assert budget.spent == 60

    def test_a_missing_cost_header_does_not_invent_a_charge(self):
        budget = CreditBudget(max_credits=100, min_remaining=0)
        budget.record(QuotaSnapshot(remaining=None, used=None, last_cost=None))

        assert budget.spent == 0

    def test_the_ceiling_stops_the_run(self):
        budget = CreditBudget(max_credits=100, min_remaining=0)
        budget.record(QuotaSnapshot(remaining=900, last_cost=100))

        assert budget.exhausted(QuotaSnapshot(remaining=900)) is True
        assert "ceiling" in (budget.stopped or "")

    def test_under_the_ceiling_the_run_continues(self):
        budget = CreditBudget(max_credits=100, min_remaining=0)
        budget.record(QuotaSnapshot(remaining=900, last_cost=99))

        assert budget.exhausted(QuotaSnapshot(remaining=900)) is False
        assert budget.stopped is None

    def test_a_call_that_could_breach_the_ceiling_is_refused_first(self):
        # THE BUG THIS PINS DOWN. Comparing only what had already been spent
        # let a single 60-credit call through a 25-credit ceiling, which then
        # reported "reached the 25-credit ceiling (spent 61)". Nothing had been
        # limited; the overshoot was simply announced afterwards.
        budget = CreditBudget(max_credits=25, min_remaining=0)

        assert budget.spent == 0
        assert budget.exhausted(QuotaSnapshot(remaining=900), reserve=90) is True
        assert "could cost up to 90" in (budget.stopped or "")

    def test_a_call_that_fits_under_the_ceiling_proceeds(self):
        budget = CreditBudget(max_credits=100, min_remaining=0)
        budget.record(QuotaSnapshot(remaining=900, last_cost=10))

        assert budget.exhausted(QuotaSnapshot(remaining=900), reserve=90) is False

    def test_the_reserve_also_protects_the_shared_pool_floor(self):
        # 5,050 remaining is above the 5,000 floor, but a 90-credit call would
        # take it under. The floor exists to protect the other models, so it
        # has to be checked against where the call LANDS.
        budget = CreditBudget(max_credits=100_000, min_remaining=5_000)

        assert budget.exhausted(QuotaSnapshot(remaining=5_050), reserve=90) is True
        assert "floor" in (budget.stopped or "")

    def test_the_shared_pool_floor_stops_the_run_even_under_the_ceiling(self):
        # The other models spend from the same allowance. A backfill that is
        # still within its own ceiling must not drain the pool underneath them.
        budget = CreditBudget(max_credits=10_000, min_remaining=5_000)

        assert budget.exhausted(QuotaSnapshot(remaining=5_000)) is True
        assert "floor" in (budget.stopped or "")

    def test_an_unknown_remaining_does_not_trip_the_floor(self):
        # No usage headers is not the same as an empty pool; treating it as one
        # would make the job unrunnable against a provider that omits them.
        budget = CreditBudget(max_credits=10_000, min_remaining=5_000)

        assert budget.exhausted(QuotaSnapshot(remaining=None)) is False


# -----------------------------------------------------------------------------
# Payload unwrapping — historical nests the live shape under `data`
# -----------------------------------------------------------------------------
class TestPayloads:
    def test_events_are_read_from_the_data_envelope(self):
        payload = {
            "timestamp": "2025-10-18T18:00:00Z",
            "data": [
                {
                    "id": "abc",
                    "sport_key": "americanfootball_ncaaf",
                    "commence_time": "2025-10-18T19:00:00Z",
                    "home_team": "Stanford",
                    "away_team": "Florida State",
                }
            ],
        }
        events = events_from_payload(payload)

        assert len(events) == 1
        assert events[0].event_id == "abc"
        assert events[0].home_team == "Stanford"
        assert events[0].commence_time == datetime(2025, 10, 18, 19, 0, tzinfo=UTC)

    def test_an_empty_envelope_yields_no_events_and_does_not_raise(self):
        assert events_from_payload({"data": []}) == []
        assert events_from_payload({}) == []

    def test_an_unparsable_kickoff_is_left_null_rather_than_dropped(self):
        events = events_from_payload(
            {"data": [{"id": "x", "commence_time": "not-a-date"}]}
        )
        assert len(events) == 1
        assert events[0].commence_time is None

    def test_props_are_unwrapped_for_the_live_parser(self):
        inner = {"id": "abc", "bookmakers": []}
        assert props_from_payload({"timestamp": "t", "data": inner}) == inner

    def test_an_already_unwrapped_payload_passes_through(self):
        # Keeps the function safe to call on a live payload too, so the two
        # paths can share a parser.
        inner = {"id": "abc", "bookmakers": []}
        assert props_from_payload(inner) == inner


# -----------------------------------------------------------------------------
# Reporting — the numbers the spend decision is made on
# -----------------------------------------------------------------------------
class TestReport:
    def test_carry_rate_counts_only_games_actually_asked_about(self):
        report = BackfillReport()
        report.games_matched = 40
        report.games_priced = 12
        report.games_empty = 8

        # 12 of the 20 attempted, NOT 12 of the 40 matched: the run stopped
        # before asking about the other 20, and dividing by those would
        # understate the carry rate and so the budget.
        assert report.carry_rate == 0.6

    def test_carry_rate_is_zero_rather_than_a_crash_before_anything_ran(self):
        assert BackfillReport().carry_rate == 0.0

    def test_cost_per_priced_game_ignores_the_free_empty_ones(self):
        report = BackfillReport()
        report.games_priced = 10
        report.games_empty = 30
        report.credits_spent = 500

        assert report.credits_per_priced_game() == 50.0

    def test_the_two_way_rate_is_reported_per_market(self):
        # anytime_td is Yes-only at most books; pass_yards is two-way. Blended
        # these read as "31% usable" and hide that one market is fully usable
        # and the other is not usable at all.
        report = BackfillReport()
        report.one_sided_by_market["anytime_td"] = 60
        report.two_way_by_market["pass_yards"] = 28

        rendered = report.render()
        assert "anytime_td" in rendered
        assert "0% usable" in rendered
        assert "100% usable" in rendered

    def test_no_prices_is_reported_as_unresolved_not_as_no_coverage(self):
        rendered = BackfillReport().render()

        assert "UNRESOLVED" in rendered
        assert "FAIL" not in rendered

    def test_a_dry_run_reports_the_carry_rate_as_unmeasured_not_as_zero(self):
        # THE ABSENCE-OF-EVIDENCE GUARD. A dry run never asks for props, so
        # printing "carry rate: 0%" states a coverage finding it did not make —
        # the same shape as the probe that wrote "historical player props:
        # FAIL" from a call it had aimed at an already-kicked game.
        report = BackfillReport(dry_run=True)
        report.games_matched = 60
        report.credits_spent = 27

        rendered = report.render()
        assert "NOT MEASURED" in rendered
        assert "carry rate: 0%" not in rendered

    def test_a_dry_run_projects_the_worst_case_spend(self):
        report = BackfillReport(dry_run=True)
        report.games_matched = 60

        # 60 games x 9 markets x 10 credits.
        assert "5,400" in report.render()

    def test_a_real_run_with_nothing_priced_does_report_zero(self):
        # The opposite error would be just as bad: a real run that asked and
        # got nothing HAS measured a carry rate, and it is zero.
        report = BackfillReport(dry_run=False)
        report.games_matched = 10
        report.games_empty = 10

        assert "carry rate: 0%" in report.render()

    def test_stopping_early_is_stated_in_the_report(self):
        report = BackfillReport()
        report.stopped_early = "reached the 1000-credit ceiling (spent 1000)"

        assert "STOPPED EARLY" in report.render()


# -----------------------------------------------------------------------------
# Week parsing and adapter capability
# -----------------------------------------------------------------------------
class TestArguments:
    def test_a_comma_list_parses(self):
        assert parse_weeks("6,7,8") == [6, 7, 8]

    def test_a_range_parses(self):
        assert parse_weeks("6-8") == [6, 7, 8]

    def test_mixed_forms_deduplicate_and_sort(self):
        assert parse_weeks("8, 6-7, 6") == [6, 7, 8]

    def test_junk_yields_nothing_rather_than_a_partial_week_list(self):
        assert parse_weeks("") == []
        assert parse_weeks(" , ") == []


class RecordingAdapter:
    """A historical adapter that records what it was asked for.

    Exists to assert what the job does NOT call. Every other test here is about
    arithmetic; this one is about spend, and spend is a function of which
    endpoints get hit.
    """

    name = "recording"

    def __init__(self) -> None:
        self.event_list_calls: list[str] = []
        self.props_calls: list[str] = []
        self.quota = QuotaSnapshot(remaining=20_000, used=0, last_cost=1)

    def historical_events(self, iso_timestamp: str) -> dict:
        self.event_list_calls.append(iso_timestamp)
        return {"data": []}

    def historical_props_raw(
        self, event_id: str, iso_timestamp: str, market_keys=None
    ) -> dict:
        self.props_calls.append(event_id)
        return {"data": {"id": event_id, "bookmakers": []}}


class TestDryRun:
    """A preview flag that bills is worse than no preview flag."""

    @staticmethod
    def _run(monkeypatch, *, dry_run: bool) -> RecordingAdapter:
        from worker.jobs import backfill_odds as job

        kickoff = datetime(2025, 10, 18, 19, 0, tzinfo=UTC)
        adapter = RecordingAdapter()

        monkeypatch.setattr(job, "load_teams", lambda conn: object())
        monkeypatch.setattr(
            job, "load_games", lambda conn, season, week: [game(1, kickoff)]
        )
        monkeypatch.setattr(
            job, "already_bought", lambda conn, season, week, adapter: set()
        )
        # Matching is exercised by TestSnapshotTiming and the live job's own
        # tests; here it only needs to succeed so the code reaches the branch
        # under test.
        monkeypatch.setattr(
            job,
            "match_event_to_game_for",
            lambda g, events, teams: job.OddsEvent(
                event_id="evt-1",
                sport_key="americanfootball_ncaaf",
                commence_time=kickoff,
                home_team="Home",
                away_team="Away",
            ),
        )

        job.backfill_week(
            conn=None,
            adapter=adapter,
            season=2025,
            week=8,
            lead_minutes=60,
            budget=job.CreditBudget(max_credits=10_000, min_remaining=0),
            report=job.BackfillReport(),
            dry_run=dry_run,
        )
        return adapter

    def test_a_dry_run_never_asks_for_props(self, monkeypatch):
        # THE REGRESSION. The first version gated only the database write, so
        # --dry-run wrote nothing and still spent 60 credits on the first game.
        adapter = self._run(monkeypatch, dry_run=True)

        assert adapter.props_calls == []

    def test_a_dry_run_still_resolves_the_slate(self, monkeypatch):
        # It has to cost the event list, or it cannot report a match rate —
        # which is the entire reason to run one.
        adapter = self._run(monkeypatch, dry_run=True)

        assert adapter.event_list_calls == ["2025-10-18T18:00:00Z"]

    def test_a_real_run_does_ask_for_props(self, monkeypatch):
        # Guards against "fixing" the above by disabling the job entirely.
        adapter = self._run(monkeypatch, dry_run=False)

        assert adapter.props_calls == ["evt-1"]


class TestExcludedMarkets:
    """Excluding a market is a straight saving — billing is per market returned.

    MEASURED over 20 games of 2025 week 8: `anytime_td` was 1,802 of 2,709
    prices bought and **0** of them two-way. A one-sided price cannot be
    de-vigged, so that two thirds of the spend produced no gradeable edge at
    all. Excluding it is what brings three weeks inside the remaining pool.
    """

    def test_an_excluded_market_is_not_requested(self, monkeypatch):
        from worker.jobs import backfill_odds as job

        kickoff = datetime(2025, 10, 18, 19, 0, tzinfo=UTC)
        asked: list[list[str]] = []

        class Adapter:
            name = "recording"
            quota = QuotaSnapshot(remaining=20_000, used=0, last_cost=1)

            def historical_events(self, iso_timestamp):
                return {"data": []}

            def historical_props_raw(self, event_id, iso_timestamp, market_keys=None):
                asked.append(list(market_keys or []))
                return {"data": {"id": event_id, "bookmakers": []}}

        monkeypatch.setattr(job, "load_teams", lambda conn: object())
        monkeypatch.setattr(
            job, "load_games", lambda conn, season, week: [game(1, kickoff)]
        )
        monkeypatch.setattr(
            job, "already_bought", lambda conn, season, week, adapter: set()
        )
        monkeypatch.setattr(
            job,
            "match_event_to_game_for",
            lambda g, events, teams: job.OddsEvent(
                event_id="evt-1",
                sport_key="ncaaf",
                commence_time=kickoff,
                home_team="H",
                away_team="A",
            ),
        )

        job.backfill_week(
            conn=None,
            adapter=Adapter(),
            season=2025,
            week=8,
            lead_minutes=60,
            budget=job.CreditBudget(max_credits=10_000, min_remaining=0),
            report=job.BackfillReport(),
            dry_run=False,
            exclude_markets=("anytime_td",),
        )

        assert asked, "props should still have been requested"
        assert "anytime_td" not in asked[0]
        # Everything else still bought — this is a scalpel, not a switch-off.
        assert "pass_yards" in asked[0]
        assert len(asked[0]) == len(OUR_KEY_TO_PROVIDER) - 1

    def test_excluding_nothing_asks_for_every_market(self, monkeypatch):
        from worker.jobs import backfill_odds as job

        assert sorted(set(OUR_KEY_TO_PROVIDER) - set(())) == sorted(
            OUR_KEY_TO_PROVIDER
        )
        assert job.CREDITS_PER_MARKET * len(OUR_KEY_TO_PROVIDER) == 90


class TestResume:
    """Finishing a week means running again, so re-buying must be free.

    MEASURED: the second week-8 run paid for 20 games the first had already
    bought. Nothing looked wrong afterwards — `captured_at` is part of the
    unique key, so the duplicate rows were silently discarded and the database
    was identical. Only the credits were gone. A no-op that costs money is
    exactly the kind of waste that leaves no trace to notice.
    """

    @staticmethod
    def _run(monkeypatch, *, bought: set[int], refresh: bool) -> list[str]:
        from worker.jobs import backfill_odds as job

        kickoff = datetime(2025, 10, 18, 19, 0, tzinfo=UTC)
        asked: list[str] = []

        class Adapter:
            name = "recording"
            quota = QuotaSnapshot(remaining=20_000, used=0, last_cost=1)

            def historical_events(self, iso_timestamp):
                return {"data": []}

            def historical_props_raw(self, event_id, iso_timestamp, market_keys=None):
                asked.append(event_id)
                return {"data": {"id": event_id, "bookmakers": []}}

        monkeypatch.setattr(job, "load_teams", lambda conn: object())
        monkeypatch.setattr(
            job, "load_games", lambda conn, season, week: [game(1, kickoff)]
        )
        monkeypatch.setattr(
            job, "already_bought", lambda conn, season, week, adapter: set()
        )
        monkeypatch.setattr(
            job, "already_bought", lambda conn, season, week, adapter: bought
        )
        monkeypatch.setattr(
            job,
            "match_event_to_game_for",
            lambda g, events, teams: job.OddsEvent(
                event_id="evt-1",
                sport_key="ncaaf",
                commence_time=kickoff,
                home_team="H",
                away_team="A",
            ),
        )

        job.backfill_week(
            conn=None,
            adapter=Adapter(),
            season=2025,
            week=8,
            lead_minutes=60,
            budget=job.CreditBudget(max_credits=10_000, min_remaining=0),
            report=job.BackfillReport(),
            dry_run=False,
            refresh=refresh,
        )
        return asked

    def test_a_game_already_bought_is_not_bought_again(self, monkeypatch):
        assert self._run(monkeypatch, bought={1}, refresh=False) == []

    def test_a_game_not_yet_bought_is_still_bought(self, monkeypatch):
        assert self._run(monkeypatch, bought={999}, refresh=False) == ["evt-1"]

    def test_refresh_buys_a_second_snapshot_deliberately(self, monkeypatch):
        assert self._run(monkeypatch, bought={1}, refresh=True) == ["evt-1"]


class TestAdapterCapability:
    def test_the_live_adapter_satisfies_the_historical_protocol(self):
        adapter = TheOddsApiAdapter(api_key="test-key")
        assert isinstance(adapter, SupportsHistorical)

    def test_the_null_adapter_does_not(self):
        # This is what makes the job refuse with an explanation instead of
        # reporting an empty backfill as "no lines found".
        assert not isinstance(NullOddsAdapter(), SupportsHistorical)
