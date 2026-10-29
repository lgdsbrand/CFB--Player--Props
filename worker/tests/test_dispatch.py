"""The merged Render dispatchers (worker/dispatch.py, worker/jobs/dispatch.py).

Twenty cron services became three dispatchers plus the two that stayed
(2026-10-26). What must not change is WHAT runs and WHEN, so the tests below
hold three things:

  * the table is the old render.yaml: every task equal to its old service, for
    as long as that service is still in the file, and nothing left unaccounted;
  * each dispatcher's own schedule fires at every minute one of its tasks is due;
  * on a simulated Render (fires on the group's cron, a run that is still
    active DELAYS the next one, as Render's docs say) every occurrence of every
    task runs exactly once, never early, and close to its slot, including when
    a job hangs.
"""

from __future__ import annotations

import random
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker import dispatch as plan
from worker import time_limit
from worker.jobs import dispatch as job

RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"
MONDAY = datetime(2026, 10, 26, tzinfo=UTC)  # a Monday


def _services() -> dict[str, tuple[str, str]]:
    return plan.render_yaml_crons(RENDER_YAML.read_text(encoding="utf-8"))


# ── The table is the old schedule ─────────────────────────────────────────────


def test_render_yaml_parses_every_cron() -> None:
    services = _services()
    assert {"cfb-live-scores", "cfb-props-monitor", "worker-odds"} <= set(services)
    assert all(cron and command for cron, command in services.values())


def test_every_task_equals_its_old_service_while_that_is_in_render_yaml() -> None:
    services = _services()
    for task in plan.TASKS:
        if task.name not in services:
            continue  # removed after the switch-over: the table is the schedule now
        cron, command = services[task.name]
        assert cron == task.cron, task.name
        assert plan.normalise(command) == plan.normalise(task.command), task.name


def test_no_render_cron_is_unaccounted_for() -> None:
    dispatchers = {g.service for g in plan.GROUPS.values()}
    known = set(plan.DIRECT_SERVICES) | dispatchers | set(plan.TASKS_BY_NAME)
    assert set(_services()) <= known, set(_services()) - known


def test_each_dispatcher_service_runs_its_group_on_its_schedule() -> None:
    text = RENDER_YAML.read_text(encoding="utf-8")
    services = _services()
    for group in plan.GROUPS.values():
        cron, command = services[group.service]
        assert cron == group.cron
        assert plan.normalise(command) == f"python -m worker.jobs.dispatch --group {group.name}"
        block = text.split(f"name: {group.service}")[1].split("- type: cron")[0]
        assert f'value: "{group.limit_seconds}"' in block, group.service


def test_task_names_and_groups_are_sound() -> None:
    assert len(plan.TASKS_BY_NAME) == len(plan.TASKS)
    assert {t.group for t in plan.TASKS} == set(plan.GROUPS)
    for task in plan.TASKS:
        plan.Cron.parse(task.cron)  # raises on anything unsupported


def test_each_dispatcher_fires_whenever_one_of_its_tasks_is_due() -> None:
    crons = {g: plan.Cron.parse(plan.GROUPS[g].cron) for g in plan.GROUPS}
    tasks = [(t, plan.Cron.parse(t.cron)) for t in plan.TASKS]
    minute = MONDAY
    while minute < MONDAY + timedelta(days=14):
        for task, cron in tasks:
            if cron.matches(minute):
                assert crons[task.group].matches(minute), (task.name, minute)
        minute += timedelta(minutes=1)


def test_the_sports_daily_chains_stay_in_separate_services() -> None:
    assert all(t.name.startswith("cfb-") for t in plan.tasks_in("cfb-daily"))
    assert all(t.name.startswith("nfl-") for t in plan.tasks_in("nfl-daily"))


# ── Cron ──────────────────────────────────────────────────────────────────────


def test_cron_fields() -> None:
    c = plan.Cron.parse("0,20,30,40 9,15 * * 0,2,4,5")
    assert c.minutes == {0, 20, 30, 40} and c.hours == {9, 15} and c.weekdays == {0, 2, 4, 5}
    assert plan.Cron.parse("*/10 8-16 * * *").minutes == {0, 10, 20, 30, 40, 50}
    assert plan.Cron.parse("*/10 8-16 * * *").hours == set(range(8, 17))
    assert plan.Cron.parse("0 9 * * 7").weekdays == {0}
    with pytest.raises(ValueError):
        plan.Cron.parse("0 9 1 * *")
    with pytest.raises(ValueError):
        plan.Cron.parse("0 25 * * *")


