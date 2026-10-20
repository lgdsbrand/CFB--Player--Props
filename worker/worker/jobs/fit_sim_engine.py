"""Fit the NFL game simulator and save it (phase S2).

    python -m worker.jobs.fit_sim_engine                      # fit and save
    python -m worker.jobs.fit_sim_engine --if-older-than-days 6   # what the daily chain runs
    python -m worker.jobs.fit_sim_engine --dry-run            # fit, report, save nothing

WHY A SAVED ENGINE. Fitting takes about two minutes (a play-choice model, the
draw pools, the quarter adjustments and the strength curve); the live poller
runs every two minutes and must start a simulation in seconds. So this fits
once a week and `run_game_sims` loads the result from `sim_engines`.

WHAT IT LEARNS FROM: nflverse's play-by-play for the four seasons before the
current one plus the current season so far (`adapters/nflverse/sim_plays.py`),
the setup the S1 backtest measured (docs/nfl-sim-backtest.html). Kickoffs are
this season's when there are enough (150), else last season's: the kickoff
rule changed in 2024 and 2025. Team-strength spread σθ is the value the
backtest fitted for 2026.

No odds, no API credits. Runs inside the NFL results chain after the plays
are in; `--if-older-than-days` makes six of its seven daily runs a no-op.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import sklearn

from worker.adapters.nflverse.sim_plays import events_by_season
from worker.config import ConfigError, get_settings
from worker.core.play_sim import SimTables, fit_quarter_offsets, fit_strength_curve
from worker.core.sim_runtime import Engine
from worker.db import fetch_one, get_config_value, pipeline_run, set_rows_written, upsert
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

JOB_NAME = "fit_sim_engine"
SPORT = "nfl"
TRAIN_SEASONS = 4
#: Team-strength spread; the S1 backtest's fit for 2026 (docs/nfl-sim-backtest.html).
SIGMA_THETA = 0.10
MIN_KICKOFFS = 150


def fit(current: int, rng: np.random.Generator) -> Engine:
    t0 = time.time()
    seasons = list(range(current - TRAIN_SEASONS, current + 1))
    ev, ps = events_by_season(seasons, current=current)
    this = ev.kickoffs.filter(pl.col("season") == current)
    ev.kickoffs = (
        this if this.height >= MIN_KICKOFFS else ev.kickoffs.filter(pl.col("season") == current - 1)
    )
    kick_season = int(ev.kickoffs["season"].max())
    tables = SimTables.fit(ev)
    pool = tables.whole_kick_pool()
    real_q = [
        float((ps["h1"] + ps["a1"]).mean()),
        float((ps["h2"] + ps["a2"] - ps["h1"] - ps["a1"]).mean()),
        float((ps["h3"] + ps["a3"] - ps["h2"] - ps["a2"]).mean()),
        float((ps["h4"] + ps["a4"] - ps["h3"] - ps["a3"]).mean()),
    ]
    offsets = fit_quarter_offsets(tables, pool, real_q, rng)
    curve = fit_strength_curve(tables, pool, rng)
    last_week = int(this["week"].max() or 0) if this.height else 0
    meta = {
        "seasons": seasons,
        "through_week": last_week,
        "kickoffs": {"season": kick_season, "n": ev.kickoffs.height},
        "quarter_theta": [round(float(x), 4) for x in offsets],
        "real_quarters": [round(x, 3) for x in real_q],
        "curve": [round(c, 4) for c in curve.coef],
        "sigma": SIGMA_THETA,
        "plays": ev.scrimmage.height,
        "sklearn": sklearn.__version__,
        "seconds": round(time.time() - t0),
    }
    log.info("Fitted the %s simulator in %ds: %s", SPORT, meta["seconds"], meta)
    return Engine(tables, curve, SIGMA_THETA, pool, meta)


def version_name(meta: dict) -> str:
    return f"S2 {meta['seasons'][0]}-{meta['seasons'][-1]} wk{meta['through_week']}"


def is_fresh(days: float) -> bool:
    row = fetch_one("select fitted_at from sim_engines where sport = %s", (SPORT,))
    return bool(row) and row["fitted_at"] > datetime.now(UTC) - timedelta(days=days)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--if-older-than-days", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    if args.if_older_than_days is not None and is_fresh(args.if_older_than_days):
        log.info(
            "The %s simulator is younger than %s days; nothing to do.",
            SPORT,
            args.if_older_than_days,
        )
        return 0
    current = int(get_config_value("current_season") or datetime.now(UTC).year)
    rng = np.random.default_rng()
    if args.dry_run:
        engine = fit(current, rng)
        log.info("Dry run: %d bytes, not saved.", len(engine.to_bytes()))
        return 0
    with pipeline_run(JOB_NAME, metadata={"sport": SPORT}) as run_id:
        engine = fit(current, rng)
        blob = engine.to_bytes()
        upsert(
            "sim_engines",
            [
                {
                    "sport": SPORT,
                    "version": version_name(engine.meta),
                    "meta": json.dumps(engine.meta),
                    "payload": blob,
                    "fitted_at": datetime.now(UTC),
                }
            ],
            conflict_columns=["sport"],
        )
        log.info("Saved %s (%d bytes).", version_name(engine.meta), len(blob))
        set_rows_written(run_id, 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
