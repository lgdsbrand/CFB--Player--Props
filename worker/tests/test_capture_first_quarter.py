"""The scheduled first-quarter capture: what it asks `ingest_odds.run` for.

No network, no database. `ingest_odds.run` itself — the kickoff window, the
started-game guard, the market check — is tested in `test_ingest_odds.py`; this
file pins the choices this job makes on top of it, and the one thing that ties
it to `render.yaml`.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from types import SimpleNamespace

from worker.jobs import capture_first_quarter as job
from worker.jobs.ingest_odds import IngestReport

RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"


def _run(monkeypatch, argv=None) -> tuple[int, dict, dict]:
    called: dict = {}
    logged: dict = {}

    monkeypatch.setattr(job, "get_settings", lambda: SimpleNamespace(log_level="INFO"))
    monkeypatch.setattr(job, "resolve_adapter_name", lambda explicit: "theoddsapi")
    monkeypatch.setattr(
        job, "resolve_slate_args", lambda season, week, sport: (2026, 2)
    )

    @contextlib.contextmanager
    def pipeline_run(name, metadata):
        logged.update(name=name, metadata=metadata)
        yield "run-id"

    monkeypatch.setattr(job, "pipeline_run", pipeline_run)
    monkeypatch.setattr(job, "set_rows_written", lambda run_id, n: None)

    def run(**kwargs):
        called.update(kwargs)
        return IngestReport()

    monkeypatch.setattr(job, "run", run)
    return job.main(argv or []), called, logged


def test_it_asks_for_the_first_quarter_markets_of_the_next_hour(monkeypatch):
    code, called, _ = _run(monkeypatch)

    assert code == 0
    assert called["sport"] == "nfl"
    assert called["markets"] == ["q1_pass_yards", "q1_rec_yards", "q1_rush_yards"]
    assert called["kickoff_within_minutes"] == 60
    # The window picks the games, not the week.
    assert called["week"] is None
    assert called["season"] == 2026


def test_it_bills_the_free_key(monkeypatch):
    _, called, _ = _run(monkeypatch)
    assert called["prefer_free"] is True


def test_it_logs_under_its_own_name_so_it_cannot_answer_for_ingest_odds(monkeypatch):
    """Logged as `ingest_odds` + nfl, every hourly run would read as a fresh
    full-game NFL capture, hiding a dead six-hourly cron from the monitor."""
    _, _, logged = _run(monkeypatch)
    assert logged["name"] == "capture_first_quarter"
    assert logged["metadata"]["sport"] == "nfl"


def test_a_non_positive_window_is_refused(monkeypatch):
    code, called, _ = _run(monkeypatch, ["--window-minutes", "0"])
    assert code == 2
    assert called == {}


def test_the_window_tiles_the_cron_schedule():
    """Every kickoff must fall in exactly one run's window: the window has to
    equal the cron period. An hourly cron with a shorter window skips games; a
    longer one pays for some twice."""
    text = RENDER_YAML.read_text(encoding="utf-8")
    block = next(
        b for b in text.split("- type: cron")
        if "worker.jobs.capture_first_quarter" in b
    )
    schedule = re.search(r'schedule:\s*"([^"]+)"', block).group(1)
    minute, hour, *rest = schedule.split()
    assert minute.isdigit() and hour == "*" and rest == ["*", "*", "*"], schedule
    assert job.WINDOW_MINUTES == 60
