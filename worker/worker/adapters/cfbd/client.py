"""CollegeFootballData API client wrapper.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). Everything CFBD-shaped belongs under this
package; the NFL build replaces this layer and keeps worker/core untouched.

Beyond authentication, this module owns the three things that stand between a
several-hundred-call backfill and a suspended API key:

  * **Pacing.** A minimum interval between requests, so a tight backfill loop
    does not look like an attack.
  * **Backoff.** Retries on 429 and 5xx with exponential delay and jitter, on a
    heavier curve for 429, and honouring Retry-After only when it asks for
    LONGER than that curve.
  * **Caching.** Completed-season responses are immutable, so re-running the
    split engine while refining play attribution costs zero quota.

`fetch()` returns plain dicts rather than cfbd model objects. That is
deliberate: it keeps ingest code free of the vendor's model classes (so the
sport seam in §3 stays clean), and it is what makes responses JSON-cacheable in
the first place.
"""

from __future__ import annotations

import random
import time
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import cfbd

from worker.adapters.cfbd.cache import ResponseCache
from worker.config import get_settings
from worker.logging_setup import get_logger

log = get_logger(__name__)

# Retry on transient conditions only. A 401/403/404 will never succeed on retry
# and must surface immediately rather than being masked by a slow retry loop.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

DEFAULT_MIN_INTERVAL = 0.12  # seconds between requests (~8/sec ceiling)
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE = 1.5  # seconds; doubled each attempt
MAX_BACKOFF = 60.0

# A 429 gets its own, much heavier curve: 5, 10, 20, 40, 60 (capped) — over two
# minutes of waiting across the five attempts, against ~35s for the default
# base. That is sized from a measurement, not a guess. On 2026-08-17 the chain's
# second job drew a 429 on its FIRST request, 74 seconds after the previous job
# had made its last one — so CFBD's burst penalty outlives more than a minute of
# total silence, and a retry curve that gives up inside 35s cannot clear it.
# 5xx keeps the shorter base: a bad gateway is usually gone in a second, and
# there is no penalty box to sit out.
RATE_LIMIT_BACKOFF_BASE = 5.0


def build_configuration(api_key: str | None = None) -> cfbd.Configuration:
    """Build an authenticated CFBD Configuration.

    The client library changed how the bearer token is supplied between major
    versions (`access_token=` in v5, `api_key`/`api_key_prefix` dicts before
    that). requirements.txt pins v5, but the fallback keeps a version bump from
    turning into a silent auth failure at 3am in a cron job.
    """
    key = api_key or get_settings().require_cfbd_api_key()

    try:
        return cfbd.Configuration(access_token=key)
    except TypeError:  # pragma: no cover — pre-v5 client
        log.warning(
            "cfbd.Configuration(access_token=...) not supported; "
            "falling back to the legacy api_key form."
        )
        configuration = cfbd.Configuration()
        configuration.api_key["Authorization"] = key
        configuration.api_key_prefix["Authorization"] = "Bearer"
        return configuration


