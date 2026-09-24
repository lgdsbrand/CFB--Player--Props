"""Game odds — moneyline, spread and total — from sharp and retail books.

    python -m worker.jobs.ingest_game_odds
    python -m worker.jobs.ingest_game_odds --dry-run
    python -m worker.jobs.ingest_game_odds --paid

The game model's capture (CLAUDE.md §11, phase G1). ONE bulk call per run,
billed per market per region and not per event: 3 markets x 3 regions is 9
credits whether the slate is 8 games or 71. That is why this job defaults to
the FREE key, which the player-props capture cannot live on — and why `--paid`
exists for when the client tops up and wants more frequent pulls.

WRITES ONLY WHAT MOVED. Each quote is compared with the book's newest row for
the same (game, book, period, market); an unchanged price writes nothing. See
migration 0074 for why that loses no information.

NEVER CAPTURES A GAME THAT HAS STARTED. The bulk endpoint returns in-play
prices for live games, and one of those written after kickoff would become the
"closing" line every grade is measured against.

HOME AND AWAY ARE OURS. The provider's home team is routinely our away team at
a neutral site, so each price is assigned by resolving its outcome's team name
against the MATCHED GAME's home_team_id, never by the provider's label.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

from worker.adapters.odds import OddsQuotaError
from worker.adapters.odds.base import GameBookMarket, GameEventOdds
from worker.adapters.odds.markets import sport_key_for
from worker.adapters.odds.theoddsapi import (
    ADAPTER_NAME,
    GAME_MARKETS,
    GAME_REGIONS,
    TheOddsApiAdapter,
)
from worker.config import ConfigError, get_settings
from worker.core.name_match import TeamMatch, TeamResolver
from worker.db import connect, pipeline_run, resolve_seasons, set_rows_written
from worker.jobs.ingest_odds import load_games, load_teams, match_event_to_game
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_game_odds"

# The bulk endpoint serves full-game markets only. 1H/1Q are per-event and
# billed like player props; they arrive in the same table later (0074).
PERIOD = "full"

# Spreads must mirror (home -3.5 is away +3.5). A book sending anything else is
# a malformed quote, and storing either half would put a spread on screen that
# the book never offered.
_MIRROR_TOLERANCE = 1e-6


@dataclass(frozen=True)
class GameOddsRow:
    """One book's one market on one game, oriented to OUR home and away."""

    sportsbook_key: str
    sportsbook_name: str
    market: str
    line: float | None
    home_price: int | None
    away_price: int | None
    over_price: int | None
    under_price: int | None
    book_updated_at: datetime | None

    def price_key(self) -> tuple:
        """What counts as 'the price moved'. Timestamps deliberately excluded."""
        return (
            None if self.line is None else round(float(self.line), 1),
            self.home_price,
            self.away_price,
            self.over_price,
            self.under_price,
        )


@dataclass
class GameOddsReport:
    events_seen: int = 0
    events_matched: int = 0
    events_started: int = 0
    events_unmatched: list[str] = field(default_factory=list)
    books: set[str] = field(default_factory=set)
    rows_written: int = 0
    rows_unchanged: int = 0
    rejected: Counter = field(default_factory=Counter)

    def render(self) -> str:
        lines = [
            f"events seen      {self.events_seen}",
            f"  matched        {self.events_matched}",
            f"  started (skip) {self.events_started}",
            f"  unmatched      {len(self.events_unmatched)}",
            f"books            {len(self.books)}: {', '.join(sorted(self.books))}",
            f"rows written     {self.rows_written}",
            f"rows unchanged   {self.rows_unchanged}",
        ]
        if self.rejected:
            lines.append(
                "rejected quotes  "
                + ", ".join(f"{k} {v}" for k, v in sorted(self.rejected.items()))
            )
        for name in self.events_unmatched[:20]:
            lines.append(f"  unmatched: {name}")
        return "\n".join(lines)


def side_resolver(
    event_odds: GameEventOdds, game: dict, resolver: TeamResolver
) -> Callable[[str], str | None]:
    """Map a provider team string to 'home' or 'away' in OUR game.

    The two strings the event carries are resolved to team ids and compared
    with the game's own. When only one resolves — the one-sided match the
    event matcher allows for bare FCS names — the other string takes the
    game's remaining side, which is safe because the matcher already demanded
    that the pair pin exactly one game.
    """
    ours = {game["home_team_id"]: "home", game["away_team_id"]: "away"}
    by_name: dict[str, str] = {}
    unresolved: list[str] = []
    for name in (event_odds.event.home_team, event_odds.event.away_team):
        match = resolver.resolve(name)
        side = ours.get(match.team_id) if isinstance(match, TeamMatch) else None
        if side is None:
            unresolved.append(name)
        else:
            by_name[name] = side
    if len(unresolved) == 1 and len(by_name) == 1:
        taken = next(iter(by_name.values()))
        by_name[unresolved[0]] = "away" if taken == "home" else "home"
    # Both strings landing on one side means the resolver and the matcher
    # disagree about who is playing. Refuse the event rather than guess.
    if len(set(by_name.values())) != len(by_name):
        by_name = {}
    return by_name.get


