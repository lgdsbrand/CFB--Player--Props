"""Jobs stop at a time limit, and a run's outcome is recorded through a blip.

2026-10-23: cfb-live-scores waited twelve hours on a database that never
answered, and Render skipped every live poll meanwhile; earlier that day an
ingest_stats run died on a dropped connection and stayed 'running' for a day,
because its failure was written on the connection that had just died.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import psycopg
import pytest

from worker import db, time_limit

ON_RENDER = {"RENDER_SERVICE_NAME": "cfb-live-scores"}


class TestWhichJob:
    def test_reads_the_module_after_dash_m(self):
        argv = ["python", "-m", "worker.jobs.poll_live_scores", "--dry-run"]
        assert time_limit.job_module(argv) == "poll_live_scores"

    def test_not_a_job(self):
        assert time_limit.job_module(["python", "-m", "pytest", "-q"]) is None
        assert time_limit.job_module(["python", "script.py"]) is None


class TestLimitFor:
    def test_off_render_there_is_no_limit(self):
        assert time_limit.limit_for("poll_live_scores", {}) is None

    def test_no_job_no_limit(self):
        assert time_limit.limit_for(None, ON_RENDER) is None

    def test_live_poll_is_short(self):
        # Cron every 2 minutes; a stall must cost a few polls, not a night.
        assert time_limit.limit_for("poll_live_scores", ON_RENDER) <= 15 * 60

    def test_unlisted_jobs_get_the_default(self):
        limit = time_limit.limit_for("run_projections", ON_RENDER)
        assert limit == time_limit.DEFAULT_LIMIT_SECONDS

    def test_override_applies_anywhere_and_zero_disables(self):
        assert time_limit.limit_for("ingest_stats", {"JOB_TIME_LIMIT_SECONDS": "30"}) == 30
        env = {**ON_RENDER, "JOB_TIME_LIMIT_SECONDS": "0"}
        assert time_limit.limit_for("ingest_stats", env) is None

    def test_every_listed_job_exists(self):
        jobs = Path(time_limit.__file__).parent / "jobs"
        for name in time_limit.JOB_LIMIT_SECONDS:
            assert (jobs / f"{name}.py").exists(), name

    def test_limits_clear_the_longest_runs_seen(self):
        # Longest successful runs on Render over 60 days (2026-10-24), seconds.
        longest = {"ingest_weather": 5259, "run_projections": 715, "ingest_stats": 579,
                   "ingest_odds": 211, "poll_live_scores": 155, "reprice_picks": 45}
        for job, seconds in longest.items():
            assert time_limit.limit_for(job, ON_RENDER) >= 1.5 * seconds, job


def test_importing_the_jobs_under_pytest_arms_nothing():
    import worker.jobs  # noqa: F401

    assert time_limit._timer is None


def test_a_stuck_job_is_stopped_with_exit_124(tmp_path):
    """The real thing: a process whose main thread never returns is ended."""
    script = tmp_path / "stuck.py"
    script.write_text(textwrap.dedent("""
        import threading
        from worker import db, time_limit
        db.fail_open_runs = lambda error: print("FAILED:", error, flush=True)
        time_limit.limit_for = lambda job, env=None: 1
        time_limit.arm("poll_live_scores")
        threading.Event().wait()
    """))
    root = Path(time_limit.__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    assert done.returncode == time_limit.EXIT_CODE
    assert "FAILED: Time limit: poll_live_scores" in done.stdout


class _Cursor:
    def __init__(self, log: list, fail: list) -> None:
        self.log, self.fail, self.rowcount = log, fail, 0

    def execute(self, sql, params=()):
        if self.fail:
            raise self.fail.pop(0)
        self.log.append((" ".join(sql.split()), params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, log, fail):
        self._cur = _Cursor(log, fail)

    def cursor(self):
        return self._cur


@pytest.fixture
def fake_db(monkeypatch):
    """Statements in order; `fail` holds exceptions the next statements raise."""
    log: list = []
    fail: list = []
    monkeypatch.setattr(
        db, "connect", lambda autocommit=False: contextlib.nullcontext(_Conn(log, fail))
    )
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    return log, fail


def _updates(log):
    return [p for sql, p in log if sql.startswith("update pipeline_runs set status = %s")]


class TestPipelineRun:
    def test_success_is_recorded(self, fake_db):
        log, _ = fake_db
        with db.pipeline_run("job") as run_id:
            pass
        assert _updates(log) == [("succeeded", None, run_id)]
        assert run_id not in db._open_runs

    def test_failure_is_recorded_through_a_dropped_connection(self, fake_db):
        log, fail = fake_db
        with pytest.raises(ValueError):
            with db.pipeline_run("job") as run_id:
                fail.append(psycopg.OperationalError("SSL connection has been closed unexpectedly"))
                raise ValueError("boom")
        assert _updates(log) == [("failed", "ValueError: boom", run_id)]

    def test_the_jobs_error_wins_when_nothing_can_be_recorded(self, fake_db):
        _, fail = fake_db
        with pytest.raises(ValueError, match="boom"):
            with db.pipeline_run("job"):
                fail.extend(psycopg.OperationalError("down") for _ in range(3))
                raise ValueError("boom")

    def test_success_that_cannot_be_recorded_fails_the_job(self, fake_db):
        _, fail = fake_db
        with pytest.raises(psycopg.OperationalError):
            with db.pipeline_run("job"):
                fail.extend(psycopg.OperationalError("down") for _ in range(3))

    def test_start_closes_this_jobs_abandoned_runs(self, fake_db):
        log, _ = fake_db
        with db.pipeline_run("ingest_stats"):
            pass
        sql, params = log[0]
        assert "status = 'running'" in sql and "Abandoned" in sql
        assert params == ("ingest_stats", db.ABANDONED_AFTER_SECONDS)
        assert log[1][0].startswith("insert into pipeline_runs")

    def test_abandoned_means_past_every_limit(self):
        assert db.ABANDONED_AFTER_SECONDS > time_limit.LONGEST_LIMIT_SECONDS

    def test_fail_open_runs_closes_what_is_open(self, fake_db):
        log, _ = fake_db
        run_id = uuid.uuid4()
        db._open_runs.add(run_id)
        try:
            db.fail_open_runs("Time limit: x")
        finally:
            db._open_runs.discard(run_id)
        assert _updates(log) == [("failed", "Time limit: x", run_id)]
