"""Measure what The Odds API actually serves for NCAAF player props.

Phase 3a deliverable. This job exists because three unknowns block decisions
downstream, and none of them can be answered from documentation:

  1. **Does this plan cover NCAAF player props at all?** (CLAUDE.md §9.1)
  2. **What does a weekly refresh cost?** Billing is per market per region per
     event, so the real number is `games x markets` credits — but the only
     trustworthy source for a call's cost is the usage header the provider
     returns, not a formula derived from the docs.
  3. **Are HISTORICAL prop odds reachable?** This is the one that shapes Phase 3.
     Calibration — "when the model says 60%, does it hit 60%?" — needs no odds
     whatsoever. EDGE needs a de-vigged book price. If historical props are not
     on this plan, the calibration report evaluates the model honestly and edge
     validation waits for live 2026 weeks to accumulate. Better to know now than
     to discover it while writing the report.

Phase 2 is the reason this is a measurement job rather than an assumption: CFBD's
`/plays/stats` silently truncated at 2,000 rows and its athlete ids were strings
on some endpoints and ints on others. Both failed without raising. Probe first.

**Quota discipline.** Defaults to ONE event. The whole run costs a handful of
credits. Widen deliberately with --event-limit.

    python -m worker.jobs.probe_odds
    python -m worker.jobs.probe_odds --historical-date 2025-11-08T18:00:00Z
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worker.adapters.odds import OddsAdapterError, OddsPlanError, TheOddsApiAdapter
from worker.adapters.odds.markets import (
    NCAAF_SPORT_KEY,
    OUR_KEY_TO_PROVIDER,
)
from worker.adapters.odds.theoddsapi import ParseDiagnostics, parse_event_odds
from worker.config import ConfigError, get_settings
from worker.db import pipeline_run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "probe_odds"

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO_ROOT / "docs" / "odds-coverage-probe.md"

# A Saturday inside the 2025 regular season. Historical props, if available at
# all, will be densest on a game day during the season — probing the offseason
# would return an empty slate and be indistinguishable from "not entitled".
DEFAULT_HISTORICAL_DATE = "2025-10-18T18:00:00Z"

# Rough FBS slate size, for turning a measured per-event cost into a weekly one.
GAMES_PER_WEEK = 60

# How close to kickoff a game must be before an empty prop response means
# anything. College books post props late — often Thursday or Friday for a
# Saturday game (CLAUDE.md §7) — so "no props" a month out is the schedule
# talking, not the plan.
LATE_LINE_DAYS = 7.0

# The provider's free allowance. Used to infer which tier a key belongs to from
# its reported allowance, rather than trusting which env var it arrived in.
FREE_PLAN_CREDITS = 500


@dataclass
class Finding:
    name: str
    ok: bool | None  # None = informational, neither pass nor fail
    detail: str
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        mark = "INFO" if self.ok is None else ("PASS" if self.ok else "FAIL")
        lines = [f"  [{mark}] {self.name}: {self.detail}"]
        lines.extend(f"         - {n}" for n in self.notes)
        return "\n".join(lines)


def _describe_coverage(diagnostics: ParseDiagnostics) -> list[str]:
    """Turn parse diagnostics into human-readable coverage notes."""
    notes: list[str] = []

    mapped_seen = {
        m for m in diagnostics.markets_seen if m not in diagnostics.markets_unmapped
    }
    wanted = set(OUR_KEY_TO_PROVIDER.values())
    missing = sorted(wanted - mapped_seen)

    notes.append(
        f"books returning data: {len(diagnostics.bookmakers)} "
        f"({', '.join(sorted(diagnostics.bookmakers)) or 'none'})"
    )
    notes.append(
        f"markets served: {len(mapped_seen)}/{len(wanted)} "
        f"({', '.join(sorted(mapped_seen)) or 'none'})"
    )
    if missing:
        notes.append(f"markets NOT served: {', '.join(missing)}")
    if diagnostics.markets_unmapped:
        notes.append(
            "unmapped markets returned (add to markets.py if useful): "
            + ", ".join(sorted(diagnostics.markets_unmapped))
        )

    two_way = diagnostics.quotes_two_way
    one_sided = diagnostics.quotes_one_sided
    total = two_way + one_sided
    if total:
        pct = 100.0 * one_sided / total
        notes.append(
            f"prices: {two_way} two-way, {one_sided} one-sided ({pct:.0f}% one-sided)"
        )
        notes.append(
            "one-sided prices CANNOT be de-vigged and yield no edge — "
            "this percentage is the ceiling on edge coverage"
        )
    if diagnostics.outcomes_unparsed:
        notes.append(
            f"WARNING: {diagnostics.outcomes_unparsed} of "
            f"{diagnostics.outcomes_total} outcomes could not be parsed"
        )
    return notes


def probe_plan(adapter: TheOddsApiAdapter) -> tuple[Finding, bool]:
    """Confirm the key authenticates and NCAAF is on the plan."""
    try:
        sports = adapter.list_sports()
    except OddsPlanError as exc:
        return (
            Finding(
                "plan / auth",
                False,
                f"provider rejected the key outright: {exc}",
                ["Re-copy ODDS_API_KEY from the-odds-api.com account page."],
            ),
            False,
        )
    except OddsAdapterError as exc:
        return Finding("plan / auth", False, str(exc)), False

    match = next(
        (s for s in sports if s.get("key") == NCAAF_SPORT_KEY), None
    )
    quota = adapter.quota
    if match is None:
        return (
            Finding(
                "plan / auth",
                False,
                f"{NCAAF_SPORT_KEY} is NOT listed on this plan "
                f"({len(sports)} sports returned). {quota.summary()}",
            ),
            False,
        )

    active = bool(match.get("active"))
    notes: list[str] = []
    if not active:
        notes.append(
            "Sport is listed but INACTIVE — normal in the offseason; "
            "live event lists will be empty until books post the slate."
        )

    # Infer the tier from the allowance rather than from which env var supplied
    # the key. A memo that says "paid" because it read ODDS_API_KEY, when the
    # free key happened to be pasted there, records a conclusion that is simply
    # wrong — and every coverage finding below depends on which tier ran.
    if quota.remaining is not None and quota.used is not None:
        total = quota.remaining + quota.used
        tier = "FREE" if total <= FREE_PLAN_CREDITS else "PAID"
        notes.append(
            f"allowance totals {total:,} credits, which indicates a {tier} plan "
            f"— coverage findings below are only valid for that tier"
        )

    return (
        Finding(
            "plan / auth",
            True,
            f"key valid, {NCAAF_SPORT_KEY} present (active={active}). "
            f"{quota.summary()}",
            notes,
        ),
        True,
    )


def probe_live_events(adapter: TheOddsApiAdapter) -> tuple[Finding, list[Any]]:
    """List upcoming NCAAF events."""
    try:
        events = adapter.list_events()
    except OddsAdapterError as exc:
        return Finding("live events", False, str(exc)), []

    if not events:
        return (
            Finding(
                "live events",
                None,
                "no upcoming NCAAF events posted. "
                f"{adapter.quota.summary()}",
                [
                    "Expected in the offseason — the 2026 season kicks off in "
                    "late August. Live market coverage cannot be measured yet; "
                    "the historical probe below answers the same questions "
                    "against a real 2025 game day.",
                ],
            ),
            [],
        )

    soonest = min(
        (e.commence_time for e in events if e.commence_time), default=None
    )
    return (
        Finding(
            "live events",
            True,
            f"{len(events)} upcoming event(s); soonest {soonest}. "
            f"{adapter.quota.summary()}",
        ),
        events,
    )


def probe_live_props(
    adapter: TheOddsApiAdapter, events: list[Any], limit: int
) -> Finding:
    """Fetch player props for a small number of live events and report coverage."""
    if not events:
        return Finding(
            "live player props", None, "skipped — no upcoming events to query"
        )

    combined = ParseDiagnostics()
    costs: list[int] = []
    probed = 0
    lead_times: list[float] = []

    for event in events[:limit]:
        if event.commence_time is not None:
            lead_times.append(
                (
                    event.commence_time - datetime.now(timezone.utc)
                ).total_seconds()
                / 86400.0
            )
        before = adapter.quota.remaining
        try:
            payload = adapter.fetch_props_raw(event.event_id)
        except OddsPlanError as exc:
            return Finding(
                "live player props",
                False,
                f"plan does not serve these markets: {exc}",
                [
                    "If this is a 422, at least one market key in markets.py is "
                    "not offered for NCAAF. Retry with a narrowed market list to "
                    "identify which.",
                ],
            )
        except OddsAdapterError as exc:
            return Finding("live player props", False, str(exc))

        _quotes, diagnostics = parse_event_odds(payload)
        combined.bookmakers |= diagnostics.bookmakers
        combined.markets_seen |= diagnostics.markets_seen
        combined.markets_unmapped |= diagnostics.markets_unmapped
        combined.outcomes_total += diagnostics.outcomes_total
        combined.outcomes_unparsed += diagnostics.outcomes_unparsed
        combined.quotes_two_way += diagnostics.quotes_two_way
        combined.quotes_one_sided += diagnostics.quotes_one_sided
        probed += 1

        after = adapter.quota
        measured = after.last_cost
        if measured is None and before is not None and after.remaining is not None:
            measured = before - after.remaining
        if measured is not None:
            costs.append(measured)

    notes = _describe_coverage(combined)
    if costs:
        per_event = sum(costs) / len(costs)
        notes.append(
            f"measured cost: {per_event:.0f} credits/event -> "
            f"~{per_event * GAMES_PER_WEEK:,.0f} credits for a "
            f"{GAMES_PER_WEEK}-game slate, per refresh"
        )

    served = bool(combined.bookmakers)

    # An empty response is NOT evidence of missing entitlement when the game is
    # still weeks away. Books post player props in the days before kickoff —
    # CLAUDE.md §7 is built around exactly that lateness. Calling this a failure
    # would put a wrong conclusion in the memo, so it is reported as unresolved
    # instead: the endpoint answered, it simply had nothing to say yet.
    nearest = min(lead_times) if lead_times else None
    too_early = nearest is not None and nearest > LATE_LINE_DAYS

    if not served and too_early:
        notes.insert(
            0,
            f"nearest probed game is {nearest:.0f} days away — books do not post "
            f"player props this far out, so an empty response here is expected "
            f"and says NOTHING about whether this plan covers them",
        )
        notes.append(
            "the call returned 200 rather than 401/403, so the event-odds "
            "endpoint itself is reachable on this key"
        )
        return Finding(
            "live player props",
            None,
            f"probed {probed} event(s); no props posted yet — UNRESOLVED, re-run "
            f"within {LATE_LINE_DAYS:.0f} days of kickoff",
            notes,
        )

    return Finding(
        "live player props",
        served,
        f"probed {probed} event(s); "
        + ("markets returned data" if served else "NO book returned any prop market"),
        notes,
    )


def probe_historical(
    adapter: TheOddsApiAdapter, iso_timestamp: str
) -> list[Finding]:
    """Determine whether historical prop odds are reachable on this plan.

    THE decisive check for Phase 3: it settles whether the backtest can grade
    edge, or only calibration.
    """
    try:
        snapshot = adapter.historical_events(iso_timestamp)
    except OddsPlanError as exc:
        return [
            Finding(
                "historical odds",
                False,
                f"NOT available on this plan: {exc}",
                [
                    "Consequence: the Phase 3 backtest grades MODEL CALIBRATION "
                    "only. Edge cannot be validated historically.",
                    "Path forward: closing lines accumulate from the moment live "
                    "ingest starts, so edge validation becomes possible during "
                    "the 2026 season without a paid backfill.",
                ],
            )
        ]
    except OddsAdapterError as exc:
        return [Finding("historical odds", False, str(exc))]

    events = snapshot.get("data") if isinstance(snapshot, dict) else None
    events = events or []
    findings = [
        Finding(
            "historical odds",
            True,
            f"available. {len(events)} event(s) at {iso_timestamp}. "
            f"{adapter.quota.summary()}",
        )
    ]

    if not events:
        findings[0].notes.append(
            "Endpoint is entitled but returned no events for this timestamp — "
            "try another in-season date before concluding coverage is thin."
        )
        return findings

    event_id = str(events[0].get("id") or "")
    before = adapter.quota.remaining
    try:
        payload = adapter.historical_props_raw(event_id, iso_timestamp)
    except OddsPlanError as exc:
        findings.append(
            Finding(
                "historical player props",
                False,
                f"event list is entitled but PROPS are not: {exc}",
                [
                    "Same consequence as no historical access: calibration only.",
                ],
            )
        )
        return findings
    except OddsAdapterError as exc:
        findings.append(Finding("historical player props", False, str(exc)))
        return findings

    inner = payload.get("data") if isinstance(payload, dict) else None
    _quotes, diagnostics = parse_event_odds(inner or {})

    notes = _describe_coverage(diagnostics)
    after = adapter.quota
    measured = after.last_cost
    if measured is None and before is not None and after.remaining is not None:
        measured = before - after.remaining
    if measured is not None:
        notes.append(
            f"measured cost: {measured} credits/event — historical is billed at "
            f"a premium, so a full-season backfill is "
            f"~{measured * GAMES_PER_WEEK * 14:,} credits"
        )

    served = bool(diagnostics.bookmakers)
    notes.append(
        "EDGE IS BACKTESTABLE" if served else "no props returned — calibration only"
    )
    findings.append(
        Finding(
            "historical player props",
            served,
            f"event {event_id} at {iso_timestamp}",
            notes,
        )
    )
    return findings


def render_report(findings: list[Finding], *, historical_date: str) -> str:
    """Write the coverage memo — the reviewable artifact for this sub-phase."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Odds coverage probe — The Odds API / NCAAF player props",
        "",
        f"Generated by `python -m worker.jobs.probe_odds` at {now}.",
        f"Historical probe date: `{historical_date}`.",
        "",
        "Answers the three questions CLAUDE.md §9.1 leaves open: does this plan",
        "cover NCAAF player props, what does a weekly refresh cost, and can edge",
        "be backtested against historical lines.",
        "",
        "## Findings",
        "",
    ]
    for finding in findings:
        mark = {True: "PASS", False: "FAIL", None: "INFO"}[finding.ok]
        lines.append(f"### {finding.name} — {mark}")
        lines.append("")
        lines.append(finding.detail)
        if finding.notes:
            lines.append("")
            lines.extend(f"- {n}" for n in finding.notes)
        lines.append("")

    lines.extend(
        [
            "## Market mapping under test",
            "",
            "| our `markets.key` | The Odds API key |",
            "|---|---|",
        ]
    )
    lines.extend(
        f"| `{ours}` | `{theirs}` |"
        for ours, theirs in sorted(OUR_KEY_TO_PROVIDER.items())
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-limit",
        type=int,
        default=1,
        help="How many live events to fetch props for (default 1, to spend "
        "as little quota as possible).",
    )
    parser.add_argument(
        "--historical-date",
        default=DEFAULT_HISTORICAL_DATE,
        help=f"ISO timestamp to probe historical odds at (default {DEFAULT_HISTORICAL_DATE}).",
    )
    parser.add_argument(
        "--skip-historical",
        action="store_true",
        help="Skip the historical probe (it is billed at a premium).",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Print findings without writing docs/odds-coverage-probe.md.",
    )
    parser.add_argument(
        "--free",
        action="store_true",
        help="Spend against ODDS_API_KEY_FREE instead of the paid key. The paid "
        "allowance is a shared pool across the client's other models and has "
        "already run out once mid-month, so dry runs should not touch it.",
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)

    api_key = settings.odds_key(prefer_free=args.free)
    if not api_key:
        log.error(
            "ODDS_API_KEY is not set. Add it to .env — it is required for this "
            "probe and is never stored in the repo (CLAUDE.md §0)."
        )
        return 2

    # Which tier produced a finding is part of the finding. "NCAAF props not
    # available" means something completely different on free than on paid.
    # Names the SOURCE only. Which tier the key actually belongs to is inferred
    # from the reported allowance in probe_plan — whichever env var it arrived
    # in, since either key can be pasted into either slot.
    if args.free and not settings.odds_api_key_free:
        tier = "ODDS_API_KEY (--free requested, but ODDS_API_KEY_FREE is unset)"
    elif args.free:
        tier = "ODDS_API_KEY_FREE"
    else:
        tier = "ODDS_API_KEY"
    log.info("Probing with the key from %s.", tier)

    findings: list[Finding] = []
    exit_code = 0

    try:
        with pipeline_run(JOB_NAME) as run_id:
            adapter = TheOddsApiAdapter(api_key)
            findings.append(Finding("key source", None, f"read from {tier}"))

            plan_finding, plan_ok = probe_plan(adapter)
            findings.append(plan_finding)

            if plan_ok:
                events_finding, events = probe_live_events(adapter)
                findings.append(events_finding)
                findings.append(
                    probe_live_props(adapter, events, args.event_limit)
                )

                if args.skip_historical:
                    findings.append(
                        Finding(
                            "historical odds",
                            None,
                            "skipped by --skip-historical",
                        )
                    )
                else:
                    findings.extend(
                        probe_historical(adapter, args.historical_date)
                    )

            log.info(
                "Odds probe findings:\n%s",
                "\n".join(f.render() for f in findings),
            )
            log.info(
                "Probe used %d call(s). %s",
                adapter.call_count,
                adapter.quota.summary(),
            )

            if not args.no_report:
                REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
                REPORT_PATH.write_text(
                    render_report(findings, historical_date=args.historical_date),
                    encoding="utf-8",
                )
                log.info("Wrote %s", REPORT_PATH)

            # A probe reports; it does not fail the pipeline for a "no". Only a
            # broken key or unreachable provider is an error, because everything
            # else is a finding the plan has to absorb, not a bug to fix.
            if not plan_ok:
                exit_code = 1
                raise RuntimeError("odds provider unreachable or key rejected")

            log.info("Odds probe complete (run %s)", run_id)
    except Exception as exc:
        log.error("Odds probe failed: %s", exc)
        return exit_code or 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