def test_cron_weekday_is_cron_numbering() -> None:
    sunday = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    assert plan.Cron.parse("0 9 * * 0").matches(sunday)
    assert not plan.Cron.parse("0 9 * * 0").matches(sunday + timedelta(days=1))
    saturday = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
    assert plan.Cron.parse("0 15,19,23 * * 6").matches(saturday)


def test_latest_and_next() -> None:
    c = plan.Cron.parse("20 * * * *")
    now = datetime(2026, 10, 26, 10, 25, 31, tzinfo=UTC)
    assert c.latest(now, timedelta(minutes=60)) == datetime(2026, 10, 26, 10, 20, tzinfo=UTC)
    assert c.latest(now, timedelta(minutes=4)) is None
    assert c.next_after(now) == datetime(2026, 10, 26, 11, 20, tzinfo=UTC)
    assert c.next_after(datetime(2026, 10, 26, 10, 20, tzinfo=UTC)) == datetime(
        2026, 10, 26, 11, 20, tzinfo=UTC
    )


# ── due() ─────────────────────────────────────────────────────────────────────


def test_due_takes_the_latest_unclaimed_occurrence_in_slot_then_table_order() -> None:
    sat_2300 = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
    pairs = plan.due("odds", sat_2300 + timedelta(seconds=40), None, {})
    names = [t.name for t, _ in pairs]
    # 22:20 and 22:40 are within the hour's lookback and unclaimed: they come
    # first, oldest first, then 23:00 in table order.
    assert names == [
        "nfl-props-odds-refresh",
        "nfl-props-q1-capture",
        "cfb-game-odds-saturday",
        "cfb-props-odds-refresh",
    ]
    slots = [s for _, s in pairs]
    assert slots == sorted(slots)


def test_due_skips_claimed_and_pre_since_occurrences() -> None:
    now = datetime(2026, 10, 26, 10, 20, 30, tzinfo=UTC)
    claimed = {"nfl-props-odds-refresh": [datetime(2026, 10, 26, 10, 20, tzinfo=UTC)]}
    assert "nfl-props-odds-refresh" not in [t.name for t, _ in plan.due("odds", now, None, claimed)]
    since = datetime(2026, 10, 26, 10, 10, tzinfo=UTC)
    names = [t.name for t, _ in plan.due("odds", now, since, {})]
    assert names == ["nfl-props-odds-refresh"]  # 10:00 is before `since`


def test_unknown_group_is_refused() -> None:
    with pytest.raises(ValueError):
        plan.tasks_in("nba")


# ── Deadlines and environment ────────────────────────────────────────────────


def test_odds_deadline_is_a_minute_before_the_next_slot_with_work() -> None:
    t = lambda h, m, s=0: datetime(2026, 10, 26, h, m, s, tzinfo=UTC)  # noqa: E731
    assert plan.odds_deadline(t(10, 0, 30)) == t(10, 19)
    assert plan.odds_deadline(t(9, 20, 30)) == t(9, 29)  # 9:30 has the game model
    assert plan.odds_deadline(t(10, 20, 30)) == t(10, 39)  # 10:30 has nothing
    assert plan.odds_deadline(t(10, 38)) == t(10, 42)  # floor: four minutes


def test_child_env_keeps_each_task_on_its_old_keys() -> None:
    base = {
        "ODDS_API_KEY": "paid",
        "ODDS_API_KEY_FREE": "free",
        "SUPABASE_DB_URL": "db",
        "JOB_TIME_LIMIT_SECONDS": "1500",
    }
    q1 = plan.child_env(plan.TASKS_BY_NAME["nfl-props-q1-capture"], base)
    assert "ODDS_API_KEY_FREE" not in q1 and q1["ODDS_API_KEY"] == "paid"
    game_odds = plan.child_env(plan.TASKS_BY_NAME["cfb-game-odds-weekday"], base)
    assert game_odds["ODDS_API_KEY_FREE"] == "free"
    for env in (q1, game_odds):
        assert "JOB_TIME_LIMIT_SECONDS" not in env
    assert q1["DISPATCH_TASK"] == "nfl-props-q1-capture"


