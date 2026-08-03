"""Where a pipeline alert goes.

CLAUDE.md §8 Phase 5 asks for "monitoring/alerting on the pipeline". Which
channel that means is not settled — the client may want Slack, may want email,
may want nothing but Render's own cron notifications — so it is configuration
(`app_config.alert_adapter`) rather than code, the same shape as the odds and AI
seams.

THE ALERTING CHANNEL IS THE ONE THING THAT CANNOT ALERT ON ITSELF. If a webhook
URL is revoked, every subsequent problem is delivered to nobody, and silence
from a monitor is indistinguishable from a healthy pipeline — which is this
project's recurring failure mode dressed up as an operations concern. Three
things follow, and they are the reason this file is not just a `send()`:

  * `send()` RAISES on delivery failure rather than logging and returning. A
    swallowed exception here is precisely the silence described above.
  * The monitor treats a delivery failure as its own failure and exits non-zero,
    so Render's built-in cron-failure notification becomes the backstop channel.
    That path needs no configuration and cannot be broken by ours.
  * The log adapter is the DEFAULT and a real destination, not a placeholder.
    Render captures cron stdout; "the alert is in the run log" is a weaker
    guarantee than a push, but it is a true one, whereas a webhook nobody has
    configured yet is not.

None of that makes the chain self-verifying. Nothing can: the last link always
has to be trusted or checked by something outside the system. What it does is
keep the chain short and make every break in it loud.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Severity = Literal["info", "warning", "critical"]

# Ordered worst-first, for picking a run's overall severity.
SEVERITY_ORDER: tuple[Severity, ...] = ("critical", "warning", "info")


class AlertAdapterError(RuntimeError):
    """An alert could not be delivered.

    Deliberately NOT subclassed into retryable and permanent variants the way
    the AI seam is. There, the distinction changes behaviour — a rate limit
    means pace yourself and an auth failure means stop. Here every delivery
    failure means the same thing: the humans did not get told, so fail the run
    and let Render's notification carry it instead.
    """


@dataclass(frozen=True)
class Alert:
    """One thing worth waking up for.

    `key` is a stable identifier for the CONDITION, not the occurrence —
    "ingest_stats is stale", not "ingest_stats was stale at 06:00 on Tuesday".
    Adapters that deduplicate or thread messages need it, and it is also what
    makes an alert greppable in a log six weeks later.
    """

    severity: Severity
    key: str
    title: str
    detail: str

    def render(self) -> str:
        return f"[{self.severity.upper()}] {self.title}\n{self.detail}"


@runtime_checkable
class AlertAdapter(Protocol):
    """What every alert destination must offer. Deliberately one method."""

    name: str

    def send(self, alert: Alert) -> None:
        """Deliver one alert, or raise `AlertAdapterError`."""
        ...
