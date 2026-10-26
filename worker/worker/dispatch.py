"""The schedule the merged Render dispatchers run (2026-10-26).

Render ran this worker as twenty cron services, one per schedule. Every worker
push rebuilt all twenty (~20 build minutes, which hit the client's spend cap on
2026-10-22) and each carries a $1 monthly minimum. Five remain:

  cfb-live-scores    every 2 minutes, unchanged: nothing may delay it
  cfb-props-monitor  every 6 hours, unchanged: it must stay up when the rest fail
  worker-odds        the hourly odds captures and the game model
  worker-cfb-daily   the college data chains, canaries and AI reads
  worker-nfl-daily   the NFL data chains

Each of the last three runs `python -m worker.jobs.dispatch --group <group>` on
a schedule that fires at every minute any of its tasks is due, and runs the
TASKS below that are due. A task is one old service: its name, its cron, its
exact start command (run through bash, so `&&` and `;` keep their meaning). The
old schedules are reproduced, not redesigned: tests/test_dispatch.py holds each
task equal to its old service in render.yaml while that still exists.

WHAT "DUE" MEANS. A Render cron never runs two of its own runs at once; it
delays the next run until the active one ends. So a run cannot tell which slot
it serves from its start time. Each run instead takes, per task, the latest
scheduled occurrence within the task's lookback that `dispatch_slots` has no
row for, claims it with an insert (the primary key makes that at-most-once) and
runs it. A late run catches up; an overlapping run finds the slot taken.

THE SPORTS STAY APART. College and NFL data run in separate services, as they
did as separate crons (render.yaml: "one sport's outage must not take the
other's schedule down"). Within one, tasks run one after another in slot order.

THE ODDS SLOTS NEVER OVERRUN. An odds task must finish before the group's next
slot with work in it, or that slot's capture would start late and could miss a
closing line. Each odds run gets a deadline one minute before that slot
(passed to every job through JOB_DEADLINE_AT, see worker.time_limit), with a
floor of a few minutes. Every odds chain has finished in under two minutes
since the time limits went in (measured 2026-10-26).

EACH TASK KEEPS ITS OLD ENVIRONMENT. The merged odds service carries both odds
keys, but `capture_first_quarter` prefers the free key when it sees one and its
old service had only the paid key, so that task runs with the free key
removed. `drop_env` lists, per task, what its old service did not have.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: The two services that stay as they are, scheduled directly in render.yaml.
DIRECT_SERVICES = ("cfb-live-scores", "cfb-props-monitor")

#: Minutes before the next odds slot by which an odds run must be done.
ODDS_DEADLINE_MARGIN = timedelta(minutes=1)
#: Least time an odds run gets, however close the next slot is.
ODDS_MIN_BUDGET = timedelta(minutes=4)
#: A daily dispatcher starts no new task after running this long; the rest
#: wait for its next run, which finds them still due.
DAILY_START_BY = timedelta(hours=3)


@dataclass(frozen=True)
class Group:
    name: str
    service: str
    cron: str
    #: How far back a missed occurrence is still worth running.
    lookback: timedelta
    #: The dispatcher process's own time limit (JOB_TIME_LIMIT_SECONDS on the service).
    limit_seconds: int


GROUPS: dict[str, Group] = {
    "odds": Group(
        "odds",
        "worker-odds",
        "0,20,30,40 * * * *",
        lookback=timedelta(minutes=60),
        limit_seconds=30 * 60,
    ),
    "cfb-daily": Group(
        "cfb-daily",
        "worker-cfb-daily",
        "*/10 8-16 * * *",
        lookback=timedelta(hours=12),
        limit_seconds=4 * 60 * 60,
    ),
    "nfl-daily": Group(
        "nfl-daily",
        "worker-nfl-daily",
        "*/10 8-16 * * *",
        lookback=timedelta(hours=12),
        limit_seconds=4 * 60 * 60,
    ),
}


@dataclass(frozen=True)
class Task:
    #: The old Render service this task replaces, kept as its name.
    name: str
    group: str
    cron: str
    command: str
    #: Environment variables the old service did not have.
    drop_env: tuple[str, ...] = ()
    #: Environment variables the task cannot run without (checked in shadow mode).
    requires_env: tuple[str, ...] = ("SUPABASE_DB_URL",)


_CFB = ("SUPABASE_DB_URL", "CFBD_API_KEY")
_PAID = ("SUPABASE_DB_URL", "ODDS_API_KEY")
_FREE = ("SUPABASE_DB_URL", "ODDS_API_KEY_FREE")

# Order matters within a slot: tasks due at the same minute run top to bottom.
# At 23:00 (and 15:00, 19:00 on Saturdays) the college game odds go before the
# props capture: both were parallel services, and the game odds take seconds.
TASKS: tuple[Task, ...] = (
    # ── odds ─────────────────────────────────────────────────────────────────
    Task(
        "cfb-game-odds-weekday",
        "odds",
        "0 23 * * 0,2,4,5",
        "python -m worker.jobs.ingest_game_odds",
        requires_env=_FREE,
    ),
    Task(
        "cfb-game-odds-saturday",
        "odds",
        "0 15,19,23 * * 6",
        "python -m worker.jobs.ingest_game_odds",
        requires_env=_FREE,
    ),
    Task(
        "cfb-props-odds-refresh",
        "odds",
        "0 * * * *",
        "python -m worker.jobs.ingest_odds --event-limit 120 --close-window-minutes 60 && "
        "python -m worker.jobs.capture_game_periods; "
        "python -m worker.jobs.reprice_picks --sport cfb; "
        "python -m worker.jobs.lock_prop_picks --sport cfb",
        drop_env=("ODDS_API_KEY_FREE",),
        requires_env=_PAID,
    ),
    Task(
        "nfl-props-odds-refresh",
        "odds",
        "20 * * * *",
        "python -m worker.jobs.ingest_odds --sport nfl --event-limit 120 "
        "--close-window-minutes 60; "
        "python -m worker.jobs.ingest_game_odds --sport nfl --paid --close-window-minutes 90; "
        "python -m worker.jobs.capture_game_periods --sport nfl; "
        "python -m worker.jobs.price_sim_edges; "
        "python -m worker.jobs.nfl_fantasy_picks --close-window-minutes 90; "
        "python -m worker.jobs.reprice_picks --sport nfl; "
        "python -m worker.jobs.lock_prop_picks --sport nfl",
        drop_env=("ODDS_API_KEY_FREE",),
        requires_env=_PAID,
    ),
    Task(
        "cfb-game-model",
        "odds",
        "30 9,15,19,23 * * *",
        "python -m worker.jobs.run_game_model",
        drop_env=("ODDS_API_KEY_FREE",),
    ),
    Task(
        "nfl-props-q1-capture",
        "odds",
        "40 * * * *",
        "python -m worker.jobs.capture_first_quarter",
        drop_env=("ODDS_API_KEY_FREE",),
        requires_env=_PAID,
    ),
    # ── college daily ────────────────────────────────────────────────────────
    Task(
        "cfb-props-ingest-results",
        "cfb-daily",
        "0 8 * * *",
        "python -m worker.jobs.ingest_reference --current && "
        "python -m worker.jobs.ingest_stats --current --incremental && "
        "python -m worker.jobs.build_splits --current && "
        "python -m worker.jobs.build_usage_shares --current --sport cfb && "
        "python -m worker.jobs.build_team_strength --current && "
        "python -m worker.jobs.ingest_rankings --live --current",
        requires_env=_CFB,
    ),
    Task(
        "cfb-props-ingest-week",
        "cfb-daily",
        "0 9 * * 0",
        "python -m worker.jobs.ingest_reference --current && "
        "python -m worker.jobs.ingest_stats --current && "
        "python -m worker.jobs.ingest_ratings --current && "
        "python -m worker.jobs.ingest_rankings --live --current && "
        "python -m worker.jobs.build_splits --current && "
        "python -m worker.jobs.build_usage_shares --current --sport cfb && "
        "python -m worker.jobs.ingest_coaches --current",
        requires_env=_CFB,
    ),
    Task(
        "cfb-props-project-week",
        "cfb-daily",
        "0 9 * * 2",
        "python -m worker.jobs.run_projections --all-weeks",
    ),
    Task(
        "cfb-props-game-lines",
        "cfb-daily",
        "0 10 * * *",
        "python -m worker.jobs.ingest_game_lines --live --current",
        requires_env=_CFB,
    ),
    Task(
        "cfb-props-weather",
        "cfb-daily",
        "0 11 * * *",
        "python -m worker.jobs.ingest_weather --current",
    ),
    Task(
        "cfb-props-healthcheck",
        "cfb-daily",
        "0 12 * * *",
        "python -m worker.jobs.healthcheck",
        requires_env=_CFB,
    ),
    Task(
        "cfb-props-project-current",
        "cfb-daily",
        "30 12 * * *",
        "python -m worker.jobs.run_projections --current-week",
    ),
    Task("cfb-props-audit", "cfb-daily", "0 13 * * *", "python -m worker.jobs.audit_data"),
    Task(
        "cfb-props-ai-reads", "cfb-daily", "0 14 * * 3", "python -m worker.jobs.generate_ai_reads"
    ),
    # ── NFL daily ────────────────────────────────────────────────────────────
    Task(
        "nfl-props-ingest-results",
        "nfl-daily",
        "20 8 * * *",
        "python -m worker.jobs.nfl_ingest_reference --current && "
        "python -m worker.jobs.nfl_ingest_stats --current && "
        "python -m worker.jobs.nfl_ingest_plays --current && "
        "python -m worker.jobs.build_splits --current --sport nfl && "
        "python -m worker.jobs.build_quarter_stats --current --sport nfl && "
        "python -m worker.jobs.build_usage_shares --current --sport nfl && "
        "python -m worker.jobs.nfl_ingest_charting --current && "
        "python -m worker.jobs.nfl_ingest_depth_charts --current && "
        "python -m worker.jobs.nfl_ingest_injuries --current; "
        "POLARS_MAX_THREADS=1 OMP_NUM_THREADS=1 "
        "python -m worker.jobs.fit_sim_engine --if-older-than-days 6",
    ),
    Task(
        "nfl-fantasy-inputs",
        "nfl-daily",
        "40 9,15 * * *",
        "python -m worker.jobs.nfl_fantasy_inputs --current; "
        "python -m worker.jobs.run_game_sims --pregame",
    ),
    Task(
        "nfl-props-project-current",
        "nfl-daily",
        "50 12 * * *",
        "python -m worker.jobs.run_projections --current-week --sport nfl",
    ),
)

TASKS_BY_NAME: dict[str, Task] = {t.name: t for t in TASKS}


# ── Cron ──────────────────────────────────────────────────────────────────────


def _field(spec: str, low: int, high: int) -> frozenset[int]:
    """One cron field: `*`, `*/n`, `a`, `a-b`, `a-b/n`, and lists of them."""
    values: set[int] = set()
    for part in spec.split(","):
        rng, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if rng == "*":
            start, end = low, high
        elif "-" in rng:
            a, b = rng.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(rng)
        if step < 1 or not (low <= start <= end <= high):
            raise ValueError(f"bad cron field {spec!r}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class Cron:
    """A five-field UTC cron expression (Render's). Day of month and month must
    be `*`: no schedule here uses them, and leaving them out avoids cron's odd
    OR rule between the two day fields."""

    minutes: frozenset[int]
    hours: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday, as in cron

    @classmethod
    def parse(cls, expression: str) -> Cron:
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(f"cron needs five fields: {expression!r}")
        minute, hour, dom, month, dow = parts
        if dom != "*" or month != "*":
            raise ValueError(f"day of month and month must be '*': {expression!r}")
        weekdays = _field(dow.replace("7", "0"), 0, 6)
        return cls(_field(minute, 0, 59), _field(hour, 0, 23), weekdays)

    def matches(self, moment: datetime) -> bool:
        cron_weekday = (moment.weekday() + 1) % 7  # Python Monday=0 -> cron Monday=1
        return (
            moment.minute in self.minutes
            and moment.hour in self.hours
            and cron_weekday in self.weekdays
        )

    def latest(self, now: datetime, lookback: timedelta) -> datetime | None:
        """The latest occurrence at or before `now` and within `lookback`."""
        moment = now.astimezone(UTC).replace(second=0, microsecond=0)
        earliest = now - lookback
        while moment >= earliest:
            if self.matches(moment):
                return moment
            moment -= timedelta(minutes=1)
        return None

    def next_after(self, now: datetime, horizon: timedelta = timedelta(days=8)) -> datetime | None:
        """The first occurrence strictly after `now`."""
        moment = now.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = now + horizon
        while moment <= end:
            if self.matches(moment):
                return moment
            moment += timedelta(minutes=1)
        return None


# ── What is due ───────────────────────────────────────────────────────────────


def tasks_in(group: str) -> list[Task]:
    if group not in GROUPS:
        raise ValueError(f"unknown dispatcher group {group!r}; known: {sorted(GROUPS)}")
    return [t for t in TASKS if t.group == group]


def due(
    group: str, now: datetime, since: datetime | None, claimed: Mapping[str, Iterable[datetime]]
) -> list[tuple[Task, datetime]]:
    """(task, slot) pairs to run now, in slot order then table order.

    Per task, its latest occurrence within the group's lookback and not before
    `since`, unless `claimed` (task name -> claimed slots) already holds it.
    Only the latest: three missed hours of a capture are one capture now.
    """
    lookback = GROUPS[group].lookback
    order = {t.name: i for i, t in enumerate(TASKS)}
    out = []
    for task in tasks_in(group):
        slot = Cron.parse(task.cron).latest(now, lookback)
        if slot is None or (since is not None and slot < since):
            continue
        if slot in set(claimed.get(task.name, ())):
            continue
        out.append((task, slot))
    return sorted(out, key=lambda ts: (ts[1], order[ts[0].name]))


def odds_deadline(now: datetime) -> datetime:
    """When an odds run must be done: a minute before the group's next slot
    with work in it, and never sooner than ODDS_MIN_BUDGET from now."""
    upcoming = [n for t in tasks_in("odds") if (n := Cron.parse(t.cron).next_after(now))]
    nearest = min(upcoming)
    return max(nearest - ODDS_DEADLINE_MARGIN, now + ODDS_MIN_BUDGET)


def child_env(task: Task, base: Mapping[str, str]) -> dict[str, str]:
    """The environment a task's command runs with: the service's, less what
    the task's old service did not have and less the dispatcher's own limit
    (each job keeps its own, see worker.time_limit), plus the task's name for
    its pipeline_runs metadata."""
    env = {
        k: v for k, v in base.items() if k not in task.drop_env and k != "JOB_TIME_LIMIT_SECONDS"
    }
    env["DISPATCH_TASK"] = task.name
    return env


def missing_env(task: Task, env: Mapping[str, str]) -> list[str]:
    return [k for k in task.requires_env if not (env.get(k) or "").strip()]


def render_yaml_crons(text: str) -> dict[str, tuple[str, str]]:
    """{service name: (schedule, start command)} for every cron in render.yaml.

    Regexes rather than a YAML library, as the tests always have: PyYAML is not
    a dependency (CLAUDE.md §0). A folded `startCommand: >-` is joined with
    single spaces, as YAML folds it; comment lines inside it are not part of it.
    """
    out: dict[str, tuple[str, str]] = {}
    for block in text.split("- type: cron")[1:]:
        name = re.search(r"^\s*name:\s*(\S+)", block, re.MULTILINE)
        schedule = re.search(r'^\s*schedule:\s*"([^"]+)"', block, re.MULTILINE)
        if not name or not schedule:
            raise ValueError("a cron service in render.yaml has no name or schedule")
        command = ""
        lines = block.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^(\s*)startCommand:\s*(.*)$", line)
            if not m:
                continue
            indent, rest = len(m.group(1)), m.group(2).strip()
            if rest not in (">-", ">", "|", "|-"):
                command = rest.strip('"')
                break
            parts = []
            for follow in lines[i + 1 :]:
                stripped = follow.strip()
                if stripped and len(follow) - len(follow.lstrip()) <= indent:
                    break
                if stripped and not stripped.startswith("#"):
                    parts.append(stripped)
            command = " ".join(parts)
            break
        out[name.group(1)] = (schedule.group(1), command)
    return out


def normalise(command: str) -> str:
    return " ".join(command.split())


def scheduled_commands(
    render_yaml_services: Mapping[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    """{name: (cron, command)} for everything that runs on a schedule: the
    services render.yaml schedules directly, other than the dispatchers and
    any old service a task has replaced, plus every task. For the tests that
    used to read render.yaml alone (monitor expectations, odds windows, docs)."""
    dispatchers = {g.service for g in GROUPS.values()}
    out = {
        name: v
        for name, v in render_yaml_services.items()
        if name not in dispatchers and name not in TASKS_BY_NAME
    }
    out.update({t.name: (t.cron, t.command) for t in TASKS})
    return out
