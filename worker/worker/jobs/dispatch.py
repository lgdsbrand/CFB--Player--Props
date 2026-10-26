"""Run what one merged Render service is due to run (2026-10-26).

    python -m worker.jobs.dispatch --group odds|cfb-daily|nfl-daily [--dry-run]

The schedule, and what "due" means, are in worker/dispatch.py. This is the
runner. Its group's mode comes from app_config.dispatch_mode:

  off     (or absent) does nothing and exits 0.
  shadow  records each due occurrence in dispatch_slots, with whether the
          service holds the keys the task needs, and runs nothing. For the
          days the new services run beside the old ones, so the two can be
          compared on Render's real clock before any work moves.
  live    claims each due occurrence (at most once, by the table's primary
          key), runs its command through bash with the task's environment, and
          records how it ended. Exits 1 if any task failed, so Render's own
          failure notice still fires, as it did per old service.

Every job inside a task keeps its own pipeline_runs row and its own time limit,
exactly as when it ran as its own service. The dispatcher's own limit is the
service's JOB_TIME_LIMIT_SECONDS, removed from the tasks' environment.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

from worker import db
from worker import dispatch as plan
from worker.config import ConfigError, get_settings
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

#: How long past an odds deadline the dispatcher waits before killing a task
#: whose jobs did not stop themselves (their own limit fires at the deadline).
KILL_GRACE_SECONDS = 90


def group_mode(group: str) -> tuple[str, datetime | None]:
    value = db.get_config_value("dispatch_mode") or {}
    entry = value.get(group) or {}
    mode = entry.get("mode", "off")
    if mode not in ("off", "shadow", "live"):
        raise ValueError(f"app_config.dispatch_mode[{group!r}].mode is {mode!r}")
    since = entry.get("since")
    return mode, datetime.fromisoformat(since.replace("Z", "+00:00")) if since else None


def claimed_slots(group: str, mode: str, now: datetime) -> dict[str, list[datetime]]:
    names = [t.name for t in plan.tasks_in(group)]
    rows = db.fetch_all(
        """
        select task, slot_at from dispatch_slots
         where mode = %s and task = any(%s) and slot_at >= %s
        """,
        (mode, names, now - plan.GROUPS[group].lookback - timedelta(hours=1)),
    )
    out: dict[str, list[datetime]] = {}
    for r in rows:
        out.setdefault(r["task"], []).append(r["slot_at"])
    return out


def claim(task: plan.Task, slot: datetime, mode: str, note: str | None = None) -> bool:
    """Take this occurrence. False when another run already has it."""
    row = db.fetch_one(
        """
        insert into dispatch_slots (task, slot_at, mode, service, note)
        values (%s, %s, %s, %s, %s)
        on conflict (task, slot_at, mode) do nothing
        returning task
        """,
        (task.name, slot, mode, plan.GROUPS[task.group].service, note),
    )
    return row is not None


def record(task: plan.Task, slot: datetime, **fields) -> None:
    """Best-effort: a bookkeeping failure must not stop the next task."""
    sets = ", ".join(f"{k} = %s" for k in fields)
    try:
        db.execute(
            f"update dispatch_slots set {sets} where task = %s and slot_at = %s and mode = 'live'",
            (*fields.values(), task.name, slot),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "dispatch_slots: could not record %s for %s %s: %s", list(fields), task.name, slot, exc
        )


def close_killed_runs(task: plan.Task, started: datetime, note: str) -> None:
    """Mark failed the rows of jobs the dispatcher had to kill. Their own limit
    would have done it had they reached it; a kill leaves them 'running'."""
    try:
        n = db.execute(
            """
            update pipeline_runs
               set status = 'failed', finished_at = now(), error = %s
             where status = 'running'
               and metadata->>'dispatch_task' = %s
               and started_at >= %s
            """,
            (note, task.name, started),
        )
        if n:
            log.warning("Closed %d run(s) of %s killed at its deadline", n, task.name)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not close the killed runs of %s: %s", task.name, exc)


def run_command(task: plan.Task, deadline: datetime | None) -> int:
    """Run the task's command through bash; return its exit code."""
    env = plan.child_env(task, os.environ)
    timeout = None
    if deadline is not None:
        env["JOB_DEADLINE_AT"] = str(int(deadline.timestamp()))
        timeout = max((deadline - datetime.now(UTC)).total_seconds(), 0) + KILL_GRACE_SECONDS
    posix = os.name == "posix"
    proc = subprocess.Popen(["bash", "-c", task.command], env=env, start_new_session=posix)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.error("%s ran past its deadline %s; stopping it", task.name, deadline)
        _kill(proc, posix)
        return 124