def test_only_the_game_odds_tasks_see_the_free_key() -> None:
    base = {"ODDS_API_KEY_FREE": "free"}
    sees = {t.name for t in plan.TASKS if "ODDS_API_KEY_FREE" in plan.child_env(t, base)}
    # The daily services never hold the key; in the odds service only these did.
    assert {n for n in sees if plan.TASKS_BY_NAME[n].group == "odds"} == {
        "cfb-game-odds-weekday",
        "cfb-game-odds-saturday",
    }


def test_missing_env() -> None:
    task = plan.TASKS_BY_NAME["cfb-game-odds-saturday"]
    assert plan.missing_env(task, {"SUPABASE_DB_URL": "x", "ODDS_API_KEY_FREE": " "}) == [
        "ODDS_API_KEY_FREE"
    ]
    assert plan.missing_env(task, {"SUPABASE_DB_URL": "x", "ODDS_API_KEY_FREE": "k"}) == []


def test_time_limit_honours_a_deadline() -> None:
    env = {"RENDER_SERVICE_NAME": "worker-odds", "JOB_DEADLINE_AT": "1000"}
    assert time_limit.limit_for("ingest_odds", env, now=700) == 300  # deadline first
    assert time_limit.limit_for("ingest_odds", env, now=-10_000) == 20 * 60  # own limit first
    assert time_limit.limit_for("ingest_odds", env, now=990) == time_limit.DEADLINE_FLOOR_SECONDS
    assert (
        time_limit.limit_for("ingest_odds", {"JOB_DEADLINE_AT": "1000"}, now=900) == 100
    )  # off Render too


# ── A simulated Render ────────────────────────────────────────────────────────

STARTUP = timedelta(seconds=40)

NORMAL_MINUTES = {
    "cfb-game-odds-weekday": 0.2,
    "cfb-game-odds-saturday": 0.2,
    "cfb-props-odds-refresh": 1.3,
    "nfl-props-odds-refresh": 1.8,
    "cfb-game-model": 0.3,
    "nfl-props-q1-capture": 0.5,
    "cfb-props-ingest-week": 21.5,
    "cfb-props-project-week": 12,
    "cfb-props-game-lines": 5.5,
    "cfb-props-weather": 3,
    "cfb-props-healthcheck": 1.1,
    "cfb-props-project-current": 2.3,
    "cfb-props-audit": 2.6,
    "cfb-props-ai-reads": 0.1,
    "cfb-power-ratings": 0.5,
    "nfl-power-ratings": 0.3,
    "nfl-props-ingest-results": 2.7,
    "nfl-fantasy-inputs": 1.9,
    "nfl-props-project-current": 0.8,
}


def _normal(task: plan.Task, slot: datetime) -> float:
    if task.name == "cfb-props-ingest-results":
        return 45.0 if slot.weekday() == 6 else 3.0  # Sunday after the Saturday slate
    return NORMAL_MINUTES[task.name]


def _simulate(group: str, minutes_for, days: int = 14, start: datetime = MONDAY):
    """Run one dispatcher on Render's rules; return [(task, slot, started)].

    Fires come from the group's cron. While a run is active the next fire is
    delayed; when it ends, a fire that fell inside it starts at once (one, not
    one per missed fire). Each task runs for minutes_for(task, slot), except
    that an odds task is stopped at its deadline (+ the kill grace)."""
    group_cron = plan.Cron.parse(plan.GROUPS[group].cron)
    claimed: dict[str, list[datetime]] = {}
    runs = []
    fire = group_cron.next_after(start - timedelta(minutes=1))
    end = start + timedelta(days=days)
    free_at = start
    while fire < end:
        begin = max(fire, free_at) + STARTUP
        clock = begin
        for task, slot in plan.due(group, clock, start, claimed):
            if group != "odds" and clock - begin > plan.DAILY_START_BY:
                break
            claimed.setdefault(task.name, []).append(slot)
            runs.append((task.name, slot, clock))
            took = timedelta(minutes=minutes_for(task, slot))
            if group == "odds":
                cap = (
                    plan.odds_deadline(clock)
                    - clock
                    + timedelta(seconds=job.KILL_GRACE_SECONDS + 30)
                )
                took = min(took, cap)
            clock += took
        free_at = clock
        nxt = group_cron.next_after(fire)
        if nxt is not None and nxt <= clock:
            # Fires that fell inside the run collapse into one delayed run.
            fire = group_cron.latest(clock, clock - fire)
        else:
            fire = nxt
    return runs


