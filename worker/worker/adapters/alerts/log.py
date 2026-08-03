"""Alerts to the run log.

THE DEFAULT, AND A REAL DESTINATION. Render captures every cron's stdout and
keeps it against the run, so an alert written here is retrievable by whoever is
looking at why a job went red — and the monitor exits non-zero when anything
critical fires, which is what turns Render's own cron-failure notification into
the delivery mechanism. That is a genuine, if minimal, alerting path that
requires nobody to configure anything and cannot break in a way our code causes.

It is deliberately NOT a null adapter. `worker/adapters/odds/null.py` serves no
odds because "no odds source" is a real product state (CLAUDE.md §9.1). There is
no equivalent state here: a pipeline nobody is told about when it breaks is not
a configuration choice, it is an outage waiting to be discovered by a reader of
the board. So the weakest option this seam offers still tells someone.
"""

from __future__ import annotations

from worker.adapters.alerts.base import Alert
from worker.logging_setup import get_logger

log = get_logger(__name__)

ADAPTER_NAME = "log"

_LEVEL = {"info": log.info, "warning": log.warning, "critical": log.error}


class LogAlertAdapter:
    """Writes each alert to the worker log at a level matching its severity."""

    name = ADAPTER_NAME

    def send(self, alert: Alert) -> None:
        emit = _LEVEL.get(alert.severity, log.warning)
        # One record per alert, severity in the message as well as the level:
        # log aggregators routinely flatten levels, and an alert that reads as
        # ordinary chatter once it has been flattened has not been delivered.
        emit("ALERT [%s] %s — %s", alert.key, alert.title, alert.detail)