def _jsonable(value: Any) -> Any:
    """Coerce a value into something JSON round-trips unchanged.

    This is load-bearing, not cosmetic. `to_dict()` leaves enum members and
    datetimes as Python objects (`SeasonType.REGULAR`, not `"regular"`). Writing
    those to the JSON cache stringifies them, so a cached read would hand ingest
    code `"SeasonType.REGULAR"` where a live read handed it an enum — the same
    call returning different types depending on cache state, which is the kind
    of bug that only shows up on the second run.

    Normalizing here means live and cached responses are byte-identical, and
    ingest sees clean primitives either way.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    return value


def _to_rows(result: Any) -> list[dict[str, Any]]:
    """Normalize any CFBD response into a list of JSON-safe plain dicts."""
    if result is None:
        return []
    if not isinstance(result, list):
        result = [result]
    rows = []
    for item in result:
        if isinstance(item, dict):
            row = item
        elif hasattr(item, "to_dict"):
            row = item.to_dict()
        else:
            row = vars(item)
        rows.append({k: _jsonable(v) for k, v in row.items()})
    return rows


def _retry_after_seconds(exc: Exception) -> float | None:
    """Extract Retry-After from an API exception, if the server sent a USABLE one.

    A NON-POSITIVE VALUE IS TREATED AS ABSENT, and that is the whole point of
    this function's shape. CFBD's 429 carries `Retry-After: 0` — sitting in the
    middle of `Cache-Control: no-store, no-cache, must-revalidate, post-check=0,
    pre-check=0` and `Expires: Thu, 01 Jan 1970`, i.e. it is part of a
    boilerplate no-cache header block, not an instruction about when to come
    back. The same response body says "wait a few seconds and retry".

    Honouring that 0 literally is what turned the 2026-08-17 fix into a second
    outage: five retries fired inside a second, all into a still-hot rate
    limiter, and the job raised the same uncaught HTTP 429 as before. A server
    telling us to retry immediately after rate-limiting us is telling us
    nothing, so we fall through to our own backoff.
    """
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    try:
        value = headers.get("Retry-After")
    except AttributeError:
        return None
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _backoff_seconds(attempt: int, exc: Exception, status: int | None) -> float:
    """How long to wait before retry number `attempt` (1-based).

    Retry-After can only ever LENGTHEN the wait, never shorten it. Deferring to
    a server that asks for longer than our curve is politeness; letting one
    shorten it hands a rate limiter the power to talk us into hammering it, and
    a header does not have to be malicious to do that — CFBD's `Retry-After: 0`
    is simply boilerplate (see `_retry_after_seconds`).
    """
    base = RATE_LIMIT_BACKOFF_BASE if status == 429 else DEFAULT_BACKOFF_BASE
    delay = min(base * (2 ** (attempt - 1)), MAX_BACKOFF)
    # Jitter so parallel or repeated runs do not resynchronize their retries
    # into a thundering herd.
    delay *= 0.5 + random.random()  # noqa: S311 - not cryptographic

    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        delay = max(delay, min(retry_after, MAX_BACKOFF))
    return delay


class CfbdClient:
    """Context-managed CFBD client with pacing, backoff and response caching.

    Usage::

        with CfbdClient() as client:
            teams = client.fetch(
                "/teams/fbs", cfbd.TeamsApi, "get_fbs_teams", year=2024
            )
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        cache: ResponseCache | None = None,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._configuration = build_configuration(api_key)
        self._api_client: cfbd.ApiClient | None = None
        self.cache = cache if cache is not None else ResponseCache()
        self.min_interval = min_interval
        self.max_retries = max_retries

        self.call_count = 0  # live calls actually made (cache hits excluded)
        self._last_call_at = 0.0

    def __enter__(self) -> CfbdClient:
        self._api_client = cfbd.ApiClient(self._configuration)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._api_client is not None:
            self._api_client.close()
            self._api_client = None
        if self.call_count or self.cache.stats.hits:
            log.info(
                "CFBD session: %d live call(s), %s",
                self.call_count,
                self.cache.stats.summary(),
            )

    def call_uncached(
        self,
        endpoint: str,
        api_class: type[Any],
        method: str,
        **params: Any,
    ) -> Any:
        """Make one paced, retried call and return the vendor result unchanged.

        The uncached sibling of `fetch()`, for endpoints whose whole value is
        that they are read fresh. `/info` is the case that matters: it reports
        the remaining quota, and a cached quota reading is worse than no quota
        reading, because it would report yesterday's headroom as today's and the
        guard built on it would wave through a job that cannot afford to run.

        THIS REPLACED A BARE `api()` HELPER, AND THE REPLACEMENT IS THE POINT.
        That helper returned the vendor API object for the caller to invoke
        directly, which meant its calls skipped pacing, backoff and retry. Its
        one and only user was `fetch_account_status`, the account preflight —
        so the single unprotected call in the codebase was also the FIRST call
        every ingest job made.

        On 2026-08-17 that combination took the Sunday chain down. The five jobs
        run back-to-back under one `&&`, so `ingest_stats` opened on a rate
        limiter that `ingest_reference` had just left hot, drew a 429 on the
        preflight, and exited 1 — killing `ingest_ratings`, `ingest_rankings`
        and `build_splits` behind it. CFBD's own advice in that response body is
        "wait a few seconds and retry", which is exactly what `_call_with_retry`
        does and exactly what this call could not reach.

        Returning results raw rather than through `_to_rows()` is deliberate:
        `/info` is a single object, not a row set, and normalising it would make
        the caller unwrap a one-item list for no gain.
        """
        if self._api_client is None:
            raise RuntimeError("CfbdClient must be used as a context manager")

        clean = {k: v for k, v in params.items() if v is not None}
        label = f"{endpoint} {clean}" if clean else endpoint
        bound = getattr(api_class(self._api_client), method)  # type: ignore[call-arg]
        return self._call_with_retry(bound, clean, label)

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if self._last_call_at and elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_at = time.monotonic()

    def _call_with_retry(self, bound_method: Any, params: dict[str, Any], label: str) -> Any:
        attempt = 0
        waited = 0.0
        while True:
            self._pace()
            try:
                result = bound_method(**params)
                self.call_count += 1
                return result
            except Exception as exc:
                status = getattr(exc, "status", None)
                if status not in RETRYABLE_STATUSES or attempt >= self.max_retries:
                    # Count it: a failed call still consumes quota server-side.
                    self.call_count += 1
                    # SAY WHAT THIS 429 IS, in the log, in one line. What the
                    # cfbd exception prints is a wall of response headers, and
                    # twice now that has had to be read by someone deciding
                    # whether the key had run out of calls. It had not: this is
                    # the burst limiter, and CFBD's own body says so.
                    if status == 429:
                        log.error(
                            "%s -> HTTP 429 after %d retries over %.0fs. This is "
                            "CFBD's BURST limit, NOT the monthly quota — the key "
                            "has calls left. Something upstream is calling too "
                            "fast, or another product on this shared key is.",
                            label,
                            attempt,
                            waited,
                        )
                    raise

                attempt += 1
                delay = _backoff_seconds(attempt, exc, status)
                waited += delay

                self.call_count += 1
                log.warning(
                    "%s -> HTTP %s; retry %d/%d in %.1fs",
                    label,
                    status,
                    attempt,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)

    def fetch(
        self,
        endpoint: str,
        api_class: type[Any],
        method: str,
        *,
        max_age: float | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Fetch an endpoint as plain dicts, via cache where possible.

        `endpoint` is a stable label (e.g. "/games") used for the cache key and
        logs — it does not have to match the URL exactly, but it must be
        consistent, since changing it invalidates every cached entry for that
        call.

        `max_age` in seconds; None means the cache never expires. Use None for
        completed seasons, which are immutable, and a short value for live
        in-week data that must not be served stale.
        """
        # Drop None params so they neither reach the client nor perturb the
        # cache key — fetch(week=None) must hit the same entry as fetch().
        clean = {k: v for k, v in params.items() if v is not None}

        cached = self.cache.get(endpoint, clean, max_age)
        if cached is not None:
            log.debug("cache hit %s %s (%d rows)", endpoint, clean, len(cached))
            return cached

        if self._api_client is None:
            raise RuntimeError("CfbdClient must be used as a context manager")

        label = f"{endpoint} {clean}" if clean else endpoint
        bound = getattr(api_class(self._api_client), method)  # type: ignore[call-arg]

        result = self._call_with_retry(bound, clean, label)
        rows = _to_rows(result)

        self.cache.put(endpoint, clean, rows)
        log.info("fetched %s -> %d rows", label, len(rows))
        return rows
