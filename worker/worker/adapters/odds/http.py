"""Minimal HTTP transport for The Odds API.

Uses `urllib.request` from the standard library rather than `requests` or
`httpx`. CLAUDE.md §0 requires a justification for every new dependency, and
this client needs exactly one thing — an authenticated GET returning JSON plus
response headers. That does not warrant adding a package to what Render installs
on every deploy.

**Key hygiene.** The Odds API authenticates via an `apiKey` QUERY PARAMETER, not
a header. That makes the credential part of the URL, and a URL is the single
most likely thing to end up in a log line, an exception message or a cache key.
Every path out of this module routes through `redact()`. Treat that as a hard
rule, not a nicety (CLAUDE.md §0).
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from worker.adapters.odds.base import (
    OddsAdapterError,
    OddsPlanError,
    OddsQuotaError,
    QuotaSnapshot,
)
from worker.logging_setup import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"

# Retry only what a retry can fix. A 401/403 (bad key, plan does not cover this)
# and a 422 (market not offered) are terminal, and masking them behind a slow
# retry loop would turn a clear entitlement answer into a timeout.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
TERMINAL_PLAN_STATUSES = frozenset({401, 403, 422})

# The provider returns HTTP 401 for BOTH "your plan does not include this" and
# "you are out of credits". Those mean opposite things — one is permanent, the
# other clears at the next reset — and only the response body tells them apart.
QUOTA_ERROR_CODES = frozenset({"OUT_OF_USAGE_CREDITS"})

DEFAULT_MIN_INTERVAL = 0.15
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE = 1.5
MAX_BACKOFF = 30.0
DEFAULT_TIMEOUT = 30.0

_APIKEY_RE = re.compile(r"(apiKey=)[^&\s]+", re.IGNORECASE)


def redact(text: str) -> str:
    """Strip the API key out of anything about to be logged or raised."""
    return _APIKEY_RE.sub(r"\1<redacted>", text)


def _int_header(headers: Any, name: str) -> int | None:
    raw = headers.get(name) if headers else None
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _error_code(body: str) -> str | None:
    """Pull the provider's machine-readable error code out of a response body.

    Returns None when the body is not the JSON shape we expect, so an unparsable
    error degrades to the generic classification rather than being mislabelled.
    """
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("error_code")
    return str(code) if code else None


def _quota_from_headers(headers: Any) -> QuotaSnapshot:
    """Read usage from the response headers.

    These headers are the ONLY reliable statement of what a call cost. The
    documented "markets x regions" formula is a model of the billing, not the
    billing itself, and the whole point of the probe is to measure rather than
    assume.
    """
    return QuotaSnapshot(
        remaining=_int_header(headers, "x-requests-remaining"),
        used=_int_header(headers, "x-requests-used"),
        last_cost=_int_header(headers, "x-requests-last"),
    )


class OddsHttpClient:
    """Paced, retrying JSON GET client with usage accounting."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not api_key:
            raise OddsAdapterError(
                "ODDS_API_KEY is empty. Set it in .env — the odds probe cannot "
                "run without a key, and the key is never stored in the repo."
            )
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout

        self.call_count = 0
        self.quota = QuotaSnapshot()
        self._last_call_at = 0.0

    # -- internals ---------------------------------------------------------
    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if self._last_call_at and elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_at = time.monotonic()

    def _build_url(self, path: str, params: dict[str, Any]) -> str:
        clean = {k: v for k, v in params.items() if v is not None}
        clean["apiKey"] = self._api_key
        query = urllib.parse.urlencode(clean, doseq=True)
        return f"{self.base_url}/{path.lstrip('/')}?{query}"

    # -- public ------------------------------------------------------------
    def get(self, path: str, **params: Any) -> Any:
        """GET a JSON endpoint, updating `self.quota` from the response headers.

        Raises OddsPlanError for entitlement failures so callers can report
        "this plan does not cover X" as a finding instead of a stack trace.
        """
        url = self._build_url(path, params)
        safe_url = redact(url)

        attempt = 0
        while True:
            self._pace()
            try:
                request = urllib.request.Request(
                    url, headers={"Accept": "application/json"}
                )
                with urllib.request.urlopen(  # noqa: S310 - fixed https base
                    request, timeout=self.timeout
                ) as response:
                    self.call_count += 1
                    self.quota = _quota_from_headers(response.headers)
                    payload = json.loads(response.read().decode("utf-8"))
                log.debug("GET %s -> ok (%s)", safe_url, self.quota.summary())
                return payload

            except urllib.error.HTTPError as exc:
                self.call_count += 1
                self.quota = _quota_from_headers(getattr(exc, "headers", None))
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:  # pragma: no cover - body already consumed
                    pass

                if exc.code in TERMINAL_PLAN_STATUSES:
                    message = f"HTTP {exc.code} on {safe_url}: {redact(body)}"
                    if _error_code(body) in QUOTA_ERROR_CODES:
                        raise OddsQuotaError(message) from exc
                    raise OddsPlanError(message) from exc

                if exc.code not in RETRYABLE_STATUSES or attempt >= self.max_retries:
                    raise OddsAdapterError(
                        f"HTTP {exc.code} on {safe_url}: {redact(body)}"
                    ) from exc

                attempt += 1
                delay = self._backoff(exc, attempt)
                log.warning(
                    "%s -> HTTP %s; retry %d/%d in %.1fs",
                    safe_url,
                    exc.code,
                    attempt,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)

            # TimeoutError IS listed alongside URLError deliberately. A read
            # timeout from `urlopen(timeout=...)` is raised bare — it is not
            # wrapped in URLError — so it used to escape this handler entirely
            # and kill the caller on the first slow response. That is merely
            # annoying for a probe and expensive for `backfill_odds`, which can
            # be dozens of paid calls into a run when it happens.
            except (urllib.error.URLError, TimeoutError) as exc:
                self.call_count += 1
                reason = getattr(exc, "reason", None) or exc
                if attempt >= self.max_retries:
                    raise OddsAdapterError(
                        f"Network error on {safe_url}: {reason}"
                    ) from exc
                attempt += 1
                delay = self._backoff(None, attempt)
                log.warning(
                    "%s -> %s; retry %d/%d in %.1fs",
                    safe_url,
                    reason,
                    attempt,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)

    def _backoff(self, exc: Exception | None, attempt: int) -> float:
        retry_after = None
        headers = getattr(exc, "headers", None)
        if headers:
            try:
                retry_after = float(headers.get("Retry-After") or 0) or None
            except (TypeError, ValueError):
                retry_after = None
        if retry_after:
            return min(retry_after, MAX_BACKOFF)
        delay = min(DEFAULT_BACKOFF_BASE * (2 ** (attempt - 1)), MAX_BACKOFF)
        # Jitter so repeated runs do not resynchronize into a thundering herd.
        return delay * (0.5 + random.random())  # noqa: S311 - not cryptographic
