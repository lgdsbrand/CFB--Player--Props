"""CollegeFootballData API client wrapper.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). Everything CFBD-shaped belongs under this
package; the NFL build replaces this layer and keeps worker/core untouched.

Beyond authentication, this module owns the three things that stand between a
several-hundred-call backfill and a suspended API key:

  * **Pacing.** A minimum interval between requests, so a tight backfill loop
    does not look like an attack.
  * **Backoff.** Retries on 429 and 5xx with exponential delay and jitter,
    honouring Retry-After when the server sends it.
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
from typing import Any, TypeVar

import cfbd

from worker.adapters.cfbd.cache import ResponseCache
from worker.config import get_settings
from worker.logging_setup import get_logger

log = get_logger(__name__)

ApiT = TypeVar("ApiT")

# Retry on transient conditions only. A 401/403/404 will never succeed on retry
# and must surface immediately rather than being masked by a slow retry loop.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

DEFAULT_MIN_INTERVAL = 0.12  # seconds between requests (~8/sec ceiling)
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE = 1.5  # seconds; doubled each attempt
MAX_BACKOFF = 60.0


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
    """Extract Retry-After from an API exception, if the server sent one."""
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
        return float(value)
    except (TypeError, ValueError):
        return None


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

    def api(self, api_class: type[ApiT]) -> ApiT:
        """Instantiate a CFBD API class bound to this client's credentials.

        Bypasses pacing, backoff and caching — use `fetch()` for ingest work.
        This remains for one-off calls such as the account preflight.
        """
        if self._api_client is None:
            raise RuntimeError("CfbdClient must be used as a context manager")
        return api_class(self._api_client)  # type: ignore[call-arg]

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if self._last_call_at and elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_at = time.monotonic()

    def _call_with_retry(self, bound_method: Any, params: dict[str, Any], label: str) -> Any:
        attempt = 0
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
                    raise

                attempt += 1
                delay = _retry_after_seconds(exc)
                if delay is None:
                    delay = min(
                        DEFAULT_BACKOFF_BASE * (2 ** (attempt - 1)), MAX_BACKOFF
                    )
                    # Jitter so parallel or repeated runs do not resynchronize
                    # their retries into a thundering herd.
                    delay *= 0.5 + random.random()  # noqa: S311 - not cryptographic

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
