"""CollegeFootballData API client wrapper.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). Everything CFBD-shaped belongs under this
package; the NFL build replaces this layer and keeps worker/core untouched.

Only the authentication and construction concerns live here. Endpoint-by-
endpoint ingest lands in Phase 2.
"""

from __future__ import annotations

from typing import Any, TypeVar

import cfbd

from worker.config import get_settings
from worker.logging_setup import get_logger

log = get_logger(__name__)

ApiT = TypeVar("ApiT")


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


class CfbdClient:
    """Thin context-managed wrapper over `cfbd.ApiClient`.

    Usage::

        with CfbdClient() as client:
            conferences = client.api(cfbd.ConferencesApi).get_conferences()

    Rate limiting and retry policy are deliberately NOT implemented yet. The key
    is assumed to be a paid tier (CLAUDE.md §4), and the right backoff behaviour
    depends on the actual per-endpoint limits the backfill runs into — that gets
    built in Phase 2 against real responses rather than guessed at now.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._configuration = build_configuration(api_key)
        self._api_client: cfbd.ApiClient | None = None

    def __enter__(self) -> CfbdClient:
        self._api_client = cfbd.ApiClient(self._configuration)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._api_client is not None:
            self._api_client.close()
            self._api_client = None

    def api(self, api_class: type[ApiT]) -> ApiT:
        """Instantiate a CFBD API class bound to this client's credentials."""
        if self._api_client is None:
            raise RuntimeError("CfbdClient must be used as a context manager")
        return api_class(self._api_client)  # type: ignore[call-arg]
