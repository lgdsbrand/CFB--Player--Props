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

ALSO WRITES THE CURRENT OFFERS (migration 0089): every book's price per side
with its bet link, replaced per captured game, for the bet slip.

ALSO WRITES THE +EV LIST (migration 0087), and only this run can. Because
`game_odds` keeps only changes, its newest row cannot say whether a book is
still posting a price; this run's quotes can. So after writing, the run
computes each captured game's +EV wagers from exactly what it saw
(core/ev.py) and replaces that game's `game_ev_wagers` rows.

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
from datetime import UTC, datetime, timedelta

import psycopg

from worker.adapters.odds import OddsQuotaError
from worker.adapters.odds.base import GameBookMarket, GameEventOdds
from worker.adapters.odds.markets import sport_key_for
from worker.adapters.odds.theoddsapi import (
    ADAPTER_NAME,
    GAME_MARKETS,
    GAME_REGIONS,
    PERIOD_MARKETS,
    PERIOD_REGIONS,
    TheOddsApiAdapter,
)
from worker.config import ConfigError, get_settings
from worker.core.ev import EvQuote, EvWager, ev_wagers
from worker.core.name_match import TeamMatch, TeamResolver
from worker.db import connect, pipeline_run, resolve_seasons, set_rows_written
from worker.jobs.ingest_odds import load_games, load_teams, match_event_to_game
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "ingest_game_odds"

