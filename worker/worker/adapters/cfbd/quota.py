"""CFBD account status: tier, entitlements and remaining monthly calls.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3).

CFBD states that exceeding the monthly quota can get a key suspended, so the
backfill must know where it stands *before* it starts rather than discovering
the ceiling by being cut off partway through. `/info` reports the tier, the
per-feature entitlements and the remaining call count in a single request, which
makes it both the cheapest possible preflight and the most informative one.

Two things this module exists to prevent:

  * Starting a several-hundred-call backfill with fewer calls remaining than it
    needs, and dying halfway with the database in a partially-loaded state.
  * Silently running on a downgraded key. The quota is a SHARED POOL across
    CFB and CBB products, so another consumer of the same key can eat the
    budget without anything in this repo changing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import cfbd

from worker.logging_setup import get_logger

log = get_logger(__name__)

# Features this project depends on, and what needs them. Checked against the
# `features` map returned by /info so a downgrade is reported as a named
# capability rather than a mystery 401 three jobs later.
REQUIRED_FEATURES = {
    "weather": "venue/weather ingest (CLAUDE.md §4)",
}

OPTIONAL_FEATURES = {
    "livePlayByPlay": "Saturday in-week refresh",
    "adjustedMetrics": "cross-checks for the opponent adjustment",
}


class QuotaError(RuntimeError):
    """Raised when the account cannot support the work about to be attempted."""


@dataclass(frozen=True)
class AccountStatus:
    """Snapshot of what this CFBD key is currently entitled to."""

    tier_name: str | None
    patron_level: int | None
    monthly_limit: int | None
    remaining_calls: int | None
    used_calls: int | None
    reset_at: datetime | str | None
    shared_pool: bool | None
    products: list[str] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=dict)

    @property
    def missing_required_features(self) -> list[str]:
        return [name for name in REQUIRED_FEATURES if not self.features.get(name)]

    @property
    def missing_optional_features(self) -> list[str]:
        return [name for name in OPTIONAL_FEATURES if not self.features.get(name)]

    def summary(self) -> str:
        tier = self.tier_name or f"patron level {self.patron_level}"
        if self.remaining_calls is None or self.monthly_limit is None:
            return f"{tier}, quota unknown"
        pct = 100.0 * self.remaining_calls / self.monthly_limit if self.monthly_limit else 0.0
        return (
            f"{tier}, {self.remaining_calls:,}/{self.monthly_limit:,} calls "
            f"remaining ({pct:.0f}%), resets {self.reset_at}"
        )


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return vars(obj)


def fetch_account_status(client: Any) -> AccountStatus:
    """Read account status from /info. Costs exactly one API call.

    `client` is an open CfbdClient. Taking it as an argument rather than opening
    one keeps this usable inside a job that already holds a client, so a
    preflight does not cost a second connection.

    GOES THROUGH `call_uncached`, NOT AROUND IT. Every ingest job calls this
    before its first real request, so whatever protection this one call has is
    the protection the whole job's opening move has. It previously used a bare
    `api()` helper with no pacing, backoff or retry, and on 2026-08-17 a 429
    here ended the Sunday chain three jobs early. Uncached rather than `fetch()`
    because the remaining-call count is the entire point of the request — see
    `CfbdClient.call_uncached`.
    """
    raw = _as_dict(client.call_uncached("/info", cfbd.InfoApi, "get_user_info"))

    features = raw.get("features") or {}
    if not isinstance(features, dict):
        features = _as_dict(features)

    products = raw.get("products") or []
    if not isinstance(products, list):
        products = list(products)

    return AccountStatus(
        tier_name=raw.get("tierName"),
        patron_level=raw.get("patronLevel"),
        monthly_limit=raw.get("monthlyLimit"),
        remaining_calls=raw.get("remainingCalls"),
        used_calls=raw.get("usedCalls"),
        reset_at=raw.get("resetAt"),
        shared_pool=raw.get("sharedPool"),
        products=[str(p) for p in products],
        features={str(k): bool(v) for k, v in features.items()},
    )


def require_capacity(
    status: AccountStatus,
    estimated_calls: int,
    *,
    safety_margin: float = 1.25,
) -> None:
    """Raise QuotaError unless the account can afford `estimated_calls`.

    The margin exists because call estimates are lower bounds: retries, pagination
    and per-week fan-out all push actual usage above the plan. Refusing to start
    is strictly better than a partial backfill, which leaves the database in a
    state no row count can be trusted to describe.
    """
    if status.remaining_calls is None:
        log.warning(
            "CFBD did not report remaining calls; proceeding without a quota guard."
        )
        return

    needed = int(estimated_calls * safety_margin)
    if status.remaining_calls < needed:
        raise QuotaError(
            f"Refusing to start: this job needs about {estimated_calls:,} calls "
            f"({needed:,} with a {safety_margin:g}x safety margin) but only "
            f"{status.remaining_calls:,} remain of {status.monthly_limit:,}. "
            f"Quota resets {status.reset_at}. "
            "Re-run after the reset, narrow the season range, or rely on the "
            "response cache for anything already fetched."
        )

    log.info(
        "Quota preflight OK: need ~%s (x%g margin = %s), %s remaining.",
        f"{estimated_calls:,}",
        safety_margin,
        f"{needed:,}",
        f"{status.remaining_calls:,}",
    )


def warn_on_missing_features(status: AccountStatus) -> None:
    """Log named warnings for entitlements this project relies on."""
    for name in status.missing_required_features:
        log.error(
            "CFBD feature %r is NOT enabled on this key — needed for %s.",
            name,
            REQUIRED_FEATURES[name],
        )
    for name in status.missing_optional_features:
        log.warning(
            "CFBD feature %r is not enabled — %s will be degraded.",
            name,
            OPTIONAL_FEATURES[name],
        )
    if status.shared_pool and len(status.products) > 1:
        log.info(
            "Quota is a SHARED POOL across %s — another product on this key "
            "consumes the same %s calls.",
            ", ".join(status.products),
            f"{status.monthly_limit:,}" if status.monthly_limit else "monthly",
        )
