"""Phase 5a job: attach real book lines to the games we already project.

    python -m worker.jobs.ingest_odds --season 2026 --week 1
    python -m worker.jobs.ingest_odds --dry-run          # resolve, write nothing

WHAT THIS IS FOR. The model runs on every projected starter whether or not a
line exists, because college books post props late — often Thursday or Friday
for a Saturday game (CLAUDE.md §7). This job is the other half: it walks what
the book has posted so far and attaches it, so the OVER/UNDER call and the edge
fill in as the market appears. A player with no line still shows the model's
lean, and that is a supported state rather than a gap.

THREE THINGS IT REFUSES TO DO.

  1. **Guess an identity.** Provider strings are resolved through
     `worker.core.name_match`, which returns a refusal rather than a best guess.
     A line attached to the wrong player produces a confident, precise, WRONG
     edge and breaks nothing visible.
  2. **Report an impression instead of a rate.** Every unresolved event, player
     and market is counted and logged. "It looked fine" is not a measurement,
     and this project has been bitten three times by calls that succeeded and
     returned nothing.
  3. **Destroy line history.** `player_prop_lines` is APPEND-ONLY: each capture
     is a new row keyed by captured_at. The closing-line hit-rate basis
     (CLAUDE.md §9.2) is reconstructed from that history, so a delete-and-
     replace would quietly foreclose an option the client has not yet chosen.
     Only the synthetic DEV rows are ever replaced, and those are fake by
     construction.

The provider is whatever `app_config.odds_adapter` names. With "none" this job
is a no-op that says so and exits 0 — a scheduled canary must not go red
because a deliberate configuration is in force.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

from worker.adapters.odds import (
    SYNTHETIC_ADAPTER,
    OddsAdapterError,
    OddsPlanError,
    OddsQuotaError,
    PropQuote,
    get_adapter,
)
from worker.adapters.odds.markets import OUR_KEY_TO_PROVIDER
from worker.adapters.odds.null import ADAPTER_NAME as NULL_ADAPTER_NAME
from worker.adapters.odds.theoddsapi import ADAPTER_NAME as THEODDSAPI_ADAPTER_NAME
from worker.config import ConfigError, env_names_containing, get_settings
from worker.core.name_match import (
    PlayerMatch,
    PlayerResolver,
    TeamMatch,
    TeamResolver,
)
from worker.core.schedule import resolve_slate_args
from worker.db import connect, get_config_value, pipeline_run, set_rows_written
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_odds"

# How far a provider kickoff may sit from ours and still be the same game.
# Kickoff times drift by a few minutes between sources and get moved for TV, so
# an exact match is too strict; a whole day is loose enough to collide two games
# between the same pair of teams, which cannot happen inside one season anyway.
# Six hours comfortably covers the drift without reaching the next slate.
KICKOFF_TOLERANCE_HOURS = 6


@dataclass
class IngestReport:
    """Everything the run did, so the log states rates rather than vibes."""

    events_seen: int = 0
    events_matched: int = 0
    events_unmatched: list[str] = field(default_factory=list)
    events_with_props: int = 0
    event_method_mix: Counter = field(default_factory=Counter)
    # Matched events whose props were NOT fetched because --event-limit was
    # reached. Counted because the truncation is otherwise invisible and its
    # shape is nasty: the provider's event list is stably ordered, so the same
    # tail of the slate goes unpriced on every run rather than a different slice
    # each time. Nothing downstream can tell "no book posted a line for this
    # game" from "we stopped asking", and both look like a board of model leans.
    events_over_limit: int = 0
    # THREE DIFFERENT UNITS, kept apart on purpose. A "quote" is one player's
    # one market for one game; a "row" is that quote at ONE BOOK, so four books
    # on one market make one quote and four rows. An early version printed
    # rows/quotes as a ratio and reported "110/37 written", which reads as
    # having written three times what it saw.
    quotes_seen: int = 0
    quotes_resolved: int = 0
    rows_written: int = 0
    # Synthetic development rows removed because a real quote arrived for the
    # same player and market. Counted separately from rows_written because it
    # is a deletion, and a silent one would be worse than the collision.
    synthetic_displaced: int = 0
    players_unresolved: Counter = field(default_factory=Counter)
    players_ambiguous: Counter = field(default_factory=Counter)
    quotes_no_price: int = 0
    market_mix: Counter = field(default_factory=Counter)
    method_mix: Counter = field(default_factory=Counter)

    def render(self) -> str:
        lines = [
            f"  events: {self.events_matched}/{self.events_seen} matched to a "
            f"game, {self.events_with_props} carried props",
        ]
        if self.events_over_limit:
            lines.append(
                f"  TRUNCATED: {self.events_over_limit} matched event(s) never "
                "queried — --event-limit was reached. Raise it above the slate "
                "size; these games will show no line."
            )
        lines.append(self.render_resolution())
        return "\n".join(lines)

    def render_resolution(self) -> str:
        """The player/market half, without the event counters.

        Split out for `backfill_odds`, which walks OUR games rather than the
        provider's event list and so counts matching itself. Printing this
        report's untouched event counters underneath its own produced a report
        that said "1/60 games matched" and "events: 0/0 matched" four lines
        apart — both true of different things, and read as a contradiction.
        """
        lines = [
            f"  quotes: {self.quotes_resolved}/{self.quotes_seen} resolved to a "
            f"player  ->  {self.rows_written} book row(s) written",
        ]
        if self.event_method_mix:
            lines.append(f"  event match methods: {dict(self.event_method_mix)}")
        if self.market_mix:
            lines.append(f"  markets: {dict(self.market_mix)}")
        if self.method_mix:
            lines.append(f"  player match methods: {dict(self.method_mix)}")
        if self.quotes_no_price:
            lines.append(
                f"  {self.quotes_no_price} quote(s) dropped with no price on "
                "either side"
            )
        if self.events_unmatched:
            lines.append(
                f"  UNMATCHED EVENTS ({len(self.events_unmatched)}): "
                + "; ".join(self.events_unmatched[:10])
            )
        if self.players_ambiguous:
            lines.append(
                f"  AMBIGUOUS PLAYERS ({len(self.players_ambiguous)}): "
                + ", ".join(sorted(self.players_ambiguous)[:10])
            )
        if self.players_unresolved:
            lines.append(
                f"  UNRESOLVED PLAYERS ({len(self.players_unresolved)}): "
                + ", ".join(sorted(self.players_unresolved)[:10])
            )
        return "\n".join(lines)


def resolve_adapter_name(explicit: str | None) -> str:
    """Which adapter to run. Config decides; --adapter overrides for testing."""
    if explicit:
        return explicit
    configured = get_config_value("odds_adapter")
    return str(configured) if configured else NULL_ADAPTER_NAME


def load_teams(conn: psycopg.Connection) -> TeamResolver:
    with conn.cursor() as cur:
        cur.execute("select id, school, mascot, alt_name from teams")
        return TeamResolver(cur.fetchall())


def load_games(
    conn: psycopg.Connection, season: int, week: int | None
) -> list[dict]:
    sql = (
        "select id, season, week, start_date, home_team_id, away_team_id "
        "  from games where season = %s"
    )
    params: list[object] = [season]
    if week is not None:
        sql += " and week = %s"
        params.append(week)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()


def match_event_to_game(
    event, games: list[dict], resolver: TeamResolver
) -> tuple[dict, str] | None:
    """Pin a provider event onto one of our games, and say how.

    Resolved on the TEAM PAIR, with kickoff only as a tiebreak. Two FBS teams
    meet at most once in a regular season, so the pair is very nearly a key on
    its own, and leaning on it means an event still resolves when the provider
    and CFBD disagree about kickoff — which they do, routinely, for TV windows.
    """
    home = resolver.resolve(event.home_team)
    away = resolver.resolve(event.away_team)
    resolved = [m for m in (home, away) if isinstance(m, TeamMatch)]

    if len(resolved) == 2:
        pair = {resolved[0].team_id, resolved[1].team_id}
        candidates = [
            g for g in games if {g["home_team_id"], g["away_team_id"]} == pair
        ]
        picked = _nearest_kickoff(candidates, event)
        if picked is not None:
            return picked, "both-teams"
        return None

    # ONE SIDE ONLY. Not a fallback into guessing — it still demands a UNIQUE
    # answer. A team plays once a week, so "the games this team appears in,
    # near this kickoff" has exactly one member or the event is rejected.
    #
    # This exists because the provider sometimes sends a bare school with no
    # mascot ("Albany"), which is genuinely ambiguous against our table on its
    # own — we carry both UAlbany and Albany State GA — while the OPPONENT and
    # the date pin the fixture with no ambiguity at all. FCS visitors are
    # common in early-season non-conference weeks, and their FBS hosts are
    # exactly the teams whose players get priced.
    if len(resolved) == 1:
        known = resolved[0].team_id
        candidates = [
            g for g in games
            if known in (g["home_team_id"], g["away_team_id"])
        ]
        picked = _nearest_kickoff(candidates, event, require_unique_window=True)
        if picked is not None:
            return picked, "one-team+kickoff"

    return None


def _nearest_kickoff(
    candidates: list[dict], event, *, require_unique_window: bool = False
) -> dict | None:
    """Choose among candidate games by kickoff proximity.

    `require_unique_window` is the safety catch for one-sided matching: it
    demands that exactly ONE candidate sits inside the tolerance, so a team
    with two plausible fixtures resolves to neither.
    """
    if not candidates:
        return None
    if len(candidates) == 1 and not require_unique_window:
        return candidates[0]
    if event.commence_time is None:
        return None

    def distance(game: dict) -> float:
        return abs((game["start_date"] - event.commence_time).total_seconds())

    within = [
        g for g in candidates
        if distance(g) <= KICKOFF_TOLERANCE_HOURS * 3600
    ]
    if require_unique_window:
        return within[0] if len(within) == 1 else None
    return min(within, key=distance) if within else None


def load_roster(conn: psycopg.Connection, game: dict) -> PlayerResolver:
    """Candidates for one game: the two rosters, with games played.

    `games` feeds the resolver's stub tie-break — our own data pairs real
    players with empty duplicate rows, and a player who has never appeared is
    not one a book has priced.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select p.id, p.name,
                   (select count(*) from player_game_stats s
                     where s.player_id = p.id and s.season = %s) as games
              from players p
              join player_team_seasons pts on pts.player_id = p.id
             where pts.season = %s and pts.team_id in (%s, %s)
            """,
            (game["season"], game["season"], game["home_team_id"],
             game["away_team_id"]),
        )
        return PlayerResolver(cur.fetchall())


def ensure_sportsbooks(conn: psycopg.Connection, quotes: list[PropQuote]) -> dict:
    """Insert any book we have not seen before, and map key -> id."""
    keys = {p.sportsbook_key: p.sportsbook_name for q in quotes for p in q.prices}
    if not keys:
        return {}
    with conn.cursor() as cur:
        for key, name in sorted(keys.items()):
            cur.execute(
                "insert into sportsbooks (key, display_name) values (%s, %s) "
                "on conflict (key) do nothing",
                (key, name),
            )
        cur.execute(
            "select key, id from sportsbooks where key = any(%s)",
            (sorted(keys),),
        )
        return {r["key"]: int(r["id"]) for r in cur.fetchall()}


def ingest_event(
    conn: psycopg.Connection,
    quotes: list[PropQuote],
    game: dict,
    adapter_name: str,
    report: IngestReport,
    *,
    dry_run: bool,
    captured_at: datetime | None = None,
    is_closing: bool = False,
) -> None:
    """Resolve and write one event's quotes.

    `captured_at` defaults to None, which lets the column's `now()` default
    stand — right for a live capture, where the row is true as of this moment.
    A BACKFILL must pass the snapshot timestamp instead: a 2025 line stamped
    with today's date would sort as the newest quote on that game and be read
    as the current market. It also makes the backfill idempotent, since
    `captured_at` is part of the table's unique key.

    `is_closing` is likewise the backfill's to set — it captures deliberately
    just before kickoff, which is the definition the column carries. A live
    in-week run cannot know it is looking at the last line before kickoff, so
    it never claims to be.
    """
    players = load_roster(conn, game)
    books = ensure_sportsbooks(conn, quotes) if not dry_run else {}

    rows: list[tuple] = []
    for quote in quotes:
        report.quotes_seen += 1
        outcome = players.resolve(quote.player_name)
        if isinstance(outcome, list):
            report.players_ambiguous[quote.player_name] += 1
            continue
        if not isinstance(outcome, PlayerMatch):
            report.players_unresolved[quote.player_name] += 1
            continue

        report.quotes_resolved += 1
        report.method_mix[outcome.method] += 1
        for price in quote.prices:
            # The schema requires at least one side. A quote with neither is a
            # parse artefact, not a market.
            if price.over_price is None and price.under_price is None:
                report.quotes_no_price += 1
                continue
            report.market_mix[quote.market_key] += 1
            rows.append(
                (
                    game["id"], outcome.player_id, quote.market_key,
                    books.get(price.sportsbook_key), game["season"],
                    game["week"], price.line, price.over_price,
                    price.under_price, adapter_name,
                    json.dumps({"provider_player": quote.player_name}),
                    captured_at, is_closing,
                )
            )

    # A dry run resolves everything and reports the rate, but writes nothing —
    # that is the whole point of it, so the counter must NOT advance or the
    # report would claim rows that do not exist.
    if dry_run or not rows:
        return

    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into player_prop_lines
              (game_id, player_id, market_key, sportsbook_id, season, week,
               line, over_price, under_price, source_adapter, source_payload,
               captured_at, is_closing)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    coalesce(%s, now()), %s)
            on conflict do nothing
            """,
            rows,
        )
    report.rows_written += len(rows)

    # A REAL QUOTE EVICTS THE FAKE ONE IT REPLACES, in the same transaction that
    # wrote it. Synthetic rows exist only so the OVER/UNDER path could be built
    # before books posted; once a real price exists for the same player and
    # market, the fake one is not a second opinion, it is noise the board cannot
    # distinguish from a market — and its -110/-110 de-vigs to exactly 0.500,
    # so it reads as a book disagreeing with us at maximum confidence.
    #
    # This happened for real: week 8 of 2025 was seeded with synthetic lines on
    # 2026-08-04, the bought closing lines landed on 2026-08-05, and 556 board
    # picks spent three days priced off a fake line while a real quote sat
    # beside it. `audit_data` caught it, but only when somebody ran it. Doing
    # the eviction here makes the collision impossible rather than detectable.
    if adapter_name != SYNTHETIC_ADAPTER:
        with conn.cursor() as cur:
            cur.execute(
                """
                delete from player_prop_lines
                 where source_adapter = %s
                   and (game_id, player_id, market_key) in (
                         select unnest(%s::bigint[]), unnest(%s::bigint[]),
                                unnest(%s::text[]))
                """,
                (
                    SYNTHETIC_ADAPTER,
                    [r[0] for r in rows],
                    [r[1] for r in rows],
                    [r[2] for r in rows],
                ),
            )
            report.synthetic_displaced += cur.rowcount