# The bulk endpoint serves full-game markets only. 1H/1Q are per-event and
# billed like player props; `run_periods` captures them into the same table
# (0074 already allows the periods), on a schedule of their own.
PERIOD = "full"
PERIODS = ("h1", "q1")

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
    period: str = PERIOD
    # Bet links (migration 0089), per side, and the game's page. Never part of
    # `price_key`: a link changing is not the price moving.
    home_link: str | None = None
    away_link: str | None = None
    over_link: str | None = None
    under_link: str | None = None
    event_link: str | None = None

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
    # Period capture only (see `run_periods`).
    events_already_captured: int = 0
    events_asked: int = 0
    books: set[str] = field(default_factory=set)
    rows_written: int = 0
    rows_unchanged: int = 0
    ev_wagers: int = 0
    rejected: Counter = field(default_factory=Counter)

    def render(self) -> str:
        lines = [
            f"events seen      {self.events_seen}",
            f"  matched        {self.events_matched}",
            f"  started (skip) {self.events_started}",
            f"  unmatched      {len(self.events_unmatched)}",
            *(
                [
                    f"  asked (billed) {self.events_asked}",
                    f"  held, not closing (free) {self.events_already_captured}",
                ]
                if self.events_asked or self.events_already_captured
                else []
            ),
            f"books            {len(self.books)}: {', '.join(sorted(self.books))}",
            f"rows written     {self.rows_written}",
            f"rows unchanged   {self.rows_unchanged}",
            f"+EV wagers       {self.ev_wagers}",
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
            period=market.period,
            event_link=market.event_link,
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
            over_link=o.link if o else None,
            under_link=u.link if u else None,
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
    links = {
        "home_link": home.link if home else None,
        "away_link": away.link if away else None,
    }

    if market.market == "h2h":
        return row(home_price=home_price, away_price=away_price, **links), None

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
    return row(line=line, home_price=home_price, away_price=away_price, **links), None


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
        captured: set[int] = set()
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

            captured.add(int(game["id"]))
            side_of = side_resolver(event_odds, game, resolver)
            for market in event_odds.markets:
                row, reason = orient(market, side_of)
                if row is None:
                    report.rejected[reason] += 1
                    continue
                report.books.add(row.sportsbook_key)
                oriented.append((int(game["id"]), row))

        wagers = ev_rows(oriented)
        report.ev_wagers = sum(len(w) for w in wagers.values())
        if dry_run:
            report.rows_written = 0
            log.info("Dry run: %d quote(s) oriented, nothing written.", len(oriented))
            log.info("Provider quota: %s", adapter.quota.summary())
            return report

        write_changed(conn, oriented, now, report)
        write_ev_wagers(conn, wagers, now)
        write_game_offers(conn, oriented, captured, (PERIOD,), now)

    log.info("Provider quota: %s", adapter.quota.summary())
    return report


def write_changed(
    conn: psycopg.Connection,
    oriented: list[tuple[int, GameOddsRow]],
    now: datetime,
    report: GameOddsReport,
) -> None:
    """Write the quotes whose price moved since the book's last row, and commit."""
    book_ids = ensure_books(conn, [r for _, r in oriented])
    stored = latest_prices(conn, sorted({g for g, _ in oriented}))
    changed: list[tuple] = []
    for game_id, row in oriented:
        book_id = book_ids[row.sportsbook_key]
        if stored.get((game_id, book_id, row.period, row.market)) == row.price_key():
            report.rows_unchanged += 1
            continue
        changed.append((
            game_id, book_id, row.period, row.market, row.line,
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


def ev_rows(oriented: list[tuple[int, GameOddsRow]]) -> dict[int, list[EvWager]]:
    """Each captured game's +EV wagers, from this run's full-game quotes only.

    Every captured game gets an entry, empty when nothing beats the fair
    price, so the write below clears a game whose last +EV price has gone.
    """
    by_game: dict[int, list[EvQuote]] = {}
    for game_id, row in oriented:
        quotes = by_game.setdefault(game_id, [])
        if row.period != PERIOD:
            continue
        first, second = (
            (row.over_price, row.under_price) if row.market == "totals"
            else (row.home_price, row.away_price)
        )
        quotes.append(EvQuote(row.sportsbook_key, row.market, row.line, first, second))
    return {game_id: ev_wagers(quotes) for game_id, quotes in by_game.items()}


def write_ev_wagers(
    conn: psycopg.Connection, wagers: dict[int, list[EvWager]], now: datetime
) -> None:
    """Replace the captured games' +EV rows, in one transaction."""
    if not wagers:
        return
    with conn.cursor() as cur:
        cur.execute("select id, key from sportsbooks")
        book_ids = {r["key"]: int(r["id"]) for r in cur.fetchall()}
        cur.execute(
            "delete from game_ev_wagers where game_id = any(%s) and period = %s",
            (list(wagers), PERIOD),
        )
        cur.executemany(
            """
            insert into game_ev_wagers
              (game_id, period, market, sportsbook_id, side, line, price,
               fair_prob, ev, captured_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (game_id, PERIOD, w.market, book_ids[w.book_key], w.side, w.line,
                 w.price, round(w.fair_prob, 5), round(w.ev, 5), now)
                for game_id, rows in wagers.items()
                for w in rows
            ],
        )
    conn.commit()


def offer_rows(row: GameOddsRow) -> list[tuple[str, int, str | None]]:
    """(side, price, link) for each priced side of one oriented quote."""
    sides = (
        (("over", row.over_price, row.over_link), ("under", row.under_price, row.under_link))
        if row.market == "totals"
        else (("home", row.home_price, row.home_link), ("away", row.away_price, row.away_link))
    )
    return [(side, price, link) for side, price, link in sides if price is not None]


def write_game_offers(
    conn: psycopg.Connection,
    oriented: list[tuple[int, GameOddsRow]],
    captured: set[int],
    periods: tuple[str, ...],
    now: datetime,
) -> None:
    """Replace the captured games' current offers in these periods, and commit.

    Every captured game is cleared, including one whose quotes were all
    refused, so the slip never keeps a price this run did not see. Rows for
    games over a day past kickoff go too.
    """
    if not captured:
        return
    book_ids = ensure_books(conn, [r for _, r in oriented])
    rows = [
        (game_id, row.period, row.market, book_ids[row.sportsbook_key], side,
         row.line, price, link, row.event_link, now)
        for game_id, row in oriented
        if row.period in periods
        for side, price, link in offer_rows(row)
    ]
    with conn.cursor() as cur:
        cur.execute(
            "delete from game_offers where game_id = any(%s) and period = any(%s)",
            (sorted(captured), list(periods)),
        )
        cur.execute(
            "delete from game_offers o using games g where g.id = o.game_id "
            "and g.start_date < now() - interval '1 day'"
        )
        if rows:
            cur.executemany(
                """
                insert into game_offers
                  (game_id, period, market, sportsbook_id, side, line, price,
                   link, event_link, captured_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                """,
                rows,
            )
    conn.commit()


def games_with_period_odds(conn: psycopg.Connection, game_ids: list[int]) -> set[int]:
    """Games that already hold at least one first-half or first-quarter price."""
    if not game_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "select distinct game_id from game_odds "
            "where game_id = any(%s) and period = any(%s)",
            (game_ids, list(PERIODS)),
        )
        return {int(r["game_id"]) for r in cur.fetchall()}


def run_periods(
    *,
    season: int,
    adapter: TheOddsApiAdapter,
    sport: str = "cfb",
    close_window_minutes: int = 60,
    event_limit: int = 120,
    dry_run: bool = False,
    now: datetime | None = None,
) -> GameOddsReport:
    """First-half and first-quarter lines, OPEN-AND-CLOSE, one call per game.

    The client asked for 1Q and 1H from FanDuel and DraftKings (likely Caesars)
    beside the full game. They are per-event on this provider and billed per
    market returned, so a game is asked about TWICE, the rule the props crons
    follow (`ingest_odds --close-window-minutes`): by the first run that finds
    periods posted for it, and by the run whose window holds its kickoff.
    Every other hour it is skipped without a call; the event list is free and
    an event with nothing posted bills nothing. At most 6 credits per call.
    """
    report = GameOddsReport()
    now = now or datetime.now(UTC)
    window = timedelta(minutes=close_window_minutes)

    events = adapter.list_events()
    report.events_seen = len(events)

    with connect() as conn:
        resolver = load_teams(conn, sport=sport)
        games = load_games(conn, season, None, sport=sport)
        if not games:
            log.warning("No %s games stored for season %s.", sport, season)
            return report

        matched: list[tuple[object, dict]] = []
        for event in events:
            hit = match_event_to_game(event, games, resolver)
            if hit is None:
                report.events_unmatched.append(f"{event.away_team} @ {event.home_team}")
                continue
            report.events_matched += 1
            matched.append((event, hit[0]))

        held = games_with_period_odds(conn, [int(g["id"]) for _, g in matched])

        oriented: list[tuple[int, GameOddsRow]] = []
        asked: set[int] = set()
        for event, game in matched:
            kickoff = event.commence_time or game.get("start_date")
            if kickoff is not None and kickoff <= now:
                report.events_started += 1
                continue
            closing = kickoff is not None and kickoff <= now + window
            if int(game["id"]) in held and not closing:
                report.events_already_captured += 1
                continue
            if report.events_asked >= event_limit:
                log.warning(
                    "Event limit %d reached; the rest wait for the next run.", event_limit
                )
                break
            report.events_asked += 1
            asked.add(int(game["id"]))
            for event_odds in adapter.fetch_period_odds(event.event_id):
                side_of = side_resolver(event_odds, game, resolver)
                for market in event_odds.markets:
                    if market.period not in PERIODS:
                        continue
                    row, reason = orient(market, side_of)
                    if row is None:
                        report.rejected[reason] += 1
                        continue
                    report.books.add(row.sportsbook_key)
                    oriented.append((int(game["id"]), row))

        if dry_run:
            log.info("Dry run: %d quote(s) oriented, nothing written.", len(oriented))
        else:
            if oriented:
                write_changed(conn, oriented, now, report)
            write_game_offers(conn, oriented, asked, PERIODS, now)

    log.info(
        "Requested %s in region %s. Provider quota: %s",
        ",".join(PERIOD_MARKETS), PERIOD_REGIONS, adapter.quota.summary(),
    )
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