def orient(
    market: GameBookMarket, side_of: Callable[[str], str | None]
) -> tuple[GameOddsRow | None, str | None]:
    """One book market -> one row in our orientation, or a reason it was refused."""

    def row(**prices) -> GameOddsRow:
        return GameOddsRow(
            sportsbook_key=market.sportsbook_key,
            sportsbook_name=market.sportsbook_name,
            market=market.market,
            book_updated_at=market.book_updated_at,
            **{
                "line": None,
                "home_price": None,
                "away_price": None,
                "over_price": None,
                "under_price": None,
                **prices,
            },
        )

    if market.market == "totals":
        over = [o for o in market.outcomes if o.name.lower() == "over"]
        under = [o for o in market.outcomes if o.name.lower() == "under"]
        if len(over) > 1 or len(under) > 1:
            return None, "totals: duplicate side"
        o = over[0] if over else None
        u = under[0] if under else None
        points = {x.point for x in (o, u) if x is not None and x.point is not None}
        if len(points) != 1:
            return None, "totals: no single point"
        if (o is None or o.price is None) and (u is None or u.price is None):
            return None, "totals: no price"
        return row(
            line=points.pop(),
            over_price=o.price if o else None,
            under_price=u.price if u else None,
        ), None

    sided: dict[str, list] = {"home": [], "away": []}
    for outcome in market.outcomes:
        side = side_of(outcome.name)
        if side is None:
            return None, f"{market.market}: unknown team"
        sided[side].append(outcome)
    if len(sided["home"]) > 1 or len(sided["away"]) > 1:
        return None, f"{market.market}: duplicate side"
    home = sided["home"][0] if sided["home"] else None
    away = sided["away"][0] if sided["away"] else None
    home_price = home.price if home else None
    away_price = away.price if away else None
    if home_price is None and away_price is None:
        return None, f"{market.market}: no price"

    if market.market == "h2h":
        return row(home_price=home_price, away_price=away_price), None

    # spreads — stored from OUR home team's perspective.
    home_point = home.point if home else None
    away_point = away.point if away else None
    if home_point is not None and away_point is not None:
        if abs(home_point + away_point) > _MIRROR_TOLERANCE:
            return None, "spreads: sides do not mirror"
        line = home_point
    elif home_point is not None:
        line = home_point
    elif away_point is not None:
        line = -away_point
    else:
        return None, "spreads: no point"
    return row(line=line, home_price=home_price, away_price=away_price), None


def ensure_books(conn: psycopg.Connection, rows: list[GameOddsRow]) -> dict[str, int]:
    names = {r.sportsbook_key: r.sportsbook_name for r in rows}
    if not names:
        return {}
    with conn.cursor() as cur:
        for key, name in sorted(names.items()):
            cur.execute(
                "insert into sportsbooks (key, display_name) values (%s, %s) "
                "on conflict (key) do nothing",
                (key, name),
            )
        cur.execute(
            "select key, id from sportsbooks where key = any(%s)", (sorted(names),)
        )
        return {r["key"]: int(r["id"]) for r in cur.fetchall()}