def run(
    *,
    season: int,
    week: int | None,
    adapter_name: str,
    dry_run: bool,
    event_limit: int | None,
    prefer_free: bool = False,
) -> IngestReport:
    report = IngestReport()
    settings = get_settings()

    kwargs = {}
    if adapter_name == THEODDSAPI_ADAPTER_NAME:
        # The env var is the deployment-time switch and the flag is the CLI
        # one; either alone is enough. render.yaml cannot express this -- the
        # cron's command is fixed and its schedule cannot be made one-shot
        # without tripping the monitor's period guards -- so the fallback has
        # to be flippable from the Render dashboard without a redeploy.
        prefer_free = prefer_free or settings.odds_prefer_free
        key = settings.odds_key(prefer_free=prefer_free)
        if not key:
            # SAY WHAT THE PROCESS CAN SEE, not only what it wanted. The bare
            # message reads as "you never added the key" and is equally true
            # when the key was added under the wrong name -- which is what
            # actually happened on 2026-09-03 (`ODDS_API_VARIABLE`). Names
            # only; see env_names_containing() for why that matters.
            visible = env_names_containing("ODDS")
            if "ODDS_API_KEY" in visible:
                # `_optional` returns None for absent OR empty, so an empty
                # value arrives here looking exactly like a missing one — while
                # the dashboard shows a variable that is present and correct.
                # Saying "misnamed" here would be actively false.
                diagnosis = (
                    " ODDS_API_KEY IS present in this environment, so its value "
                    "must be empty or blank — an empty variable reads as unset. "
                    "Re-enter the value rather than looking for a missing name."
                )
            elif visible:
                diagnosis = (
                    " Environment variables with 'ODDS' in the name that this "
                    f"process CAN see: {', '.join(visible)}. None of them is "
                    "ODDS_API_KEY, so the value is most likely set under the "
                    "wrong name (names shown, never values)."
                )
            else:
                diagnosis = (
                    " No environment variable with 'ODDS' in the name is "
                    "visible to this process at all, so the key is absent from "
                    "this run's environment rather than misnamed. On Render a "
                    "dashboard change reaches a SCHEDULED run only once the "
                    "service redeploys; check render_git_commit in this row's "
                    "metadata against the deploy you expect."
                )
            raise ConfigError(
                "ODDS_API_KEY is not set, but app_config.odds_adapter selects "
                f"{adapter_name!r}. Set the key, or set the adapter to "
                f"{NULL_ADAPTER_NAME!r} to run without lines." + diagnosis
            )
        # Name the pool this run will bill. odds_key() falls back to the paid
        # key when --free is asked for and no free key is configured, so the
        # flag alone does not prove which allowance got spent -- and a run that
        # quietly drained the shared pool is the surprise this line prevents.
        using_free = prefer_free and bool(settings.odds_api_key_free)
        if prefer_free and not using_free:
            log.warning(
                "The free key was requested (--free or ODDS_PREFER_FREE) but "
                "ODDS_API_KEY_FREE is unset, so this run bills the shared paid "
                "pool instead."
            )
        log.info(
            "Billing against %s.",
            "ODDS_API_KEY_FREE" if using_free else "ODDS_API_KEY (shared paid pool)",
        )
        kwargs["api_key"] = key

    adapter = get_adapter(adapter_name, **kwargs)
    events = adapter.list_events()
    report.events_seen = len(events)

    with connect() as conn:
        resolver = load_teams(conn)
        games = load_games(conn, season, week)
        if not games:
            log.warning(
                "No games stored for season %s%s. The schedule has to be "
                "ingested before lines can attach to anything: "
                "python -m worker.jobs.ingest_reference --seasons %s",
                season, f" week {week}" if week else "", season,
            )
            return report

        processed = 0
        for event in events:
            matched = match_event_to_game(event, games, resolver)
            if matched is None:
                report.events_unmatched.append(
                    f"{event.away_team} @ {event.home_team}"
                )
                continue
            game, how = matched
            report.events_matched += 1
            report.event_method_mix[how] += 1

            if event_limit is not None and processed >= event_limit:
                report.events_over_limit += 1
                continue
            processed += 1

            try:
                quotes = adapter.fetch_props(
                    event.event_id, sorted(OUR_KEY_TO_PROVIDER)
                )
            except OddsQuotaError:
                log.error(
                    "Out of credits after %d event(s). Stopping; what was "
                    "written so far stands. %s",
                    processed, adapter.quota.summary(),
                )
                break
            except OddsPlanError as exc:
                log.error("Plan does not serve these markets: %s", exc)
                break

            if not quotes:
                continue
            report.events_with_props += 1
            ingest_event(
                conn, quotes, game, adapter_name, report, dry_run=dry_run
            )

        if not dry_run:
            conn.commit()

    # Loud, because the alternative is a board that silently stops covering part
    # of the slate while every counter above still reads as a successful run.
    if report.events_over_limit:
        log.warning(
            "--event-limit %s stopped %d matched event(s) from being queried. "
            "The provider's event order is stable, so this is the SAME tail of "
            "the slate on every run: those games carry no book line all week, "
            "and nothing downstream can tell that apart from a book declining "
            "to post one. Raise the limit above the slate size — the largest "
            "week measured is 99 games.",
            event_limit, report.events_over_limit,
        )

    log.info("Provider quota: %s", adapter.quota.summary())
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Both default to the current slate so this can run on a cron, which has
    # nobody to tell it what week it is. An explicit value always wins.
    parser.add_argument("--season", type=int, help="Defaults to the current slate.")
    parser.add_argument(
        "--week", type=int,
        help="Restrict to one week. Defaults to the current slate's week.",
    )
    parser.add_argument(
        "--adapter",
        help="Override app_config.odds_adapter (for testing a provider).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve and report, write nothing.",
    )
    parser.add_argument(
        "--event-limit", type=int,
        help="Fetch props for at most N matched events. Quota discipline: "
             "billing is per market returned, so this bounds the spend.",
    )
    parser.add_argument(
        "--free", action="store_true",
        help="Bill against ODDS_API_KEY_FREE instead of the shared paid pool. "
             "ODDS_PREFER_FREE=1 in the environment does the same thing, for "
             "the cron, whose command lives in render.yaml. "
             "The paid allowance is shared with the client's other models and "
             "has already run out mid-month once; this is the fallback for a "
             "slate that needs lines while that pool is empty.",
    )
    args = parser.parse_args(argv)

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
        # A DELIBERATE CONFIGURATION, NOT A FAILURE. The board is built to show
        # model leans with no book line attached, so this must exit 0 or the
        # cron canary goes red on a working system.
        log.info(
            "app_config.odds_adapter is %r — no lines will be ingested and the "
            "board shows model leans only. Set it to %r to ingest.",
            NULL_ADAPTER_NAME, THEODDSAPI_ADAPTER_NAME,
        )
        return 0

    try:
        season, week = resolve_slate_args(args.season, args.week)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        with pipeline_run(
            JOB_NAME, metadata={"season": season, "week": week}
        ) as run_id:
            report = run(
                season=season,
                week=week,
                adapter_name=adapter_name,
                dry_run=args.dry_run,
                event_limit=args.event_limit,
                prefer_free=args.free,
            )
            log.info(
                "Odds ingest (%s%s):\n%s",
                adapter_name, ", DRY RUN" if args.dry_run else "", report.render(),
            )
            set_rows_written(run_id, report.rows_written)
    except (ConfigError, OddsAdapterError) as exc:
        log.error("%s", exc)
        return 1
    except Exception as exc:
        log.error("Odds ingest failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
