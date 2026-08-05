"""Buy historical closing lines for past weeks, so edge can be graded.

    python -m worker.jobs.backfill_odds --season 2025 --weeks 6,7,8 --dry-run
    python -m worker.jobs.backfill_odds --season 2025 --weeks 6,7,8 --max-credits 4000

WHY THIS EXISTS. Every backtest number the project has produced was graded
against a SYNTHETIC line — each player's own trailing average (see the "Lines
are synthetic" caveat in the calibration report). That is enough to prove the
model is well CALIBRATED, and not nearly enough to prove it is PROFITABLE:
beating a player's trailing average is a far easier test than beating a price a
bookmaker set. Every edge % the board displays rests on the second claim. This
job buys the evidence that can settle it.

THE MEASUREMENT IS THE PURCHASE. Billing is per market RETURNED, so there is no
way to count how many games carried props without paying for the ones that did.
What is bought is kept, which is why this writes to `player_prop_lines` rather
than to a report: a week paid for once is graded as often as we like.

FOUR THINGS IT REFUSES TO DO.

  1. **Ask about a game that already kicked.** The historical endpoint answers
     "what was posted at time T", and books pull props at kickoff. An earlier
     probe asked one fixed timestamp for a whole slate, hit a game that had
     started two hours prior, got an empty 200 back and wrote "historical player
     props: FAIL" into a memo that then shaped Phase 3. Snapshots here are
     PER GAME, at `kickoff - --lead-minutes`, so every call is aimed at a
     moment when the market was actually open.
  2. **Spend past a ceiling.** What has been spent is counted from the
     provider's own reported cost, never from a formula. What may be spent next
     RESERVES the call's worst case, so `--max-credits` refuses a call that
     could breach rather than noticing afterwards — an early version compared
     only the running total and let one 60-credit call through a 25-credit
     ceiling. `--min-remaining` additionally refuses to draw the shared pool
     below a floor; these credits also feed the client's MLB, tennis and WNBA
     models.

  2b. **Lose what it paid for.** Writes commit per game, not at the end of the
     run. A network failure forty calls in must not roll back forty calls'
     worth of purchased data — the credits do not come back with it.
  3. **Read an empty response as an answer.** A 200 carrying no markets means
     UNRESOLVED, never "not covered". It is recorded as a game that carried
     nothing and cost nothing, which is a fact about that game, not about the
     plan.
  4. **Report a headline one-sided rate.** A one-sided price cannot be
     de-vigged and yields no edge at all, so that rate is the real constraint
     on what this backfill buys. It is reported PER MARKET, because anytime TD
     is posted Yes-only by most books and dominates any sample it appears in —
     a blended number would say 94% and mean nothing.

WHAT IT WRITES. Append-only rows in `player_prop_lines`, stamped `captured_at`
= the snapshot moment and `is_closing = true`. That combination is what unlocks
the closing-line hit-rate basis CLAUDE.md §9.2 leaves open. Re-running the same
week is idempotent: `captured_at` is part of the table's unique key.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

from worker.adapters.odds import (
    OddsAdapterError,
    OddsEvent,
    OddsPlanError,
    OddsQuotaError,
    get_adapter,
)
from worker.adapters.odds.base import SupportsHistorical
from worker.adapters.odds.markets import OUR_KEY_TO_PROVIDER
from worker.adapters.odds.null import ADAPTER_NAME as NULL_ADAPTER_NAME
from worker.adapters.odds.theoddsapi import (
    ADAPTER_NAME as THEODDSAPI_ADAPTER_NAME,
)
from worker.adapters.odds.theoddsapi import (
    parse_event_odds,
)
from worker.config import ConfigError, get_settings
from worker.db import connect, pipeline_run, set_rows_written
from worker.jobs.ingest_odds import (
    IngestReport,
    ingest_event,
    load_games,
    load_teams,
    match_event_to_game,
    resolve_adapter_name,
)
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "backfill_odds"

# How long before kickoff to snapshot. Late enough to be a genuine closing
# line, early enough that books have not begun pulling markets. 60 minutes sits
# inside the window the probe found populated.
DEFAULT_LEAD_MINUTES = 60

# Never draw the shared pool below this without being told to. The client's
# other models spend from the same allowance, and a backfill that runs long is
# the kind of thing that empties it quietly.
DEFAULT_MIN_REMAINING = 5_000

# MEASURED on 2026-08-05, not read from the docs: one historical event list cost
# 1 credit, and a single game returning 6 markets cost 60. Both are used only to
# RESERVE headroom before a call — what actually gets counted is the cost the
# provider reports afterwards.
EVENT_LIST_COST = 1
CREDITS_PER_MARKET = 10


def _iso(moment: datetime) -> str:
    """The provider's timestamp format: UTC, whole seconds, trailing Z."""
    return (
        moment.astimezone(UTC)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


@dataclass
class BackfillReport:
    """What the run bought, and what it learned about what is buyable.

    Deliberately separate from `IngestReport`, which counts resolution. This
    counts COVERAGE and COST — the two things the spend decision turns on.
    """

    weeks: list[int] = field(default_factory=list)
    # A dry run never asks for props, so every coverage number below is
    # UNMEASURED rather than zero. Carried explicitly because "carry rate: 0%"
    # printed by a run that did not ask is the same absence-of-evidence bug
    # that put "historical player props: FAIL" into a memo and shaped Phase 3.
    dry_run: bool = False
    snapshots: int = 0
    games_total: int = 0
    games_matched: int = 0
    games_skipped: int = 0
    games_priced: int = 0
    games_empty: int = 0
    credits_spent: int = 0
    stopped_early: str | None = None
    # Per market: how many book prices carried both sides. A market that is
    # mostly one-sided cannot contribute edge no matter how many rows it adds.
    two_way_by_market: Counter = field(default_factory=Counter)
    one_sided_by_market: Counter = field(default_factory=Counter)
    ingest: IngestReport = field(default_factory=IngestReport)

    @property
    def carry_rate(self) -> float:
        """Share of matched games that carried any props. Drives the budget."""
        attempted = self.games_priced + self.games_empty
        return self.games_priced / attempted if attempted else 0.0

    def credits_per_priced_game(self) -> float:
        return self.credits_spent / self.games_priced if self.games_priced else 0.0

    def render(self) -> str:
        lines = [
            f"  weeks: {self.weeks or '-'} over {self.snapshots} kickoff snapshot(s)",
            f"  games: {self.games_matched}/{self.games_total} matched to a "
            f"provider event",
        ]

        if self.dry_run:
            # State the projection, not a measurement. The worst case is every
            # matched game carrying every market we ask for.
            worst = self.games_matched * CREDITS_PER_MARKET * len(OUR_KEY_TO_PROVIDER)
            lines.append(
                "  carry rate: NOT MEASURED — a dry run never asks for props"
            )
            lines.append(
                f"  credits spent: {self.credits_spent} (event lists only); a real "
                f"run costs up to {worst:,} more if every game carries every market"
            )
            return "\n".join(lines)

        lines.extend([
            f"  of those: {self.games_priced} carried props, "
            f"{self.games_empty} carried none"
            + (
                f", {self.games_skipped} already bought (skipped, free)"
                if self.games_skipped
                else ""
            ),
            f"  carry rate: {self.carry_rate:.0%}"
            + (
                f"  ->  ~{self.credits_per_priced_game():.0f} credits per priced game"
                if self.games_priced
                else ""
            ),
            f"  credits spent: {self.credits_spent}",
            f"  rows written: {self.ingest.rows_written}",
        ])

        if self.two_way_by_market or self.one_sided_by_market:
            lines.append("  two-way rate BY MARKET (only two-way prices give edge):")
            markets = sorted(
                set(self.two_way_by_market) | set(self.one_sided_by_market)
            )
            for market in markets:
                two = self.two_way_by_market[market]
                one = self.one_sided_by_market[market]
                total = two + one
                lines.append(
                    f"    {market:<16} {two:>5} two-way / {total:>5} "
                    f"({two / total:.0%} usable)"
                    if total
                    else f"    {market:<16} no prices"
                )
        else:
            # Not the same as "no coverage" — say which one this is.
            lines.append(
                "  no prices parsed: nothing was returned for these snapshots. "
                "UNRESOLVED, not a coverage finding."
            )

        if self.stopped_early:
            lines.append(f"  STOPPED EARLY: {self.stopped_early}")

        lines.append("  --- resolution ---")
        lines.append(self.ingest.render_resolution())
        return "\n".join(lines)


def already_bought(
    conn: psycopg.Connection, season: int, week: int, adapter_name: str
) -> set[int]:
    """Games that already hold closing lines from this adapter.

    RESUMING IS THE NORMAL CASE, not the exception. Runs stop at a credit
    ceiling or a network failure, so finishing a week means running again — and
    without this, the second run re-buys everything the first one got. The
    unique key discards the duplicate ROWS, which is what hid the problem: a
    re-run looks harmless because nothing changes in the database, while the
    credits are spent all the same. Measured: the second week-8 run paid for 20
    games it already had.

    Keyed on the game rather than on individual rows because that is the unit
    that gets billed — one call per game, priced by markets returned.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct game_id
              from player_prop_lines
             where season = %s and week = %s
               and source_adapter = %s and is_closing
            """,
            (season, week, adapter_name),
        )
        return {int(row["game_id"]) for row in cur.fetchall()}


