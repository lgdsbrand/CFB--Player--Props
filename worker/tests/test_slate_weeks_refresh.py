"""`run_projections` refreshes `mv_slate_weeks` (migration 0102) after writing.

The week strip above the board reads the materialised view, and this job is
the only writer of `projections`, so a run that wrote projections without the
refresh would leave the strip describing the previous run.
"""

from __future__ import annotations

import contextlib
import inspect

from worker.jobs import run_projections


def test_refresh_is_concurrent_and_commits(monkeypatch):
    executed: list[str] = []
    committed: list[bool] = []

    class _Conn:
        def execute(self, sql):
            executed.append(sql)

        def commit(self):
            committed.append(True)

    monkeypatch.setattr(run_projections, "connect", lambda: contextlib.nullcontext(_Conn()))
    run_projections.refresh_slate_weeks()
    assert executed == ["refresh materialized view concurrently mv_slate_weeks"]
    assert committed == [True]


def test_main_refreshes_after_the_weeks_and_before_success():
    source = inspect.getsource(run_projections.main)
    loop = source.index("for week in weeks:")
    refresh = source.index("refresh_slate_weeks()")
    succeeded = source.index('_finish_model_run(model_run_id, "succeeded")')
    # After every week is written, and before the run is called a success, so a
    # failed refresh fails the run instead of leaving a stale strip behind a
    # green run.
    assert loop < refresh < succeeded
    # Only when something was written: the refresh sits in the not-dry-run branch.
    branch = source.rindex("if not args.dry_run:", 0, refresh)
    assert source[branch:refresh].count("\n") <= 2