def latest_prices(
    conn: psycopg.Connection, game_ids: list[int]
) -> dict[tuple[int, int, str, str], tuple]:
    """The newest stored price per (game, book, period, market)."""
    if not game_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct on (game_id, sportsbook_id, period, market)
                   game_id, sportsbook_id, period, market,
                   line, home_price, away_price, over_price, under_price
              from game_odds
             where game_id = any(%s)
             order by game_id, sportsbook_id, period, market, captured_at desc
            """,
            (game_ids,),
        )
        return {
            (r["game_id"], r["sportsbook_id"], r["period"], r["market"]): (
                None if r["line"] is None else round(float(r["line"]), 1),
                r["home_price"],
                r["away_price"],
                r["over_price"],
                r["under_price"],
            )
            for r in cur.fetchall()
        }


def run(
    *,
    season: int,
    sport: str = "cfb",
    prefer_free: bool = True,
    dry_run: bool = False,
    now: datetime | None = None,
    adapter: TheOddsApiAdapter | None = None,
) -> GameOddsReport:
    report = GameOddsReport()
    now = now or datetime.now(UTC)

    if adapter is None:
        settings = get_settings()
        key = settings.odds_key(prefer_free=prefer_free)
        if not key:
            raise ConfigError(
                "No odds key: set ODDS_API_KEY_FREE (the default for this job) "
                "or ODDS_API_KEY."
            )
        using_free = prefer_free and bool(settings.odds_api_key_free)
        log.info(
            "Billing against %s.",
            "ODDS_API_KEY_FREE" if using_free else "ODDS_API_KEY (shared paid pool)",
        )
        adapter = TheOddsApiAdapter(key, sport_key=sport_key_for(sport))

    log.info("Requesting %s across regions %s.", ",".join(GAME_MARKETS), GAME_REGIONS)
    events = adapter.fetch_game_odds()
    report.events_seen = len(events)

    with connect() as conn:
        resolver = load_teams(conn, sport=sport)
        games = load_games(conn, season, None, sport=sport)
        if not games:
            log.warning("No %s games stored for season %s.", sport, season)
            return report

        oriented: list[tuple[int, GameOddsRow]] = []
        for event_odds in events:
            event = event_odds.event
            matched = match_event_to_game(event, games, resolver)
            if matched is None:
                report.events_unmatched.append(f"{event.away_team} @ {event.home_team}")
                continue
            game, _how = matched
            report.events_matched += 1

            kickoff = event.commence_time or game.get("start_date")
            if kickoff is not None and kickoff <= now:
                report.events_started += 1
                continue

            side_of = side_resolver(event_odds, game, resolver)
            for market in event_odds.markets:
                row, reason = orient(market, side_of)
                if row is None:
                    report.rejected[reason] += 1
                    continue
                report.books.add(row.sportsbook_key)
                oriented.append((int(game["id"]), row))

        if dry_run:
            report.rows_written = 0
            log.info("Dry run: %d quote(s) oriented, nothing written.", len(oriented))
            log.info("Provider quota: %s", adapter.quota.summary())
            return report

        book_ids = ensure_books(conn, [r for _, r in oriented])
        stored = latest_prices(conn, sorted({g for g, _ in oriented}))
        changed: list[tuple] = []
        for game_id, row in oriented:
            book_id = book_ids[row.sportsbook_key]
            if stored.get((game_id, book_id, PERIOD, row.market)) == row.price_key():
                report.rows_unchanged += 1
                continue
            changed.append((
                game_id, book_id, PERIOD, row.market, row.line,
                row.home_price, row.away_price, row.over_price, row.under_price,
                row.book_updated_at, now, ADAPTER_NAME,
            ))

        # ONE executemany, which psycopg pipelines. Row-by-row execute was a
        # round trip per quote: the first production capture (2026-09-23)
        # sent ~3,870 of them, ran past three minutes and was killed having
        # written nothing.
        if changed:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    insert into game_odds (
                      game_id, sportsbook_id, period, market, line,
                      home_price, away_price, over_price, under_price,
                      book_updated_at, captured_at, source_adapter
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    changed,
                )
        report.rows_written = len(changed)
        conn.commit()

    log.info("Provider quota: %s", adapter.quota.summary())
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", default="cfb", choices=("cfb", "nfl"))
    parser.add_argument(
        "--seasons", type=int, nargs="+",
        help="Match events against this season's games. Default: current_season.",
    )
    parser.add_argument(
        "--paid", action="store_true",
        help="Bill the shared paid pool instead of ODDS_API_KEY_FREE.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    try:
        seasons = resolve_seasons(args.seasons, current=True)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    season = seasons[-1]

    if args.dry_run:
        # Not recorded in pipeline_runs: a dry run writing 0 rows must not read
        # to the monitor as a capture that found nothing. It still spends the
        # 9 credits, because the point of it is to see the real matching.
        try:
            report = run(season=season, sport=args.sport,
                         prefer_free=not args.paid, dry_run=True)
        except Exception as exc:
            log.error("Dry run failed: %s", exc, exc_info=True)
            return 1
        log.info("Game odds dry run:\n%s", report.render())
        return 0

    try:
        with pipeline_run(
            JOB_NAME, metadata={"season": season, "sport": args.sport, "paid": args.paid}
        ) as run_id:
            report = run(
                season=season,
                sport=args.sport,
                prefer_free=not args.paid,
            )
            set_rows_written(run_id, report.rows_written)
            log.info("Game odds capture:\n%s", report.render())
            # A capture that matched nothing is a broken capture, not a quiet
            # week: the provider always lists the next slate.
            if report.events_seen and not report.events_matched:
                raise RuntimeError(
                    f"{report.events_seen} event(s) returned and none matched a "
                    f"{args.sport} game in season {season}."
                )
    except OddsQuotaError as exc:
        log.error("Out of credits: %s", exc)
        return 3
    except Exception as exc:
        log.error("Game odds capture failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