def snapshot_for(game: dict, lead_minutes: int) -> datetime | None:
    """The moment to ask about for one game: shortly before it kicked."""
    kickoff = game.get("start_date")
    if kickoff is None:
        return None
    return kickoff - timedelta(minutes=lead_minutes)


def group_by_snapshot(
    games: list[dict], lead_minutes: int
) -> dict[str, list[dict]]:
    """Bucket games by the timestamp we will ask the provider about.

    Games kicking together share one `historical_events` call, which is the
    only part of this job billed per snapshot rather than per game. Games with
    no kickoff time are dropped and counted — we cannot aim a snapshot at them,
    and guessing one is how the already-kicked bug happened.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for game in games:
        moment = snapshot_for(game, lead_minutes)
        if moment is None:
            continue
        buckets[_iso(moment)].append(game)
    return dict(sorted(buckets.items()))


def events_from_payload(payload: dict) -> list[OddsEvent]:
    """The historical event list, which nests its data under `data`."""
    raw = payload.get("data") if isinstance(payload, dict) else None
    events: list[OddsEvent] = []
    for item in raw or []:
        commence = item.get("commence_time")
        parsed = None
        if commence:
            try:
                parsed = datetime.fromisoformat(str(commence).replace("Z", "+00:00"))
            except ValueError:
                parsed = None
        events.append(
            OddsEvent(
                event_id=str(item.get("id") or ""),
                sport_key=str(item.get("sport_key") or ""),
                commence_time=parsed,
                home_team=str(item.get("home_team") or ""),
                away_team=str(item.get("away_team") or ""),
                raw=item,
            )
        )
    return events


def props_from_payload(payload: dict) -> dict:
    """Unwrap the historical envelope so the live parser can read it.

    The historical endpoints wrap the live shape in `{timestamp, data}`. Reusing
    `parse_event_odds` rather than writing a second parser means the one-sided
    handling and the per-book grouping cannot drift between live and backfill —
    the two paths whose numbers get compared to each other.
    """
    if isinstance(payload, dict) and "data" in payload:
        inner = payload.get("data")
        return inner if isinstance(inner, dict) else {}
    return payload if isinstance(payload, dict) else {}


class CreditBudget:
    """A ceiling enforced against the provider's own reported cost.

    `record` reads `quota.last_cost`, never a formula. The documented
    "markets x regions" model is a description of the billing, not the billing,
    and this job exists partly to measure the difference.

    THE CEILING RESERVES HEADROOM RATHER THAN WAITING TO BE CROSSED. A first
    version compared only what had already been spent, which let a single
    60-credit call sail through a 25-credit ceiling and then report "reached the
    25-credit ceiling (spent 61)". A limit that is only noticed after it has
    been passed by 144% is not a limit. `exhausted` therefore refuses any call
    whose WORST case would breach — `reserve` is that worst case, and for a
    props call it is 10 credits per market asked for.
    """

    def __init__(self, max_credits: int, min_remaining: int) -> None:
        self.max_credits = max_credits
        self.min_remaining = min_remaining
        self.spent = 0
        self.stopped: str | None = None

    def record(self, quota) -> None:
        cost = quota.last_cost
        if cost:
            self.spent += int(cost)

    def exhausted(self, quota, reserve: int = 0) -> bool:
        """Whether to stop BEFORE a call that could cost up to `reserve`.

        TWO conditions, not one. The budget can be spent out (`spent` has
        reached the ceiling) or merely too thin for what comes next (`reserve`
        would take it past). Folding them into a single comparison makes one of
        the two wrong at the boundary: `>=` refuses a call that exactly fits,
        `>` lets a run sit at its ceiling making free calls forever.
        """
        if self.spent >= self.max_credits:
            self.stopped = (
                f"reached the {self.max_credits}-credit ceiling "
                f"(spent {self.spent})"
            )
            return True
        if reserve and self.spent + reserve > self.max_credits:
            self.stopped = (
                f"stopping at {self.spent} of the {self.max_credits}-credit "
                f"ceiling: the next call could cost up to {reserve}"
            )
            return True
        remaining = quota.remaining
        if remaining is not None and remaining - reserve <= self.min_remaining:
            self.stopped = (
                f"provider pool down to {remaining:,}, at or below the "
                f"{self.min_remaining:,} floor reserved for the other models"
            )
            return True
        return False


def backfill_week(
    conn: psycopg.Connection,
    adapter: SupportsHistorical,
    *,
    season: int,
    week: int,
    lead_minutes: int,
    budget: CreditBudget,
    report: BackfillReport,
    dry_run: bool,
    exclude_markets: tuple[str, ...] = (),
    refresh: bool = False,
) -> None:
    """Walk one week's kickoff clusters, buying what the books had posted."""
    teams = load_teams(conn)
    games = load_games(conn, season, week)
    if not games:
        log.warning(
            "No games stored for %s week %s — ingest the schedule first.",
            season, week,
        )
        return

    report.games_total += len(games)
    market_keys = sorted(set(OUR_KEY_TO_PROVIDER) - set(exclude_markets or ()))

    bought = set() if refresh else already_bought(
        conn, season, week, THEODDSAPI_ADAPTER_NAME
    )
    if bought:
        report.games_skipped += len(bought)
        log.info(
            "%s week %s: skipping %d game(s) already bought — pass --refresh "
            "to buy a second snapshot of them.",
            season, week, len(bought),
        )
    # Worst case for one props call: every market we ask for comes back, at the
    # measured 10 credits each. The budget reserves this before every call so a
    # single event cannot overshoot the ceiling.
    props_worst_case = CREDITS_PER_MARKET * len(market_keys)

    for iso_timestamp, cluster in group_by_snapshot(games, lead_minutes).items():
        if budget.exhausted(adapter.quota, EVENT_LIST_COST):
            report.stopped_early = budget.stopped
            return

        payload = adapter.historical_events(iso_timestamp)
        budget.record(adapter.quota)
        report.snapshots += 1

        events = events_from_payload(payload)
        log.info(
            "%s week %s @ %s: %d event(s) live, %d of our game(s) in this cluster",
            season, week, iso_timestamp, len(events), len(cluster),
        )

        for game in cluster:
            if game["id"] in bought:
                continue
            matched = match_event_to_game_for(game, events, teams)
            if matched is None:
                continue
            report.games_matched += 1

            # A DRY RUN STOPS HERE. It resolves the slate and reports the match
            # rate; it does not ask for props, because props are the entire
            # cost. An earlier version gated only the database write, so
            # `--dry-run` faithfully wrote nothing while spending 60 credits on
            # the first game it saw — a preview flag that bills is worse than no
            # preview flag, since it is the one people reach for to be careful.
            if dry_run:
                continue

            if budget.exhausted(adapter.quota, props_worst_case):
                report.stopped_early = budget.stopped
                return

            try:
                raw = adapter.historical_props_raw(
                    matched.event_id, iso_timestamp, market_keys
                )
            except OddsQuotaError:
                report.stopped_early = (
                    "provider reported OUT_OF_USAGE_CREDITS; what was written "
                    "so far stands"
                )
                log.error("%s. %s", report.stopped_early, adapter.quota.summary())
                return
            except OddsPlanError as exc:
                report.stopped_early = f"plan does not serve this request: {exc}"
                log.error("%s", report.stopped_early)
                return
            finally:
                budget.record(adapter.quota)

            quotes, diagnostics = parse_event_odds(props_from_payload(raw))
            if not quotes:
                # A game books did not price. Free, and a real measurement of
                # the carry rate — NOT evidence about the plan.
                report.games_empty += 1
                continue

            report.games_priced += 1
            for quote in quotes:
                for price in quote.prices:
                    if price.is_two_way:
                        report.two_way_by_market[quote.market_key] += 1
                    else:
                        report.one_sided_by_market[quote.market_key] += 1

            if diagnostics.markets_unmapped:
                log.warning(
                    "Unmapped market(s) at %s: %s",
                    iso_timestamp, sorted(diagnostics.markets_unmapped),
                )

            ingest_event(
                conn,
                quotes,
                game,
                THEODDSAPI_ADAPTER_NAME,
                report.ingest,
                dry_run=dry_run,
                # The line was true at the snapshot, not now, and it is the last
                # one before kickoff by construction.
                captured_at=datetime.fromisoformat(
                    iso_timestamp.replace("Z", "+00:00")
                ),
                is_closing=True,
            )

            # COMMIT PER GAME, NOT AT THE END OF THE RUN. What has been paid
            # for is already spent, so a network blip forty calls in must not
            # roll back forty calls' worth of data — the credits do not come
            # back with it. A run that dies halfway leaves a half-backfilled
            # week, which re-running completes: `captured_at` is part of the
            # unique key, so the games already stored cost nothing to revisit.
            if not dry_run:
                conn.commit()