def _kill(proc: subprocess.Popen, posix: bool) -> None:
    for sig, wait in ((signal.SIGTERM, 20), (getattr(signal, "SIGKILL", signal.SIGTERM), 10)):
        try:
            if posix:
                os.killpg(proc.pid, sig)
            else:
                proc.send_signal(sig)
            proc.wait(timeout=wait)
            return
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
            continue


def run_live(group: str, pairs: list[tuple[plan.Task, datetime]]) -> int:
    began = time.monotonic()
    failed = []
    for task, slot in pairs:
        if group != "odds" and time.monotonic() - began > plan.DAILY_START_BY.total_seconds():
            log.warning(
                "Ran %s already; %s (%s) waits for the next run",
                plan.DAILY_START_BY,
                task.name,
                slot,
            )
            break
        if not claim(task, slot, "live"):
            log.info("%s %s already claimed by another run; skipped", task.name, slot)
            continue
        started = datetime.now(UTC)
        deadline = plan.odds_deadline(started) if group == "odds" else None
        record(task, slot, started_at=started)
        log.info(
            "Running %s (slot %s)%s",
            task.name,
            slot.strftime("%Y-%m-%d %H:%M"),
            f", deadline {deadline:%H:%M:%S}" if deadline else "",
        )
        try:
            code = run_command(task, deadline)
        except OSError as exc:
            log.error("%s could not start: %s", task.name, exc)
            code = 127
        if code == 124 and deadline is not None:
            close_killed_runs(
                task,
                started,
                f"Stopped by the {group} dispatcher at its deadline {deadline:%H:%M} UTC.",
            )
        record(task, slot, finished_at=datetime.now(UTC), exit_code=code)
        log.info("%s finished with exit code %d", task.name, code)
        if code != 0:
            failed.append(task.name)
    if failed:
        log.error("Failed: %s", ", ".join(failed))
        return 1
    return 0


def run_shadow(pairs: list[tuple[plan.Task, datetime]]) -> int:
    for task, slot in pairs:
        missing = plan.missing_env(task, plan.child_env(task, os.environ))
        note = f"would run; MISSING {', '.join(missing)}" if missing else "would run"
        if claim(task, slot, "shadow", note):
            (log.warning if missing else log.info)(
                "shadow: %s %s %s", task.name, slot.strftime("%Y-%m-%d %H:%M"), note
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--group", required=True, choices=sorted(plan.GROUPS))
    parser.add_argument(
        "--dry-run", action="store_true", help="say what is due; claim and run nothing"
    )
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    mode, since = group_mode(args.group)
    if mode == "off" and not args.dry_run:
        log.info("dispatch_mode for %s is off; nothing to do", args.group)
        return 0
    now = datetime.now(UTC)
    pairs = plan.due(
        args.group, now, since, claimed_slots(args.group, mode if mode != "off" else "live", now)
    )
    if args.dry_run:
        for task, slot in pairs:
            print(f"{slot:%Y-%m-%d %H:%M}  {task.name}  (mode {mode})")
        if not pairs:
            print(f"nothing due for {args.group} (mode {mode})")
        return 0
    if not pairs:
        return 0
    return run_shadow(pairs) if mode == "shadow" else run_live(args.group, pairs)


if __name__ == "__main__":
    sys.exit(main())
