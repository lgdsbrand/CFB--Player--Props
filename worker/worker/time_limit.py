"""A wall-clock limit on every job process that runs on Render.

2026-10-23: a `cfb-live-scores` run asked the database which games were in
their window, got no answer, and waited twelve hours, until Render's own cron
timeout killed it. Render skips every run of a cron while one is active, so the
live scoreboard, live projections and live game simulations were all off for
four of that night's five college games. The connection timeouts in `db` did
not catch it and cannot: the pooler stays up and answers the keepalives while
the database behind it does not, so the socket is healthy and the query simply
never returns. Neither does a server-side `statement_timeout`, because the
stalled server is the one that would have to enforce it.

So the limit is on the process. Each job gets a budget well past the longest
run it has ever needed (measured on production over 60 days, 2026-10-24). When
the budget runs out, the job marks its own open `pipeline_runs` rows failed,
which is best-effort since the database may be what stalled, and exits, so the
next scheduled run starts on time instead of being skipped.

Armed only on Render, from `worker.jobs` itself, so a local backfill or a test
run is never cut short. `JOB_TIME_LIMIT_SECONDS` overrides the budget
anywhere, and 0 turns it off.

`JOB_DEADLINE_AT` (Unix seconds, 2026-10-26) shortens the budget to the time
left before that moment, whatever the job. The merged odds dispatcher sets it
so every step of a chain stops before the next odds slot (worker/dispatch.py);
a step started past the deadline still gets DEADLINE_FLOOR_SECONDS to fail
cleanly rather than none.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Mapping, Sequence

from worker.logging_setup import get_logger

log = get_logger(__name__)

ENV_OVERRIDE = "JOB_TIME_LIMIT_SECONDS"
ENV_DEADLINE = "JOB_DEADLINE_AT"
DEADLINE_FLOOR_SECONDS = 30

# Two hours covers every job not listed: the longest of them (run_projections)
# has taken 12 minutes.
DEFAULT_LIMIT_SECONDS = 2 * 60 * 60

JOB_LIMIT_SECONDS: dict[str, int] = {
    # Cron every 2 minutes, longest run 2.5 minutes. Ten minutes means a stall
    # costs a few polls, not a night.
    "poll_live_scores": 10 * 60,
    # The hourly odds chains run these back to back at :00, :20 and :40. The
    # longest any has taken is 3.5 minutes.
    "ingest_odds": 20 * 60,
    "ingest_game_odds": 20 * 60,
    "capture_game_periods": 20 * 60,
    "capture_first_quarter": 20 * 60,
    "price_sim_edges": 20 * 60,
    "nfl_fantasy_picks": 20 * 60,
    "reprice_picks": 20 * 60,
    "lock_prop_picks": 20 * 60,
    "monitor_pipeline": 20 * 60,
    # Open-Meteo's rate limit makes this slow: 88 minutes at its longest.
    "ingest_weather": 3 * 60 * 60,
}

LONGEST_LIMIT_SECONDS = max(DEFAULT_LIMIT_SECONDS, *JOB_LIMIT_SECONDS.values())

# What coreutils `timeout` exits with, so the cause reads the same in Render's log.
EXIT_CODE = 124

# How long the expiry waits to mark its rows failed before exiting anyway.
CLEANUP_SECONDS = 45

_timer: threading.Timer | None = None


def job_module(argv: Sequence[str] | None = None) -> str | None:
    """The job this process runs, from `python -m worker.jobs.<name>`, else None.

    Read from `sys.orig_argv` because the package is imported before `runpy`
    rewrites `sys.argv[0]`, so at that point `sys.argv` only says "-m".
    """
    argv = list(sys.orig_argv if argv is None else argv)
    for i, arg in enumerate(argv[:-1]):
        if arg == "-m":
            module = argv[i + 1]
            prefix = "worker.jobs."
            return module[len(prefix):] if module.startswith(prefix) else None
    return None


def limit_for(
    job: str | None, env: Mapping[str, str] | None = None, now: float | None = None
) -> int | None:
    """The budget in seconds for `job`, or None for no limit."""
    env = os.environ if env is None else env
    if job is None:
        return None
    seconds = _base_limit(job, env)
    deadline = (env.get(ENV_DEADLINE) or "").strip()
    if deadline:
        left = int(float(deadline) - (time.time() if now is None else now))
        left = max(left, DEADLINE_FLOOR_SECONDS)
        seconds = left if seconds is None else min(seconds, left)
    return seconds


def _base_limit(job: str, env: Mapping[str, str]) -> int | None:
    override = (env.get(ENV_OVERRIDE) or "").strip()
    if override:
        seconds = int(override)
        return seconds if seconds > 0 else None
    if not env.get("RENDER_SERVICE_NAME"):
        return None
    return JOB_LIMIT_SECONDS.get(job, DEFAULT_LIMIT_SECONDS)


def arm(job: str | None = None, env: Mapping[str, str] | None = None) -> int | None:
    """Start the limit for this process once. Returns the budget, or None."""
    global _timer
    if _timer is not None:
        return None
    job = job if job is not None else job_module()
    seconds = limit_for(job, env)
    if seconds is None:
        return None
    _timer = threading.Timer(seconds, _expire, args=(job, seconds))
    _timer.daemon = True
    _timer.start()
    return seconds


def _expire(job: str, seconds: int) -> None:
    spent = f"{seconds // 60} minutes" if seconds >= 120 else f"{seconds} seconds"
    message = (
        f"Time limit: {job} was still running after {spent} "
        f"and was stopped so the next scheduled run is not skipped."
    )
    log.error(message)
    from worker import db

    cleanup = threading.Thread(target=db.fail_open_runs, args=(message,), daemon=True)
    cleanup.start()
    cleanup.join(CLEANUP_SECONDS)
    for handler in logging.getLogger().handlers:
        handler.flush()
    sys.stdout.flush()
    sys.stderr.flush()
    # os._exit, not sys.exit: the main thread is the one stuck, and only a hard
    # exit gets past a call that never returns.
    os._exit(EXIT_CODE)