def _occurrences(group: str, days: int = 14, start: datetime = MONDAY):
    out = []
    for task in plan.tasks_in(group):
        cron = plan.Cron.parse(task.cron)
        minute = start
        while minute < start + timedelta(days=days):
            if cron.matches(minute):
                out.append((task.name, minute))
            minute += timedelta(minutes=1)
    return out


@pytest.mark.parametrize("group", sorted(plan.GROUPS))
def test_every_occurrence_runs_exactly_once_and_on_time(group: str) -> None:
    runs = _simulate(group, _normal)
    ran = [(name, slot) for name, slot, _ in runs]
    assert len(ran) == len(set(ran)), "an occurrence ran twice"
    # The last minutes of the window may still be pending; everything before ran.
    horizon = MONDAY + timedelta(days=14) - timedelta(hours=1)
    expected = {o for o in _occurrences(group) if o[1] < horizon}
    assert expected <= set(ran), sorted(expected - set(ran))[:5]
    worst = {
        "odds": timedelta(minutes=4),
        "cfb-daily": timedelta(minutes=75),
        "nfl-daily": timedelta(minutes=2),
    }[group]
    for name, slot, started in runs:
        assert started >= slot, (name, slot, started)
        assert started - slot <= worst, (name, slot, started - slot)


def test_a_hung_college_capture_cannot_take_the_nfl_slot() -> None:
    """The :00 chain hangs for an hour once a day; the :20 NFL capture must
    still start within a few minutes of :20, every hour."""

    def minutes(task, slot):
        if task.name == "cfb-props-odds-refresh" and slot.hour == 17:
            return 60.0
        return _normal(task, slot)

    runs = _simulate("odds", minutes)
    for name, slot, started in runs:
        if name == "nfl-props-odds-refresh":
            assert started - slot <= timedelta(minutes=4), (slot, started)
    ran = [(n, s) for n, s, _ in runs]
    assert len(ran) == len(set(ran))


def test_a_hung_daily_job_delays_its_sport_but_loses_nothing() -> None:
    def minutes(task, slot):
        if task.name == "cfb-props-game-lines":
            return 120.0  # stopped by its own two-hour limit
        return _normal(task, slot)

    runs = _simulate("cfb-daily", minutes)
    ran = [(n, s) for n, s, _ in runs]
    assert len(ran) == len(set(ran))
    horizon = MONDAY + timedelta(days=14) - timedelta(hours=1)
    expected = {o for o in _occurrences("cfb-daily") if o[1] < horizon}
    assert expected <= set(ran)
    for name, slot, started in runs:
        assert started.date() == slot.date(), (name, slot, started)


def test_random_delays_never_duplicate_or_run_early() -> None:
    rng = random.Random(20261026)
    for group in plan.GROUPS:
        runs = _simulate(group, lambda t, s: _normal(t, s) * rng.uniform(0.2, 6.0))
        ran = [(n, s) for n, s, _ in runs]
        assert len(ran) == len(set(ran)), group
        assert all(started >= slot for _, slot, started in runs), group


# ── The runner ────────────────────────────────────────────────────────────────


class _Fake:
    def __init__(self, monkeypatch, mode, since=None, claimed=None, exit_codes=None, taken=()):
        self.claims, self.records, self.ran, self.closed = [], [], [], []
        self.exit_codes = exit_codes or {}
        monkeypatch.setattr(job, "get_settings", lambda: SimpleNamespace(log_level="INFO"))
        monkeypatch.setattr(job, "configure_logging", lambda level: None)
        monkeypatch.setattr(job, "group_mode", lambda group: (mode, since))
        monkeypatch.setattr(job, "claimed_slots", lambda g, m, now: claimed or {})

        def claim(task, slot, m, note=None):
            self.claims.append((task.name, m, note))
            return task.name not in taken

        monkeypatch.setattr(job, "claim", claim)
        monkeypatch.setattr(
            job, "record", lambda task, slot, **f: self.records.append((task.name, f))
        )
        monkeypatch.setattr(
            job, "close_killed_runs", lambda task, started, note: self.closed.append(task.name)
        )

        def run_command(task, deadline):
            self.ran.append((task.name, deadline))
            return self.exit_codes.get(task.name, 0)

        monkeypatch.setattr(job, "run_command", run_command)


