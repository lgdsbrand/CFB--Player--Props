"""Pluggable odds ingestion (CLAUDE.md §9.1).

The odds source is a configuration value, not a hardcoded import. Which adapter
runs comes from `app_config.odds_adapter`, so switching provider — or turning
lines off entirely — is a row edit rather than a deploy.
"""

from __future__ import annotations

from typing import Any

from worker.adapters.odds.base import (
    BookPrice,
    OddsAdapter,
    OddsAdapterError,
    OddsEvent,
    OddsPlanError,
    OddsQuotaError,
    PropQuote,
    QuotaSnapshot,
    SupportsHistorical,
)
from worker.adapters.odds.null import ADAPTER_NAME as NULL_ADAPTER_NAME
from worker.adapters.odds.null import NullOddsAdapter
from worker.adapters.odds.theoddsapi import ADAPTER_NAME as THEODDSAPI_ADAPTER_NAME
from worker.adapters.odds.theoddsapi import TheOddsApiAdapter

__all__ = [
    "BookPrice",
    "NullOddsAdapter",
    "OddsAdapter",
    "OddsAdapterError",
    "OddsEvent",
    "OddsPlanError",
    "OddsQuotaError",
    "PropQuote",
    "QuotaSnapshot",
    "SupportsHistorical",
    "TheOddsApiAdapter",
    "get_adapter",
]

KNOWN_ADAPTERS = (NULL_ADAPTER_NAME, THEODDSAPI_ADAPTER_NAME)


def get_adapter(name: str, **kwargs: Any) -> OddsAdapter:
    """Build the named adapter.

    Unknown names raise rather than falling back to the null adapter. A silent
    fallback would present "no lines available" identically to "your config has
    a typo", and those need different responses.
    """
    if name == NULL_ADAPTER_NAME:
        return NullOddsAdapter()
    if name == THEODDSAPI_ADAPTER_NAME:
        return TheOddsApiAdapter(**kwargs)
    raise OddsAdapterError(
        f"Unknown odds adapter {name!r}. Known adapters: {list(KNOWN_ADAPTERS)}. "
        "Set app_config.odds_adapter to one of these."
    )