def match_event_to_game_for(game: dict, events: list[OddsEvent], teams):
    """Find the provider event for ONE of our games.

    `match_event_to_game` in the live job runs the other way round — provider
    event onto a list of our games — because a live run walks the provider's
    slate. A backfill walks OURS, so that we only ever pay for games we can
    actually grade. Same matcher, inverted caller.
    """
    for event in events:
        matched = match_event_to_game(event, [game], teams)
        if matched is not None:
            return event
    return None


def run(
    *,
    season: int,
    weeks: list[int],
    adapter_name: str,
    lead_minutes: int,
    max_credits: int,
    min_remaining: int,
    dry_run: bool,
    exclude_markets: tuple[str, ...] = (),
    refresh: bool = False,
) -> BackfillReport:
    report = BackfillReport(weeks=list(weeks), dry_run=dry_run)
    settings = get_settings()

    kwargs = {}
    if adapter_name == THEODDSAPI_ADAPTER_NAME:
        key = settings.odds_key()
        if not key:
            raise ConfigError(
                "ODDS_API_KEY is not set. A backfill spends real credits and "
                "cannot run without it."
            )
        kwargs["api_key"] = key

    adapter = get_adapter(adapter_name, **kwargs)
    if not isinstance(adapter, SupportsHistorical):
        raise ConfigError(
            f"Adapter {adapter_name!r} cannot serve historical odds, so there "
            "is nothing to backfill from. Set app_config.odds_adapter to "
            f"{THEODDSAPI_ADAPTER_NAME!r}, or pass --adapter."
        )

    budget = CreditBudget(max_credits, min_remaining)

    with connect() as conn:
        for week in weeks:
            if report.stopped_early:
                break
            backfill_week(
                conn,
                adapter,
                season=season,
                week=week,
                lead_minutes=lead_minutes,
                budget=budget,
                report=report,
                dry_run=dry_run,
                exclude_markets=exclude_markets,
                refresh=refresh,
            )
        # `backfill_week` commits per game. This catches nothing but a trailing
        # no-op, and is kept so the transaction is definitely closed.
        if not dry_run:
            conn.commit()

    report.credits_spent = budget.spent
    log.info("Provider quota: %s", adapter.quota.summary())
    return report


