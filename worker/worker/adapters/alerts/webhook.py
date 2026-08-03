"""Alerts to an incoming webhook.

One JSON POST to a URL from the environment. Slack, Discord and Microsoft Teams
all accept an incoming webhook whose body carries a `text` field, so this covers
the three the client is likely to name without committing to any of them, and
without adding a vendor SDK for a single POST (CLAUDE.md §0).

**THE URL IS THE CREDENTIAL.** A Slack incoming-webhook URL carries its secret
in the path — anyone holding it can post as that integration. Two consequences
this module exists to enforce:

  * It comes from `ALERT_WEBHOOK_URL` in the environment, never from
    `app_config`. That table is world-readable under RLS and there is an audit
    check for credential-shaped values in it (CLAUDE.md §0).
  * It is never logged, never put in an exception message, and never echoed back
    in a report. `_safe()` strips it from anything on its way out, including the
    URLError reprs that would otherwise carry the full URL.

Uses `urllib.request` like the other two transports in this repo rather than
adding `requests`. Retries only what a retry can fix: a 404 means the webhook
was deleted and will still be deleted in ten seconds.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request

from worker.adapters.alerts.base import Alert, AlertAdapterError
from worker.config import ConfigError, get_settings

ADAPTER_NAME = "webhook"

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 3
TIMEOUT = 15.0
MAX_BACKOFF = 20.0

# Slack renders these; the others pass them through as text. Purely cosmetic,
# and chosen so severity survives a channel that strips formatting.
_MARKER = {"info": "•", "warning": "⚠️", "critical": "🚨"}


class WebhookAlertAdapter:
    """POSTs `{"text": …}` to an incoming webhook."""

    name = ADAPTER_NAME

    def __init__(self, url: str | None = None) -> None:
        resolved = url or _url_from_environment()
        if not resolved:
            raise ConfigError(
                "ALERT_WEBHOOK_URL is not set, but app_config.alert_adapter is "
                "'webhook'. Set the environment variable or switch the adapter "
                "back to 'log'. The URL must NOT be stored in app_config — it "
                "carries its own secret and that table is world-readable."
            )
        self._url = resolved

    def send(self, alert: Alert) -> None:
        marker = _MARKER.get(alert.severity, "•")
        payload = {
            "text": f"{marker} *{alert.title}*\n{alert.detail}",
            # Sent alongside rather than only inside `text` so a receiver that
            # routes on structure has something to route on.
            "severity": alert.severity,
            "key": alert.key,
        }
        body = json.dumps(payload).encode("utf-8")
        attempt = 0

        while True:
            request = urllib.request.Request(
                self._url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT):  # noqa: S310
                    return

            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUSES or attempt >= MAX_RETRIES:
                    raise AlertAdapterError(
                        _safe(
                            f"Alert webhook returned HTTP {exc.code}. "
                            "Nobody was notified.",
                            self._url,
                        )
                    ) from None
                attempt += 1
                _backoff(attempt, exc.headers.get("Retry-After"))

            except urllib.error.URLError as exc:
                if attempt >= MAX_RETRIES:
                    raise AlertAdapterError(
                        _safe(
                            f"Could not reach the alert webhook: {exc.reason}. "
                            "Nobody was notified.",
                            self._url,
                        )
                    ) from None
                attempt += 1
                _backoff(attempt)


def _url_from_environment() -> str | None:
    """Read the webhook URL, without letting unrelated config gaps mask it.

    `get_settings()` raises when SUPABASE_DB_URL is absent, which has nothing to
    do with this adapter. Reporting "the database URL is missing" to someone who
    misconfigured their webhook sends them to the wrong file, so an unloadable
    environment is reported here as "no URL" and the constructor's own message —
    which names the variable AND says the URL must not go in app_config — is
    what surfaces.
    """
    try:
        return get_settings().alert_webhook_url
    except ConfigError:
        return None


def _backoff(attempt: int, retry_after: str | None = None) -> None:
    if retry_after:
        try:
            time.sleep(min(float(retry_after), MAX_BACKOFF))
            return
        except (TypeError, ValueError):
            pass
    delay = min(2.0**attempt, MAX_BACKOFF)
    time.sleep(delay * (0.5 + random.random() / 2))  # noqa: S311 - not crypto


def _safe(message: str, url: str) -> str:
    """Remove the webhook URL from a message about to be raised or logged.

    Substring removal rather than a pattern, because the whole URL is the
    secret — there is no key-shaped fragment to match, and a regex tuned to
    Slack's format would let a Discord or Teams URL straight through.
    """
    return message.replace(url, "<redacted webhook url>")
