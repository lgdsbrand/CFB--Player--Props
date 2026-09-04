"""The daily `--current-week` run, and why it is not the Tuesday one.

WHY THIS FILE EXISTS. `ingest_odds` writes `player_prop_lines`; `picks` — where
the board reads the line, the price and the edge from — is written only by
`run_projections`. Between those two facts sits a whole class of outage that
looks like an odds problem and is not: lines land on schedule, the provider is
healthy, every job reports success, and the board goes stale anyway.

That is not hypothetical. On 2026-09-04, with the odds cron working perfectly,
8,989 of week 1's 10,866 captured lines had no pick attached and the live board
on the Friday before a Saturday slate was quoting a capture 23 hours old,
because the only scheduled `run_projections` was `--all-weeks` on Tuesdays and
college books post props Thursday or Friday.

Nothing about that failure is visible to the type checker, and no test in the
suite went red for it. These tests pin the two halves that fix it: that the
daily run exists in `render.yaml`, and that its "nothing is live" path stays a
recorded success rather than becoming either an error or a silence.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest

from worker.jobs import run_projections

RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"


# -----------------------------------------------------------------------------
# The "nothing is live" path
# -----------------------------------------------------------------------------
@pytest.fixture
def no_live_weeks(monkeypatch):
    """Run `main()` with no database, no live week, and a recorded run captured.

    Yields the list the fake `pipeline_run` appends its metadata to, so a test
    can assert on what was recorded as well as on the exit code.
    """
    recorded: list[dict] = []

    @contextlib.contextmanager
    def fake_pipeline_run(job_name, metadata=None):
        recorded.append({"job_name": job_name, "metadata": metadata or {}})
        yield None

    class _Settings:
        log_level = "INFO"
        environment = "production"

    monkeypatch.setattr(run_projections, "get_settings", lambda: _Settings())
    monkeypatch.setattr(run_projections, "resolve_season", lambda explicit: 2026)
    monkeypatch.setattr(run_projections, "live_weeks", lambda season: [])
    monkeypatch.setattr(run_projections, "pipeline_run", fake_pipeline_run)
    return recorded


def test_no_live_week_exits_zero(no_live_weeks):
    """The off-season is not a failure.

    A daily cron that goes red every day from January to August is an alert
    everyone has learned to ignore by the week in September when it matters.
    """
    assert run_projections.main(["--current-week"]) == 0


def test_no_live_week_is_still_recorded_as_a_run(no_live_weeks):
    """And the other direction, which is the easier mistake to make.

    `monitor_pipeline` measures staleness from the last SUCCEEDED run and holds
    `run_projections` to 36 hours. Returning early without writing a row would
    make a job that is working perfectly indistinguishable from one that has
    stopped firing — a false alarm rather than a missed one, but just as
    corrosive to the alert.
    """
    run_projections.main(["--current-week"])

    assert len(no_live_weeks) == 1, "the no-op path must still record a run"
    run = no_live_weeks[0]
    assert run["job_name"] == run_projections.JOB_NAME
    assert run["metadata"]["weeks"] == []
    assert "skipped" in run["metadata"], (
        "the row must say WHY it wrote nothing, or reading pipeline_runs six "
        "months later cannot tell a quiet week from a broken job"
    )


def test_no_live_week_does_not_touch_the_model(no_live_weeks, monkeypatch):
    """It must return before any of the expensive machinery.

    Calibration loading and `project_slate` both hit the database. If the early
    return ever moves below them, the off-season cron starts doing real work —
    and failing — for a slate that does not exist.
    """
    def explode(*args, **kwargs):  # pragma: no cover - the point is not calling it
        raise AssertionError("the no-live-week path reached the model")

    monkeypatch.setattr(run_projections, "load_calibration", explode)
    monkeypatch.setattr(run_projections, "project_slate", explode)

    assert run_projections.main(["--current-week"]) == 0


def test_asking_for_no_weeks_at_all_still_errors(monkeypatch):
    """`--current-week` must not become an accidental default.

    Bare `run_projections` has always been an error rather than a guess, and it
    should stay one: the three selectors mean genuinely different populations.
    """
    class _Settings:
        log_level = "INFO"
        environment = "production"

    monkeypatch.setattr(run_projections, "get_settings", lambda: _Settings())
    monkeypatch.setattr(run_projections, "resolve_season", lambda explicit: 2026)

    assert run_projections.main([]) == 2


# -----------------------------------------------------------------------------
# render.yaml
# -----------------------------------------------------------------------------
def _cron_blocks() -> list[str]:
    text = RENDER_YAML.read_text(encoding="utf-8")
    blocks = text.split("- type: cron")[1:]
    assert blocks, "no cron services parsed out of render.yaml — the regex broke"
    return blocks


def test_a_daily_current_week_cron_exists() -> None:
    """The deployed half of the fix.

    The flag can exist, be tested, and be documented while nothing in
    production ever passes it — which is exactly the state this repo was in on
    2026-09-04, with a perfectly good `--weeks` argument nobody was running.
    """
    daily = [
        b for b in _cron_blocks()
        if "run_projections --current-week" in b
        and re.search(r'^\s*schedule:\s*"\S+ \S+ \* \* \*"', b, re.MULTILINE)
    ]
    assert daily, (
        "no daily cron runs `run_projections --current-week` — captured odds "
        "would sit unpriced until the weekly --all-weeks run, which is the "
        "outage this flag was added to close"
    )


def test_the_daily_run_is_not_all_weeks() -> None:
    """Frequency and scope are separate decisions, and this one is deliberate.

    `--all-weeks` projects every week the schedule contains, most of which
    nobody can bet yet. Running that daily would be a large amount of work to
    rewrite rows no reader can act on.
    """
    for block in _cron_blocks():
        if "run_projections --all-weeks" not in block:
            continue
        schedule = re.search(r'^\s*schedule:\s*"([^"]+)"', block, re.MULTILINE)
        assert schedule
        day_of_week = schedule.group(1).split()[4]
        assert day_of_week != "*", (
            "--all-weeks is scheduled daily; it is the whole-season refresh and "
            "belongs on a weekly cadence, with --current-week carrying the day "
            "to day"
        )


def test_the_daily_run_lands_after_an_odds_capture() -> None:
    """Ordering is the entire value of the job.

    Projecting before the day's capture prices yesterday's lines and reports
    success, which is a subtler version of the bug being fixed. `ingest_odds`
    runs at 00/06/12/18 UTC; the worst duration measured on production is 157s,
    so the daily projection needs to sit clear of the top of one of those hours.
    """
    captures = {0, 6, 12, 18}

    for block in _cron_blocks():
        if "run_projections --current-week" not in block:
            continue
        schedule = re.search(r'^\s*schedule:\s*"([^"]+)"', block, re.MULTILINE)
        assert schedule
        minute, hour, _, _, _ = schedule.group(1).split()
        assert hour.isdigit() and minute.isdigit(), (
            f"expected a fixed daily time, got {schedule.group(1)!r}"
        )
        assert int(hour) in captures, (
            f"the daily projection runs at hour {hour}, which is not one of the "
            f"odds-capture hours {sorted(captures)} — it would price lines from "
            "an earlier capture"
        )
        assert int(minute) >= 10, (
            f"only {minute} minutes after the capture starts; ingest_odds has "
            "taken 157s on production and this would race it"
        )