@pytest.fixture
def at_sat_2300(monkeypatch):
    moment = datetime(2026, 10, 24, 23, 0, 40, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    monkeypatch.setattr(job, "datetime", Clock)
    return moment


def test_off_runs_and_claims_nothing(monkeypatch, at_sat_2300) -> None:
    fake = _Fake(monkeypatch, "off")
    assert job.main(["--group", "odds"]) == 0
    assert fake.claims == [] and fake.ran == []


def test_shadow_records_without_running_and_flags_missing_keys(monkeypatch, at_sat_2300) -> None:
    monkeypatch.delenv("ODDS_API_KEY_FREE", raising=False)
    monkeypatch.setenv("ODDS_API_KEY", "paid")
    monkeypatch.setenv("SUPABASE_DB_URL", "db")
    fake = _Fake(monkeypatch, "shadow")
    assert job.main(["--group", "odds"]) == 0
    assert fake.ran == []
    notes = {name: note for name, mode, note in fake.claims if mode == "shadow"}
    assert notes["cfb-game-odds-saturday"] == "would run; MISSING ODDS_API_KEY_FREE"
    assert notes["cfb-props-odds-refresh"] == "would run"


def test_live_runs_due_tasks_in_order_with_odds_deadlines(monkeypatch, at_sat_2300) -> None:
    fake = _Fake(
        monkeypatch,
        "live",
        exit_codes={"cfb-game-odds-saturday": 1},
        taken={"nfl-props-q1-capture"},
    )
    assert job.main(["--group", "odds"]) == 1  # one task failed
    assert [n for n, _ in fake.ran] == [
        "nfl-props-odds-refresh",
        "cfb-game-odds-saturday",
        "cfb-props-odds-refresh",
    ]
    assert all(d is not None for _, d in fake.ran)
    finished = [f for n, f in fake.records if "exit_code" in f]
    assert [f["exit_code"] for f in finished] == [0, 1, 0]


def test_a_killed_task_has_its_open_runs_closed(monkeypatch, at_sat_2300) -> None:
    fake = _Fake(monkeypatch, "live", exit_codes={"cfb-props-odds-refresh": 124})
    job.main(["--group", "odds"])
    assert fake.closed == ["cfb-props-odds-refresh"]


def test_daily_tasks_get_no_deadline(monkeypatch) -> None:
    moment = datetime(2026, 10, 26, 8, 20, 40, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    monkeypatch.setattr(job, "datetime", Clock)
    fake = _Fake(monkeypatch, "live")
    assert job.main(["--group", "nfl-daily"]) == 0
    assert fake.ran == [("nfl-props-ingest-results", None)]


def test_run_command_passes_the_deadline_and_stops_an_overrun(monkeypatch) -> None:
    seen = {}

    class FakePopen:
        def __init__(self, args, env, start_new_session):
            seen["args"], seen["env"] = args, env
            self.pid = 4242
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                seen["timeout"] = timeout
                raise subprocess.TimeoutExpired("bash", timeout)
            return -15

        def send_signal(self, sig):
            seen["signal"] = sig

    monkeypatch.setattr(job.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        job.os, "killpg", lambda pid, sig: seen.setdefault("killpg", (pid, sig)), raising=False
    )
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    task = plan.TASKS_BY_NAME["cfb-props-odds-refresh"]
    assert job.run_command(task, deadline) == 124
    assert seen["args"][:2] == ["bash", "-c"] and seen["args"][2] == task.command
    assert seen["env"]["JOB_DEADLINE_AT"] == str(int(deadline.timestamp()))
    assert seen["env"]["DISPATCH_TASK"] == "cfb-props-odds-refresh"
    assert 5 * 60 < seen["timeout"] <= 5 * 60 + job.KILL_GRACE_SECONDS
