"""Odds adapter contract.

CLAUDE.md §9.1 leaves the odds source deliberately open: the client's key is for
The Odds API, but whether that plan covers NCAAF player props — and at what
quota cost — is unverified. Ingestion is therefore pluggable, and every concrete
source implements the protocol defined here.

Two properties this file is built around:

  * **The app degrades gracefully.** `NullOddsAdapter` is a real, selectable
    implementation, not an error path. With `app_config.odds_adapter = "none"`
    the board still shows model leans and simply carries no book line
    (CLAUDE.md §7).
  * **Rows stay attributable.** Every quote knows which adapter produced it, so
    changing provider does not orphan history — `player_prop_lines.source_adapter`
    exists for exactly this.

This module is provider-agnostic on purpose. The provider's market vocabulary
lives in `markets.py`; its HTTP shape lives in the concrete adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

# The `source_adapter` written on a development line that no book ever quoted.
# There is no synthetic *adapter* — nothing implements the protocol below and
# nothing fetches anything; `run_projections --synthetic-lines` writes the rows
# directly. The label lives here anyway because two jobs need to agree on it:
# the one that writes those rows and the one that must EVICT them when a real
# quote arrives for the same player and market. Two string literals that must
# match, defined in two places, is how that eviction quietly stops working.
SYNTHETIC_ADAPTER = "synthetic"


class OddsAdapterError(RuntimeError):
    """Raised when an odds provider cannot serve what was asked of it."""


class OddsPlanError(OddsAdapterError):
    """Raised when the failure is entitlement, not transport.

    Separated from the generic error because the remedy is completely different:
    a 401/403 on a market means the plan does not cover it, and no amount of
    retrying will change that. The probe job reports these as findings rather
    than crashes.
    """


class OddsQuotaError(OddsAdapterError):
    """Raised when the account is out of credits.

    MUST NOT be folded into OddsPlanError even though the provider returns the
    same HTTP 401 for both. They mean opposite things: entitlement says "this
    plan will never serve that", quota says "this plan serves it, just not until
    the allowance resets". Conflating them made the probe report
    "historical odds NOT available on this plan" for a plan we had direct
    evidence DOES carry historical — a wrong conclusion written into a memo
    whose entire job is to be right about that.

    The provider distinguishes them in the response body via `error_code`, so
    the classification is read from there rather than inferred from the status.
    """


@dataclass(frozen=True)
class QuotaSnapshot:
    """What the provider said about the account after the last request.

    The Odds API bills per market per region per event, so the *observed* cost
    of a call is the only trustworthy basis for a weekly budget — a formula
    derived from documentation is a guess. `last_cost` is the difference the
    provider reports for the request that just completed.
    """

    remaining: int | None = None
    used: int | None = None
    last_cost: int | None = None

    def summary(self) -> str:
        if self.remaining is None:
            return "quota unknown (provider sent no usage headers)"
        total = (
            self.remaining + self.used
            if self.remaining is not None and self.used is not None
            else None
        )
        base = f"{self.remaining:,} credits remaining"
        if total:
            base += f" of {total:,}"
        if self.last_cost is not None:
            base += f"; last call cost {self.last_cost}"
        return base


@dataclass(frozen=True)
class OddsEvent:
    """One schedulable game as the provider sees it.

    `home_team` and `away_team` are the PROVIDER's team strings, deliberately
    left unresolved. Mapping them onto our `teams` rows is a separate, fallible
    step that belongs to ingest, not to transport.
    """

    event_id: str
    sport_key: str
    commence_time: datetime | None
    home_team: str
    away_team: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class BookPrice:
    """One sportsbook's two-way price on one line.

    `over_price` / `under_price` are American odds and either may be None: books
    routinely post only the favoured side of a longshot anytime-TD market. That
    asymmetry is preserved rather than smoothed over, because a one-sided price
    CANNOT be de-vigged and must reach `devig_two_way` as a NULL so it is
    reported as "no book probability" instead of a fabricated edge.
    """

    sportsbook_key: str
    sportsbook_name: str
    line: float
    over_price: int | None
    under_price: int | None

    @property
    def is_two_way(self) -> bool:
        return self.over_price is not None and self.under_price is not None


@dataclass(frozen=True)
class PropQuote:
    """Every book's price on one player's one market for one game.

    `player_name` is the provider's raw string. Resolving it to a `player_id` is
    deliberately NOT done here: name matching across ~21k FBS players with
    duplicates, suffixes and nicknames is its own problem with its own failure
    modes, and burying it in the transport layer would make those failures
    invisible.
    """

    event_id: str
    market_key: str  # OUR markets.key, never the provider's
    player_name: str
    prices: list[BookPrice] = field(default_factory=list)

    @property
    def two_way_prices(self) -> list[BookPrice]:
        return [p for p in self.prices if p.is_two_way]


@runtime_checkable
class OddsAdapter(Protocol):
    """What every odds source must provide.

    Intentionally small. Anything richer (line movement, closing-line marking,
    player resolution) is built ON this in the ingest job, so a new provider
    only has to satisfy these three members.
    """

    name: str

    def list_events(self, *, days_ahead: int | None = None) -> list[OddsEvent]:
        """Upcoming games the provider knows about."""
        ...

    def fetch_props(
        self, event_id: str, market_keys: list[str]
    ) -> list[PropQuote]:
        """Player prop quotes for one event, keyed by OUR market keys."""
        ...

    @property
    def quota(self) -> QuotaSnapshot:
        """Most recent usage reading. Empty until a request has been made."""
        ...


@runtime_checkable
class SupportsHistorical(Protocol):
    """An adapter that can also answer "what was posted at time T".

    SEPARATE FROM `OddsAdapter` ON PURPOSE. Historical odds are a paid extra on
    The Odds API and may not exist at all on a future provider, so requiring
    them in the base protocol would make a perfectly usable live-only source
    fail to satisfy the contract. `backfill_odds` asks for this narrower
    protocol and refuses with an explanation when the configured adapter cannot
    serve it — including `NullOddsAdapter`, which deliberately cannot.

    Both methods return RAW payloads. Parsing belongs to the concrete adapter's
    module so a backfill can be replayed from a captured fixture without a
    network call.
    """

    name: str

    def historical_events(self, iso_timestamp: str) -> dict[str, Any]:
        """The event list as it stood at a past moment."""
        ...

    def historical_props_raw(
        self,
        event_id: str,
        iso_timestamp: str,
        market_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """One event's prop payload as it stood at a past moment."""
        ...

    @property
    def quota(self) -> QuotaSnapshot:
        ...
