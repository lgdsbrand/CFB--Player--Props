"""Minimal JSON-POST transport for the AI providers.

Uses `urllib.request` from the standard library rather than adding an SDK or an
HTTP client. CLAUDE.md §0 requires a justification for every new dependency, and
what these providers need is one authenticated POST returning JSON. Both Gemini
and Grok are plain REST and Grok is OpenAI-compatible, so a vendor SDK would add
install weight on every Render deploy to wrap a single call. `worker/adapters/
odds/http.py` was written under the same constraint and has held.

**Key hygiene** (CLAUDE.md §0). Gemini authenticates with an `x-goog-api-key`
HEADER and Grok with an `Authorization: Bearer` header, so unlike the odds
provider the credential is not in the URL. That is safer, but not safe by
itself: urllib puts request headers into some exception reprs, so every string
leaving this module goes through `redact()`.
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from typing import Any

from worker.adapters.ai.base import (
    AiAdapterError,
    AiAuthError,
    AiRateLimitError,
)
from worker.logging_setup import get_logger

log = get_logger(__name__)

# Retry only what a retry can fix. 401/403 is the key or the entitlement and
# will still be wrong in ten seconds; 400 is a malformed request, which is our
# bug. 429 and 5xx are the ones worth waiting out.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
AUTH_STATUSES = frozenset({401, 403})

DEFAULT_MIN_INTERVAL = 0.1
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE = 2.0
MAX_BACKOFF = 60.0
DEFAULT_TIMEOUT = 60.0

# Matches an API key wherever it might surface: a bearer header, a Google key
# header, or a query parameter if a provider ever moves it there.
_SECRET_RE = re.compile(
    r"(Bearer\s+|x-goog-api-key['\"\s:=]+|key=|api[-_]?key['\"\s:=]+)[A-Za-z0-9_\-\.]{8,}",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """Strip anything key-shaped out of text about to be logged or raised."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}<redacted>", text)


class AiHttpClient:
    """Paced, retrying JSON POST client."""

    def __init__(
        self,
        *,
        headers: dict[str, str],
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._headers = {"Content-Type": "application/json", **headers}
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_call = 0.0

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON and return the decoded response, or raise."""
        body = json.dumps(payload).encode("utf-8")
        attempt = 0

        while True:
            self._pace()
            request = urllib.request.Request(
                url, data=body, headers=self._headers, method="POST"
            )
            try:
                with urllib.request.urlopen(  # noqa: S310 - fixed https hosts
                    request, timeout=self.timeout
                ) as response:
                    return json.loads(response.read().decode("utf-8"))

            except urllib.error.HTTPError as exc:
                detail = _safe_body(exc)
                status = exc.code

                if status in AUTH_STATUSES:
                    raise AiAuthError(
                        redact(
                            f"HTTP {status} from the AI provider — the key is "
                            f"rejected or not entitled to this model. {detail}"
                        )
                    ) from None

                if status not in RETRYABLE_STATUSES or attempt >= self.max_retries:
                    message = redact(f"HTTP {status} from the AI provider. {detail}")
                    if status == 429:
                        raise AiRateLimitError(message) from None
                    raise AiAdapterError(message) from None

                attempt += 1
                self._sleep_for(attempt, retry_after=exc.headers.get("Retry-After"))

            except urllib.error.URLError as exc:
                if attempt >= self.max_retries:
                    raise AiAdapterError(
                        redact(f"Could not reach the AI provider: {exc.reason}")
                    ) from None
                attempt += 1
                self._sleep_for(attempt)

            except json.JSONDecodeError as exc:
                # A 200 carrying something that is not JSON. Not retryable and
                # not an auth problem; surfacing it plainly beats guessing.
                raise AiAdapterError(
                    f"AI provider returned a non-JSON 200 response: {exc}"
                ) from None

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def _sleep_for(self, attempt: int, retry_after: str | None = None) -> None:
        """Back off, preferring the provider's own instruction when it gives one."""
        if retry_after:
            try:
                time.sleep(min(float(retry_after), MAX_BACKOFF))
                return
            except (TypeError, ValueError):
                pass
        # Jitter matters here: a weekly job fires ~1,700 requests, and a fleet
        # of retries synchronised to the same schedule re-creates the burst that
        # caused the rate limit.
        delay = min(DEFAULT_BACKOFF_BASE**attempt, MAX_BACKOFF)
        time.sleep(delay * (0.5 + random.random() / 2))  # noqa: S311 - not crypto


def _safe_body(exc: urllib.error.HTTPError) -> str:
    """Read an error body without letting the read itself raise."""
    try:
        return exc.read().decode("utf-8", errors="replace")[:400]
    except Exception:  # pragma: no cover - defensive
        return "<no body>"