def parse_weeks(raw: str) -> list[int]:
    """`6,7,8` or `6-8` into [6, 7, 8]."""
    weeks: list[int] = []
    for part in raw.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            weeks.extend(range(int(start), int(end) + 1))
        else:
            weeks.append(int(chunk))
    return sorted(set(weeks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--weeks", required=True,
        help="Comma list or range: '8', '6,7,8', '6-8'.",
    )
    parser.add_argument("--adapter", help="Override app_config.odds_adapter.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve games to provider events and report, writing nothing. "
             "Still costs 1 credit per kickoff snapshot for the event list, "
             "but never asks for props — which is where the money is.",
    )
    parser.add_argument(
        "--max-credits", type=int, default=1_000,
        help="Hard ceiling on this run's spend, checked against the provider's "
             "reported cost. Default 1000, roughly one week.",
    )
    parser.add_argument(
        "--min-remaining", type=int, default=DEFAULT_MIN_REMAINING,
        help="Stop if the shared pool falls to this. Default "
             f"{DEFAULT_MIN_REMAINING}, reserved for the other models.",
    )
    parser.add_argument(
        "--lead-minutes", type=int, default=DEFAULT_LEAD_MINUTES,
        help="How long before kickoff to snapshot. Default "
             f"{DEFAULT_LEAD_MINUTES}.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Buy games that already have closing lines stored. Off by "
             "default: re-buying identical data costs credits and, because "
             "captured_at is part of the unique key, writes no rows.",
    )
    parser.add_argument(
        "--exclude-markets", default="",
        help="Comma list of our market keys NOT to buy. Billing is per market "
             "returned, so excluding one is a straight saving. 'anytime_td' is "
             "the one worth excluding: measured 0 of 1,802 prices two-way over "
             "20 games, and a one-sided price yields no edge at all.",
    )
    args = parser.parse_args(argv)

    exclude = tuple(
        part.strip() for part in args.exclude_markets.split(",") if part.strip()
    )
    unknown = sorted(set(exclude) - set(OUR_KEY_TO_PROVIDER))
    if unknown:
        # A typo here silently buys a market you meant to skip, and the bill is
        # the first place you would notice.
        log.error(
            "--exclude-markets has unknown key(s): %s. Known: %s",
            unknown, sorted(OUR_KEY_TO_PROVIDER),
        )
        return 2

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    try:
        adapter_name = resolve_adapter_name(args.adapter)
    except Exception as exc:
        log.error("Could not read app_config.odds_adapter: %s", exc)
        return 2

    if adapter_name == NULL_ADAPTER_NAME:
        # Unlike the live job, this is NOT a benign no-op. Nobody schedules a
        # backfill by accident; it was asked for, and silently doing nothing
        # would read as "there were no lines to find".
        log.error(
            "app_config.odds_adapter is %r, which serves no historical odds. "
            "Set it to %r or pass --adapter.",
            NULL_ADAPTER_NAME, THEODDSAPI_ADAPTER_NAME,
        )
        return 2

    weeks = parse_weeks(args.weeks)
    if not weeks:
        log.error("No weeks parsed from %r.", args.weeks)
        return 2

    try:
        with pipeline_run(
            JOB_NAME,
            metadata={
                "season": args.season,
                "weeks": weeks,
                "max_credits": args.max_credits,
                "dry_run": args.dry_run,
                "excluded_markets": list(exclude),
            },
        ) as run_id:
            report = run(
                season=args.season,
                weeks=weeks,
                adapter_name=adapter_name,
                lead_minutes=args.lead_minutes,
                max_credits=args.max_credits,
                min_remaining=args.min_remaining,
                dry_run=args.dry_run,
                exclude_markets=exclude,
                refresh=args.refresh,
            )
            log.info(
                "Odds backfill (%s%s):\n%s",
                adapter_name,
                ", DRY RUN" if args.dry_run else "",
                report.render(),
            )
            set_rows_written(run_id, report.ingest.rows_written)
    except (ConfigError, OddsAdapterError) as exc:
        log.error("%s", exc)
        return 1
    except Exception as exc:
        log.error("Odds backfill failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
