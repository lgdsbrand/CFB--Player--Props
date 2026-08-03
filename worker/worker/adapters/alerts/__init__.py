"""Pluggable alert destination for pipeline monitoring (CLAUDE.md §8 Phase 5).

Which channel runs comes from `app_config.alert_adapter`, so pointing alerts at
Slack — or moving them off it — is a row edit rather than a deploy.
"""

from __future__ import annotations

from typing import Any

from worker.adapters.alerts.base import (
    Alert,
    AlertAdapter,
    AlertAdapterError,
    Severity,
)
from worker.adapters.alerts.log import ADAPTER_NAME as LOG_ADAPTER_NAME
from worker.adapters.alerts.log import LogAlertAdapter
from worker.adapters.alerts.webhook import ADAPTER_NAME as WEBHOOK_ADAPTER_NAME
from worker.adapters.alerts.webhook import WebhookAlertAdapter

__all__ = [
    "Alert",
    "AlertAdapter",
    "AlertAdapterError",
    "KNOWN_ADAPTERS",
    "LOG_ADAPTER_NAME",
    "LogAlertAdapter",
    "Severity",
    "WEBHOOK_ADAPTER_NAME",
    "WebhookAlertAdapter",
    "get_adapter",
]

KNOWN_ADAPTERS = (LOG_ADAPTER_NAME, WEBHOOK_ADAPTER_NAME)


def get_adapter(name: str, **kwargs: Any) -> AlertAdapter:
    """Build the named adapter.

    Unknown names RAISE rather than falling back to the log adapter, for the
    same reason the AI seam does — a silent fallback makes a typo in the config
    look exactly like a deliberate choice. It matters more here: the fallback
    would be quieter than what was asked for, so the failure mode of a
    misconfigured alerting system would be an alerting system that seems fine.
    """
    if name == LOG_ADAPTER_NAME:
        return LogAlertAdapter()
    if name == WEBHOOK_ADAPTER_NAME:
        return WebhookAlertAdapter(**kwargs)
    raise AlertAdapterError(
        f"Unknown alert adapter {name!r}. Known adapters: {list(KNOWN_ADAPTERS)}. "
        "Set app_config.alert_adapter to one of these."
    )
