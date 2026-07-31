"""The Odds API adapter.

PROVIDER-SPECIFIC (CLAUDE.md §9.1). Implements `OddsAdapter` against
api.the-odds-api.com v4.

Two structural facts about this provider drive the whole design:

  1. **Player props are only served per-event.** The bulk `/odds` endpoint
     carries game-level markets; player props require
     `/events/{id}/odds`, one call per game. Billing is per market per region
     per event, so a full FBS slate is roughly `games x markets` credits — a
     budget question, not a code question, which is why `probe_odds` measures
     the real cost before any ingest job is written.
  2. **One-sided prices are normal, not exceptional.** Books frequently post a
     longshot anytime-TD "Yes" with no "No". Those quotes are kept, flagged
     `is_two_way = False`, and must reach the de-vig as a NULL — a one-sided
     price cannot be de-vigged, and inventing the other side would manufacture
     edge out of nothing.

Parsing is exposed as a module-level function so it can be tested against
captured payloads without a network call, and so the probe can report
diagnostics the adapter protocol has no place for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from worker.adapters.odds.base import (
    BookPrice,
    OddsEvent,
    PropQuote,
    QuotaSnapshot,
)
from worker.adapters.odds.http import OddsHttpClient
from worker.adapters.odds.markets import (
    BINARY_LINE,
    NCAAF_SPORT_KEY,
    OVER_LABELS,
    UNDER_LABELS,
    our_key,
    provider_keys,
)
from worker.logging_setup import get_logger

log = get_logger(__name__)

ADAPTER_NAME = "theoddsapi"
DEFAULT_REGIONS = "us"
DEFAULT_ODDS_FORMAT = "american"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class ParseDiagnostics:
    """What the payload actually contained, including what we could not use.

    Exists because the probe's job is to measure coverage honestly. A parser
    that silently drops what it does not understand would report clean results
    from a payload it half-read — the exact failure mode that cost us 16,221
    roster rows in Phase 2.
    """

    bookmakers: set[str] = field(default_factory=set)
    markets_seen: set[str] = field(default_factory=set)
    markets_unmapped: set[str] = field(default_factory=set)
    outcomes_total: int = 0
    outcomes_unparsed: int = 0
    quotes_two_way: int = 0
    quotes_one_sided: int = 0


def parse_event_odds(
    payload: dict[str, Any],
) -> tuple[list[PropQuote], ParseDiagnostics]:
    """Turn one event-odds response into PropQuotes keyed by OUR market keys.

    Outcomes are grouped by (market, player, line): a book's Over and Under for
    the same player at the same number are two halves of ONE two-way price, and
    only a two-way price can be de-vigged. Alternate lines from the same book
    therefore surface as separate BookPrice entries rather than being collapsed,
    since choosing a "main" line is an ingest policy decision, not a parsing one.
    """
    diagnostics = ParseDiagnostics()
    event_id = str(payload.get("id") or "")

    # (our_market_key, player) -> (book, line) -> partially built price.
    # The inner key MUST carry the book: two books quoting the same player at
    # the same number are two separate two-way prices, and merging them would
    # pair one book's Over against another's Under — a fabricated market whose
    # de-vigged probability belongs to neither book.
    grouped: dict[tuple[str, str], dict[tuple[str, float], dict[str, Any]]] = {}

    for bookmaker in payload.get("bookmakers") or []:
        book_key = str(bookmaker.get("key") or "")
        book_name = str(bookmaker.get("title") or book_key)
        diagnostics.bookmakers.add(book_key)

        for market in bookmaker.get("markets") or []:
            provider_market = str(market.get("key") or "")
            diagnostics.markets_seen.add(provider_market)
            mapped = our_key(provider_market)
            if mapped is None:
                diagnostics.markets_unmapped.add(provider_market)
                continue

            for outcome in market.get("outcomes") or []:
                diagnostics.outcomes_total += 1

                label = str(outcome.get("name") or "").strip().lower()
                player = outcome.get("description") or outcome.get("participant")
                price = outcome.get("price")
                point = outcome.get("point")

                # A binary market carries no point; our schema stores it as
                # "over 0.5", so every market shares one representation.
                line = BINARY_LINE if point is None else float(point)

                if not player or price is None or label not in (
                    OVER_LABELS | UNDER_LABELS
                ):
                    diagnostics.outcomes_unparsed += 1
                    continue

                bucket = grouped.setdefault((mapped, str(player)), {})
                slot = bucket.setdefault(
                    (book_key, line),
                    {"book": book_key, "name": book_name, "over": None, "under": None},
                )
                if label in OVER_LABELS:
                    slot["over"] = int(price)
                else:
                    slot["under"] = int(price)

    quotes: list[PropQuote] = []
    for (market_key, player), by_book_line in grouped.items():
        prices = [
            BookPrice(
                sportsbook_key=str(slot["book"]),
                sportsbook_name=str(slot["name"]),
                line=line,
                over_price=slot["over"],
                under_price=slot["under"],
            )
            for (_book, line), slot in sorted(by_book_line.items())
        ]
        for price in prices:
            if price.is_two_way:
                diagnostics.quotes_two_way += 1
            else:
                diagnostics.quotes_one_sided += 1
        quotes.append(
            PropQuote(
                event_id=event_id,
                market_key=market_key,
                player_name=player,
                prices=prices,
            )
        )

    return quotes, diagnostics


class TheOddsApiAdapter:
    """Live adapter. One instance holds one HTTP client and its usage counter.

    Satisfies `OddsAdapter` structurally rather than by inheritance — that is the
    point of a Protocol, and it keeps a future provider free to be written
    without importing our base class. The test suite asserts conformance.
    """

    name = ADAPTER_NAME

    def __init__(
        self,
        api_key: str,
        *,
        sport_key: str = NCAAF_SPORT_KEY,
        regions: str = DEFAULT_REGIONS,
        client: OddsHttpClient | None = None,
    ) -> None:
        self.sport_key = sport_key
        self.regions = regions
        self._client = client or OddsHttpClient(api_key)

    @property
    def quota(self) -> QuotaSnapshot:
        return self._client.quota

    @property
    def call_count(self) -> int:
        return self._client.call_count

    def list_sports(self) -> list[dict[str, Any]]:
        """All sports on this plan. Documented as free — the probe verifies."""
        return self._client.get("/sports", all="true") or []

    def list_events(self, *, days_ahead: int | None = None) -> list[OddsEvent]:
        payload = self._client.get(f"/sports/{self.sport_key}/events") or []
        events = [
            OddsEvent(
                event_id=str(item.get("id") or ""),
                sport_key=str(item.get("sport_key") or self.sport_key),
                commence_time=_parse_time(item.get("commence_time")),
                home_team=str(item.get("home_team") or ""),
                away_team=str(item.get("away_team") or ""),
                raw=item,
            )
            for item in payload
        ]
        if days_ahead is None:
            return events

        now = datetime.now().astimezone()
        cutoff = now.timestamp() + days_ahead * 86400
        return [
            e
            for e in events
            if e.commence_time is None or e.commence_time.timestamp() <= cutoff
        ]

    def fetch_props_raw(
        self, event_id: str, market_keys: list[str] | None = None
    ) -> dict[str, Any]:
        """Raw event-odds payload, for the probe and for cached replay."""
        return self._client.get(
            f"/sports/{self.sport_key}/events/{event_id}/odds",
            regions=self.regions,
            markets=",".join(provider_keys(market_keys)),
            oddsFormat=DEFAULT_ODDS_FORMAT,
        ) or {}

    def fetch_props(
        self, event_id: str, market_keys: list[str] | None = None
    ) -> list[PropQuote]:
        quotes, diagnostics = parse_event_odds(
            self.fetch_props_raw(event_id, market_keys)
        )
        if diagnostics.markets_unmapped:
            log.warning(
                "Event %s returned unmapped market(s): %s",
                event_id,
                sorted(diagnostics.markets_unmapped),
            )
        return quotes

    # -- historical --------------------------------------------------------
    def historical_events(self, iso_timestamp: str) -> dict[str, Any]:
        """Event list as it stood at a past moment.

        This is the endpoint that decides whether EDGE can be backtested at all.
        Calibration needs no odds; edge does. If this is not on the plan, the
        Phase 3 report evaluates model calibration only and edge validation waits
        for live weeks to accumulate.
        """
        return self._client.get(
            f"/historical/sports/{self.sport_key}/events", date=iso_timestamp
        ) or {}

    def historical_props_raw(
        self,
        event_id: str,
        iso_timestamp: str,
        market_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._client.get(
            f"/historical/sports/{self.sport_key}/events/{event_id}/odds",
            date=iso_timestamp,
            regions=self.regions,
            markets=",".join(provider_keys(market_keys)),
            oddsFormat=DEFAULT_ODDS_FORMAT,
        ) or {}
