"""Watch the pipeline and say something when it stops working.

CLAUDE.md §8 Phase 5 asks for monitoring and alerting on the pipeline. The thing
being monitored is `pipeline_runs`, which every job writes through
`worker.db.pipeline_run`, plus the data those jobs are supposed to produce.

    python -m worker.jobs.monitor_pipeline
    python -m worker.jobs.monitor_pipeline --dry-run     # evaluate, send nothing

FOUR CHECKS, AND THE ORDER THEY ARE IN IS THE POINT.

1. **Stuck runs.** `pipeline_run` marks a row `failed` only when Python catches
   the exception. An OOM kill, a Render deploy restart, or a hard timeout leaves
   the row `running` forever — and a `running` row has a RECENT `started_at`, so
   any freshness check keyed on when a job last STARTED reads a dead job as a
   healthy one. That is why check 3 keys on `finished_at where status =
   'succeeded'` and nothing else, and why this check exists separately.

2. **The latest run failed.** Cheap, and the one people expect.

3. **Staleness.** A job that has not SUCCEEDED within its expected interval.
   Expectations live in `MONITORED_JOBS` beside the cron that produces them.

4. **Data freshness.** A job can succeed and write nothing — the provider
   returned an empty list, the week resolved to the wrong number, the filter
   excluded everyone. Checks 1-3 all go green on that. This is the check that
   asks whether the board a reader opens actually has anything on it, and it is
   the one this project has needed most often.

TWO THINGS DELIBERATELY NOT BUILT.

*No alert deduplication.* A condition that persists is re-sent on every run.
Suppression needs state, and a suppression bug is silent by construction — the
symptom is an alert that never arrives, which is indistinguishable from a
healthy pipeline. The cadence in `render.yaml` is the throttle instead.

*No alerting on the monitor's own liveness.* Nothing here can detect that this
job stopped being scheduled; that is the last link and it has to be checked from
outside. Render's cron-failure notification is the backstop, which is why a
critical finding exits non-zero even though the run itself succeeded.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any

from worker.adapters.alerts import (
    Alert,
    AlertAdapterError,
    Severity,
    get_adapter,
)
from worker.config import ConfigError, get_settings
from worker.core.schedule import Slate, current_slate
from worker.db import fetch_all, fetch_one, get_config_value, pipeline_run
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "monitor_pipeline"

# How long a run may sit `running` before we call it dead rather than slow. The
# longest scheduled job is the weekly projection pass; a multi-season backfill
# takes longer than this but is run by hand, not on a schedule, so a false
# positive there costs one log line during work someone is already watching.
STUCK_AFTER_HOURS = 6.0


@dataclass(frozen=True)
class JobExpectation:
    """How often a scheduled job is supposed to succeed.

    `max_age_hours` must exceed the job's cron period with room for one missed
    run — alerting the first time a job is an hour late trains people to ignore
    it. Each entry names the `render.yaml` schedule it belongs to so the two
    cannot drift apart unnoticed; `tests/test_monitor.py` asserts they agree.
    """

    name: str
    max_age_hours: float
    severity: Severity = "warning"
    #: Skip out of season, when idleness is correct rather than broken.
    in_season_only: bool = True
    #: `app_config` key that must not read "none" for this job to be expected.
    enabled_key: str | None = None
    note: str = ""


MONITORED_JOBS: tuple[JobExpectation, ...] = (
    JobExpectation(
        name="healthcheck",
        max_age_hours=30,
        severity="critical",
        in_season_only=False,
        note="daily 12:00 UTC — the year-round canary, so no in-season gate",
    ),
    JobExpectation(
        name="ingest_reference",
        max_age_hours=200,
        note="Sunday 09:00 UTC, chained ahead of ingest_stats",
    ),
    JobExpectation(
        name="ingest_stats",
        max_age_hours=200,
        severity="critical",
        note="Sunday 09:00 UTC — everything downstream is built from these rows",
    ),
    JobExpectation(
        name="ingest_ratings",
        max_age_hours=200,
        note="Sunday 09:00 UTC, chained after ingest_stats",
    ),
    JobExpectation(
        name="build_splits",
        max_age_hours=200,
        severity="critical",
        note="Sunday 09:00 UTC — the position-split engine, CLAUDE.md §5",
    ),
    JobExpectation(
        name="run_projections",
        max_age_hours=200,
        severity="critical",
        note="Tuesday 09:00 UTC — without this the board has nothing on it",
    ),
    JobExpectation(
        name="ingest_odds",
        max_age_hours=12,
        enabled_key="odds_adapter",
        note="every 3h — books post late, often Thu/Fri (CLAUDE.md §7)",
    ),
    JobExpectation(
        name="generate_ai_reads",
        max_age_hours=200,
        enabled_key="ai_adapter",
        note="Wednesday 14:00 UTC — one read per player per week, cached",
    ),
    JobExpectation(
        name="audit_data",
        max_age_hours=30,
        in_season_only=False,
        note="daily 13:00 UTC — the data-integrity canary",
    ),
)


@dataclass
class MonitorReport:
    alerts: list[Alert] = field(default_factory=list)
    checks_run: int = 0
    skipped: list[str] = field(default_factory=list)

    def add(self, severity: Severity, key: str, title: str, detail: str) -> None:
        self.alerts.append(Alert(severity=severity, key=key, title=title, detail=detail))

    @property
    def critical(self) -> list[Alert]:
        return [a for a in self.alerts if a.severity == "critical"]

    def summary(self) -> str:
        if not self.alerts:
            return f"{self.checks_run} checks, everything healthy"
        counts: dict[str, int] = {}
        for alert in self.alerts:
            counts[alert.severity] = counts.get(alert.severity, 0) + 1
        parts = ", ".join(f"{n} {sev}" for sev, n in sorted(counts.items()))
        return f"{self.checks_run} checks, {parts}"


# -----------------------------------------------------------------------------
# Checks
# -----------------------------------------------------------------------------
def check_stuck_runs(report: MonitorReport, *, hours: float = STUCK_AFTER_HOURS) -> None:
    """Runs that opened and never closed.

    See the module docstring: these are the ones that make a dead job look
    fresh, so they are checked on their own rather than inferred from staleness.
    """
    report.checks_run += 1
    rows = fetch_all(
        """
        select job_name,
               count(*)                                          as stuck,
               max(started_at)                                    as latest,
               round(extract(epoch from (now() - min(started_at))) / 3600.0, 1)
                                                                  as oldest_hours
          from pipeline_runs
         where status = 'running'
           and started_at < now() - make_interval(mins => %s)
         group by job_name
         order by job_name
        """,
        (int(hours * 60),),
    )
    for row in rows:
        report.add(
            "critical",
            f"stuck:{row['job_name']}",
            f"{row['job_name']} has {row['stuck']} run(s) stuck in 'running'",
            f"Oldest has been running {row['oldest_hours']}h (threshold {hours}h). "
            "A row stays 'running' when the process died without Python catching "
            "it — an OOM kill, a deploy restart, a hard timeout. Note that these "
            "rows have a recent started_at, so any freshness view keyed on when "
            "a job last STARTED would report this job as healthy.",
        )


def check_latest_run_failed(report: MonitorReport) -> None:
    """Jobs whose most recent run failed, and how many in a row."""
    report.checks_run += 1
    rows = fetch_all(
        """
        with latest as (
          select distinct on (job_name)
                 job_name, status, started_at, error
            from pipeline_runs
           order by job_name, started_at desc
        ),
        streak as (
          select p.job_name, count(*) as consecutive
            from pipeline_runs p
            join latest l on l.job_name = p.job_name
           where p.status = 'failed'
             and p.started_at > coalesce(
                   (select max(s.started_at) from pipeline_runs s
                     where s.job_name = p.job_name and s.status = 'succeeded'),
                   '-infinity'::timestamptz)
           group by p.job_name
        )
        select l.job_name,
               l.started_at,
               coalesce(l.error, '(no error recorded)') as error,
               coalesce(s.consecutive, 1)               as consecutive
          from latest l
          left join streak s on s.job_name = l.job_name
         where l.status = 'failed'
         order by l.job_name
        """
    )
    for row in rows:
        # The monitor's own failures are reported by the NEXT monitor run, which
        # is why a critical finding here must not fail this run — see main().
        report.add(
            "critical",
            f"failed:{row['job_name']}",
            f"{row['job_name']} last run FAILED"
            + (
                f" ({row['consecutive']} consecutive)"
                if int(row["consecutive"]) > 1
                else ""
            ),
            f"Started {row['started_at']:%Y-%m-%d %H:%M UTC}. {row['error']}",
        )


def check_staleness(report: MonitorReport, slate: Slate | None) -> None:
    """Jobs that have not SUCCEEDED recently enough."""
    for expectation in MONITORED_JOBS:
        if expectation.in_season_only and (slate is None or not slate.in_season):
            report.skipped.append(f"{expectation.name} (out of season)")
            continue

        if expectation.enabled_key:
            configured = get_config_value(expectation.enabled_key)
            if configured is None or str(configured) == "none":
                report.skipped.append(
                    f"{expectation.name} ({expectation.enabled_key} is 'none')"
                )
                continue

        report.checks_run += 1
        # `finished_at where succeeded` — NOT started_at, and not the latest run
        # of any status. Both of the obvious alternatives report a job that is
        # reliably crashing, or reliably hanging, as fresh.
        row = fetch_one(
            """
            select max(finished_at) as last_success,
                   round(extract(epoch from (now() - max(finished_at))) / 3600.0, 1)
                     as age_hours
              from pipeline_runs
             where job_name = %s
               and status = 'succeeded'
            """,
            (expectation.name,),
        )
        last_success = (row or {}).get("last_success")

        if last_success is None:
            report.add(
                expectation.severity,
                f"never-succeeded:{expectation.name}",
                f"{expectation.name} has never recorded a successful run",
                f"Expected at least every {expectation.max_age_hours:.0f}h "
                f"({expectation.note}). Either the cron is not deployed or it "
                "has never completed.",
            )
            continue

        age = float(row["age_hours"])  # type: ignore[index]
        if age > expectation.max_age_hours:
            report.add(
                expectation.severity,
                f"stale:{expectation.name}",
                f"{expectation.name} has not succeeded in {age:.0f}h",
                f"Last success {last_success:%Y-%m-%d %H:%M UTC}; expected every "
                f"{expectation.max_age_hours:.0f}h ({expectation.note}).",
            )


def check_data_freshness(report: MonitorReport, slate: Slate | None) -> None:
    """Whether the current slate actually has anything on it.

    THE CHECK FOR THE FAILURE THAT RETURNS SUCCESS. Every check above can pass
    while the board is empty: a job that ran, exited 0, and wrote no rows looks
    identical to one that had nothing to do.

    Each comparison is against SOMETHING THAT ALREADY PRODUCED rather than
    against a fixed threshold, which makes it self-calibrating and survives a
    rule change about which weeks are projectable — a hardcoded floor would
    quietly go wrong.

    WEEK 1 NEEDED ITS OWN REFERENCE, and the lack of one was the exact hole
    Phase 6 exists to close. The original check asked whether an EARLIER WEEK OF
    THE SAME SEASON had produced, on the reasoning that the opening weeks
    legitimately have nothing. Week 1 has no earlier week, so an empty opening
    board could never trip it — and an empty opening board is precisely what the
    client rejected, sitting behind a `run_projections` that exits 0 because it
    genuinely succeeded at projecting nobody. Since Phase 6c the opening weeks
    are published, so the prior SEASON's same week is the reference that makes
    week 1 checkable at all.
    """
    if slate is None or not slate.in_season:
        report.skipped.append("data freshness (out of season)")
        return

    report.checks_run += 1
    row = fetch_one(
        """
        select (select count(*) from projections
                 where season = %(season)s and week = %(week)s) as this_week,
               (select count(*) from projections
                 where season = %(season)s and week < %(week)s) as earlier_weeks,
               (select count(*) from projections
                 where season = %(season)s - 1 and week = %(week)s) as last_season
        """,
        {"season": slate.season, "week": slate.week},  # type: ignore[arg-type]
    )
    assert row is not None
    if int(row["this_week"]) == 0:
        if int(row["earlier_weeks"]) > 0:
            report.add(
                "critical",
                "empty-board",
                f"No projections for {slate.season} week {slate.week}",
                f"{row['earlier_weeks']} projections exist for earlier weeks of "
                "this season, so the pipeline has been producing and has "
                "stopped. The board renders empty for every reader in this "
                "state, and no job has necessarily failed to cause it.",
            )
        elif int(row["last_season"]) > 0:
            report.add(
                "critical",
                "empty-board",
                f"No projections for {slate.season} week {slate.week}, the "
                "first week of the season",
                f"{row['last_season']} projections existed for week "
                f"{slate.week} of {slate.season - 1}, so this week is "
                "projectable in principle and something specific to this season "
                "is missing — most likely the roster, which no amount of "
                "modelling substitutes for. Nothing else would report this: "
                "there is no earlier week of this season to compare against, "
                "and run_projections exits 0 having genuinely succeeded at "
                "projecting nobody.",
            )

    if str(get_config_value("odds_adapter") or "none") != "none":
        report.checks_run += 1
        row = fetch_one(
            """
            select (select count(*) from player_prop_lines l
                      join games g on g.id = l.game_id
                     where g.season = %(season)s and g.week = %(week)s) as this_week
            """,
            {"season": slate.season, "week": slate.week},  # type: ignore[arg-type]
        )
        assert row is not None
        if int(row["this_week"]) == 0 and slate.complete:
            # Only once the slate has kicked off. Before that, no lines is the
            # normal state — college books post props Thursday or Friday for
            # Saturday games (CLAUDE.md §7), so an empty Tuesday is not news.
            report.add(
                "warning",
                "no-lines",
                f"No book lines for {slate.season} week {slate.week}",
                "The odds adapter is configured and the slate has started, but "
                "no quotes were stored. Every pick on the board is showing a "
                "model lean with no line beside it.",
            )


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
def run_checks(slate: Slate | None) -> MonitorReport:
    report = MonitorReport()
    check_stuck_runs(report)
    check_latest_run_failed(report)
    check_staleness(report, slate)
    check_data_freshness(report, slate)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate every check and print the alerts without sending them.",
    )
    parser.add_argument("--adapter", help="Override app_config.alert_adapter.")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    configure_logging(settings.log_level)

    delivery_failed: str | None = None
    report = MonitorReport()

    try:
        with pipeline_run(JOB_NAME) as run_id:
            slate = current_slate()
            if slate is None:
                log.warning(
                    "No games ingested, so there is no slate to monitor. "
                    "Run ingest_reference before expecting this to mean anything."
                )
            else:
                log.info(
                    "Monitoring %s week %s (in_season=%s, complete=%s)",
                    slate.season,
                    slate.week,
                    slate.in_season,
                    slate.complete,
                )

            report = run_checks(slate)
            log.info("%s (run %s)", report.summary(), run_id)
            for skipped in report.skipped:
                log.info("  skipped: %s", skipped)

            if args.dry_run:
                for alert in report.alerts:
                    log.info("WOULD SEND:\n%s", alert.render())
            else:
                delivery_failed = _deliver(report, args.adapter)

            _record(run_id, report, delivery_failed)
    except Exception as exc:
        # A crash here is the monitor itself failing, which the pipeline_run
        # contextmanager has already recorded as `failed` — so the NEXT run of
        # this job reports it through check_latest_run_failed. That is the only
        # self-monitoring available, and it is why this job runs often.
        log.error("Monitor failed: %s", exc)
        return 1

    if delivery_failed:
        log.error("Alert delivery failed: %s", delivery_failed)
        return 1

    # Exit non-zero on a critical finding so RENDER marks the cron failed and
    # sends its own notification. This is the backstop channel, and it works
    # even when ours is misconfigured.
    #
    # The pipeline_run row above is still `succeeded`, and that is not a
    # contradiction: the monitor did its job. Marking it failed would make the
    # next run alert on this run, and every run after that, forever.
    if report.critical:
        log.error(
            "%d critical finding(s) — exiting non-zero so the cron is marked failed.",
            len(report.critical),
        )
        return 1

    return 0


def _deliver(report: MonitorReport, override: str | None) -> str | None:
    """Send every alert. Returns an error string if any could not be delivered."""
    if not report.alerts:
        return None

    name = override or str(get_config_value("alert_adapter") or "log")
    try:
        adapter = get_adapter(name)
    except (AlertAdapterError, ConfigError) as exc:
        return str(exc)

    log.info("Sending %d alert(s) via %s", len(report.alerts), adapter.name)
    for alert in report.alerts:
        try:
            adapter.send(alert)
        except (AlertAdapterError, ConfigError) as exc:
            # Stop at the first failure. Continuing would spend minutes of retry
            # backoff per remaining alert against a channel already known to be
            # down, and the outcome is the same either way: nobody was told.
            return f"{alert.key}: {exc}"
    return None


def _record(run_id: Any, report: MonitorReport, delivery_failed: str | None) -> None:
    """Store what this run found, so alert history survives the log retention."""
    import psycopg

    from worker.db import connect

    metadata = {
        "checks_run": report.checks_run,
        "skipped": report.skipped,
        "alerts": [
            {"severity": a.severity, "key": a.key, "title": a.title}
            for a in report.alerts
        ],
        "delivery_failed": delivery_failed,
    }
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "update pipeline_runs set metadata = %s::jsonb where id = %s",
            (psycopg.types.json.Json(metadata), run_id),
        )


if __name__ == "__main__":
    sys.exit(main())
