"""Tests for pipeline monitoring and the alert seam (Phase 5d).

Two things are checked here that nothing else can check.

**That the schedules and the expectations agree.** `render.yaml` says when a job
runs; `MONITORED_JOBS` says how long it may go without succeeding. Neither file
can tell it has fallen out of step with the other — a cron added without an
expectation is simply never monitored, and an expectation whose job was renamed
alerts forever about a job that no longer exists. Both read fine in review.

**That an alerting failure is loud.** The channel is the one component that
cannot report its own outage: a swallowed delivery error produces silence, and
silence from a monitor is indistinguishable from a healthy pipeline.

All offline. No database, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from worker.adapters.alerts import (
    Alert,
    AlertAdapterError,
    LogAlertAdapter,
    get_adapter,
)
from worker.adapters.alerts.webhook import _safe
from worker.jobs.monitor_pipeline import (
    JOB_NAME as MONITOR_JOB_NAME,
)
from worker.jobs.monitor_pipeline import (
    MONITORED_JOBS,
    MonitorReport,
)

RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"


# -----------------------------------------------------------------------------
# render.yaml <-> MONITORED_JOBS
# -----------------------------------------------------------------------------
def _scheduled_jobs() -> dict[str, str]:
    """{job module name: cron expression} from render.yaml.

    Parsed with regexes rather than a YAML library on purpose: PyYAML is not a
    dependency, and CLAUDE.md §0 requires a justification for adding one. The
    file is ours and its shape is stable, and a parse that finds nothing fails
    the tests below rather than passing vacuously.
    """
    text = RENDER_YAML.read_text(encoding="utf-8")
    blocks = text.split("- type: cron")[1:]

    found: dict[str, str] = {}
    for block in blocks:
        schedule = re.search(r'^\s*schedule:\s*"([^"]+)"', block, re.MULTILINE)
        assert schedule, "a cron service in render.yaml has no schedule"
        for module in re.findall(r"python -m worker\.jobs\.(\w+)", block):
            found[module] = schedule.group(1)
    return found


def _cron_period_hours(expression: str) -> float:
    """How often a cron expression fires, for the forms this repo uses.

    Raises on anything it does not understand rather than guessing. A schedule
    this cannot read is a schedule whose expectation cannot be verified, and
    silently returning a default would make the assertion below vacuous — which
    is the failure mode these tests exist to prevent.
    """
    minute, hour, dom, month, dow = expression.split()
    if month != "*" or dom != "*":
        raise ValueError(f"unsupported cron form: {expression!r}")
    if dow != "*":
        return 168.0
    if hour == "*":
        return 1.0
    if hour.startswith("*/"):
        return float(hour[2:])
    if hour.isdigit():
        return 24.0
    raise ValueError(f"unsupported cron form: {expression!r}")


def test_render_yaml_actually_parsed() -> None:
    # Guards the guard. If the regex stops matching — someone reformats the
    # file, or switches to a different startCommand style — every assertion
    # below would pass against an empty dict.
    scheduled = _scheduled_jobs()
    assert len(scheduled) >= 7, scheduled
    assert "healthcheck" in scheduled
    assert MONITOR_JOB_NAME in scheduled


def test_every_scheduled_job_is_monitored() -> None:
    scheduled = set(_scheduled_jobs())
    # The monitor cannot meaningfully monitor its own staleness — that is the
    # last link, and it is Render's cron-failure notification that carries it.
    scheduled.discard(MONITOR_JOB_NAME)

    expected = {job.name for job in MONITORED_JOBS}
    missing = scheduled - expected
    assert not missing, (
        f"scheduled in render.yaml but absent from MONITORED_JOBS: {sorted(missing)} "
        "— these jobs can stop running and nothing will say so"
    )


def test_every_expectation_names_a_job_that_is_scheduled() -> None:
    scheduled = set(_scheduled_jobs())
    orphans = {job.name for job in MONITORED_JOBS} - scheduled
    assert not orphans, (
        f"expected by MONITORED_JOBS but not scheduled: {sorted(orphans)} "
        "— these would alert forever about a job nothing runs"
    )


def test_each_expectation_allows_at_least_one_missed_run() -> None:
    scheduled = _scheduled_jobs()
    for job in MONITORED_JOBS:
        period = _cron_period_hours(scheduled[job.name])
        assert job.max_age_hours > period, (
            f"{job.name}: max_age {job.max_age_hours}h is not longer than its "
            f"{period}h cron period — it would alert on a punctual run"
        )
        # Alerting the moment a single run is missed is how alert fatigue
        # starts. Require genuine slack, not a rounding error.
        assert job.max_age_hours >= period * 1.15, (
            f"{job.name}: max_age {job.max_age_hours}h leaves no room for one "
            f"late run of a {period}h cron"
        )


def test_gated_jobs_are_the_ones_that_can_be_switched_off() -> None:
    # `odds_adapter` and `ai_adapter` both default to "none", which is a real
    # product state (CLAUDE.md §9.1). Alerting that a deliberately disabled job
    # has not run is the purest form of crying wolf.
    gated = {job.name: job.enabled_key for job in MONITORED_JOBS if job.enabled_key}
    assert gated == {
        "ingest_odds": "odds_adapter",
        "generate_ai_reads": "ai_adapter",
    }


def test_the_year_round_canaries_are_not_gated_on_the_season() -> None:
    always_on = {job.name for job in MONITORED_JOBS if not job.in_season_only}
    assert always_on == {"healthcheck", "audit_data"}


# -----------------------------------------------------------------------------
# The alert seam
# -----------------------------------------------------------------------------
def test_unknown_adapter_raises_rather_than_falling_back() -> None:
    # A silent fallback to the log adapter would make a typo in the config look
    # exactly like a working configuration — and quieter than what was asked
    # for, so the failure mode of misconfigured alerting would be alerting that
    # seems fine.
    with pytest.raises(AlertAdapterError) as exc:
        get_adapter("slak")
    assert "slak" in str(exc.value)
    assert "log" in str(exc.value)


def test_log_adapter_is_selectable_and_sends_without_configuration() -> None:
    adapter = get_adapter("log")
    assert isinstance(adapter, LogAlertAdapter)
    adapter.send(Alert("critical", "k", "title", "detail"))  # must not raise


def test_webhook_adapter_refuses_to_start_without_a_url() -> None:
    from worker.config import ConfigError

    with pytest.raises(ConfigError) as exc:
        get_adapter("webhook", url="")
    message = str(exc.value)
    assert "ALERT_WEBHOOK_URL" in message
    # And it must say where the URL does NOT belong.
    assert "app_config" in message


def test_the_webhook_url_never_survives_into_an_error_message() -> None:
    # The whole URL is the credential — a Slack incoming-webhook URL carries its
    # secret in the path. Substring removal rather than a pattern, because there
    # is no key-shaped fragment to match and a Slack-tuned regex would let a
    # Discord or Teams URL straight through.
    url = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXX"
    message = _safe(f"Could not reach the alert webhook: {url} timed out.", url)
    assert url not in message
    assert "XXXXXXXXXXXXXXXX" not in message
    assert "redacted" in message


def test_a_teams_or_discord_url_is_redacted_too() -> None:
    url = "https://discord.com/api/webhooks/123456789/abcdefGHIJKL-_mnopQRS"
    assert url not in _safe(f"HTTP 404 from {url}", url)


# -----------------------------------------------------------------------------
# Report severity
# -----------------------------------------------------------------------------
def test_only_critical_findings_drive_the_non_zero_exit() -> None:
    report = MonitorReport()
    report.add("warning", "k1", "t", "d")
    report.add("info", "k2", "t", "d")
    assert report.critical == []

    report.add("critical", "k3", "t", "d")
    assert len(report.critical) == 1


def test_a_healthy_report_says_so_rather_than_saying_nothing() -> None:
    report = MonitorReport()
    report.checks_run = 12
    assert "healthy" in report.summary()
    assert "12" in report.summary()
