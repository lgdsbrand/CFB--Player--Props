"""Integration tests for the monitor's SQL (Phase 5d).

Skipped when SUPABASE_DB_URL is unset, like `test_schema_constraints.py`, and
everything runs inside one transaction that is rolled back at teardown — no row
survives, including in `pipeline_runs`, which is a live operational log the
monitor itself reads.

THE CLAIM UNDER TEST IS THE ONE THAT CANNOT BE READ OFF THE CODE.

`worker.db.pipeline_run` marks a row `failed` only when Python catches the
exception. An OOM kill, a Render deploy restart, or a hard timeout leaves the
row `running` forever — and a `running` row has a RECENT `started_at`. So the
obvious freshness query,

    select max(started_at) from pipeline_runs where job_name = …

reports a job that has been dead for a week as having run minutes ago. The
staleness check therefore keys on `finished_at where status = 'succeeded'` and
nothing else, and that is a property of a SQL string. It type-checks either way,
it reads plausibly either way, and only an engine can settle it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.rows import dict_row  # noqa: E402

from worker.config import ConfigError, get_settings  # noqa: E402
from worker.jobs import monitor_pipeline  # noqa: E402
from worker.jobs.monitor_pipeline import (  # noqa: E402
    MonitorReport,
    check_latest_run_failed,
    check_staleness,
    check_stuck_runs,
)

pytestmark = pytest.mark.integration

# Never collides with a real job name, so a leaked row could not be mistaken for
# one — and the expectations below are built explicitly rather than read from
# MONITORED_JOBS, so this file tests the QUERY, not the configuration.
JOB = "__test_monitor__"


@pytest.fixture
def conn():
    try:
        url = get_settings().database_url
    except ConfigError:
        pytest.skip("SUPABASE_DB_URL not set — integration tests need a database")

    with psycopg.connect(url, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def db(conn, monkeypatch):
    """Point the monitor's queries at this transaction.

    The check functions call `fetch_one`/`fetch_all`, which open their own
    connections and would therefore never see uncommitted rows. Redirecting the
    two names the module imported keeps the SQL under test exactly as it ships
    while confining every row to a transaction that is rolled back.
    """

    def fetch_all(sql, params=None):
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    def fetch_one(sql, params=None):
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

    monkeypatch.setattr(monitor_pipeline, "fetch_all", fetch_all)
    monkeypatch.setattr(monitor_pipeline, "fetch_one", fetch_one)
    return conn


def _run(conn, status: str, *, started_hours_ago: float, finished: bool = True,
         error: str | None = None) -> None:
    now = datetime.now(UTC)
    started = now - timedelta(hours=started_hours_ago)
    conn.cursor().execute(
        """
        insert into pipeline_runs (job_name, status, started_at, finished_at, error)
        values (%s, %s::run_status, %s, %s, %s)
        """,
        (
            JOB,
            status,
            started,
            (started + timedelta(minutes=5)) if finished else None,
            error,
        ),
    )


def _expect(max_age_hours: float):
    return monitor_pipeline.JobExpectation(
        name=JOB, max_age_hours=max_age_hours, in_season_only=False
    )


def _staleness(db, expectation) -> MonitorReport:
    report = MonitorReport()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(monitor_pipeline, "MONITORED_JOBS", (expectation,))
        check_staleness(report, slate=None)
    return report


# -----------------------------------------------------------------------------
# The headline property
# -----------------------------------------------------------------------------
def test_a_stuck_running_row_does_not_count_as_freshness(db) -> None:
    # Succeeded three days ago, then started something 10 minutes ago that never
    # finished. `max(started_at)` says this job ran 10 minutes ago. It is dead.
    _run(db, "succeeded", started_hours_ago=72)
    _run(db, "running", started_hours_ago=0.17, finished=False)

    report = _staleness(db, _expect(max_age_hours=24))

    assert [a.key for a in report.alerts] == [f"stale:{JOB}"]
    assert "72h" in report.alerts[0].title or "71h" in report.alerts[0].title


def test_a_failed_row_does_not_count_as_freshness(db) -> None:
    # The same trap in its other form: a job that is reliably crashing has very
    # recent rows, none of which mean it did any work.
    _run(db, "succeeded", started_hours_ago=72)
    _run(db, "failed", started_hours_ago=0.5, error="boom")

    report = _staleness(db, _expect(max_age_hours=24))
    assert [a.key for a in report.alerts] == [f"stale:{JOB}"]


def test_a_recent_success_is_fresh(db) -> None:
    _run(db, "succeeded", started_hours_ago=2)
    assert _staleness(db, _expect(max_age_hours=24)).alerts == []


def test_a_job_that_has_never_succeeded_is_reported_distinctly(db) -> None:
    # Different from "stale": nothing was lost, the cron was never wired up.
    # Same fix, different sentence, and the sentence is what someone acts on.
    _run(db, "failed", started_hours_ago=1, error="boom")
    report = _staleness(db, _expect(max_age_hours=24))
    assert [a.key for a in report.alerts] == [f"never-succeeded:{JOB}"]


# -----------------------------------------------------------------------------
# Stuck runs
# -----------------------------------------------------------------------------
def test_stuck_runs_are_found(db) -> None:
    _run(db, "running", started_hours_ago=9, finished=False)
    report = MonitorReport()
    check_stuck_runs(report, hours=6)

    mine = [a for a in report.alerts if a.key == f"stuck:{JOB}"]
    assert len(mine) == 1
    assert "9" in mine[0].detail


def test_a_recently_started_run_is_not_called_stuck(db) -> None:
    # Jobs take time. Alerting on a run that started twenty minutes ago would
    # fire on every long ingest.
    _run(db, "running", started_hours_ago=0.33, finished=False)
    report = MonitorReport()
    check_stuck_runs(report, hours=6)
    assert [a for a in report.alerts if a.key == f"stuck:{JOB}"] == []


# -----------------------------------------------------------------------------
# Failures
# -----------------------------------------------------------------------------
def test_the_latest_run_failing_is_reported(db) -> None:
    _run(db, "failed", started_hours_ago=1, error="ValueError: nope")
    report = MonitorReport()
    check_latest_run_failed(report)

    mine = [a for a in report.alerts if a.key == f"failed:{JOB}"]
    assert len(mine) == 1
    assert "ValueError: nope" in mine[0].detail


def test_a_failure_followed_by_a_success_is_not_reported(db) -> None:
    # The ordinary case: it broke, it was fixed, it ran. Alerting here is how a
    # monitor becomes something people mute.
    _run(db, "failed", started_hours_ago=3, error="boom")
    _run(db, "succeeded", started_hours_ago=1)

    report = MonitorReport()
    check_latest_run_failed(report)
    assert [a for a in report.alerts if a.key == f"failed:{JOB}"] == []


def test_consecutive_failures_are_counted_since_the_last_success(db) -> None:
    _run(db, "succeeded", started_hours_ago=50)
    _run(db, "failed", started_hours_ago=3, error="boom")
    _run(db, "failed", started_hours_ago=2, error="boom")
    _run(db, "failed", started_hours_ago=1, error="boom")

    report = MonitorReport()
    check_latest_run_failed(report)
    mine = [a for a in report.alerts if a.key == f"failed:{JOB}"][0]
    # Three since the last success, not all three ever recorded — the count is
    # what tells "flaky" apart from "broken since Tuesday".
    assert "3 consecutive" in mine.title
