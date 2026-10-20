"""Backtest of the NFL game simulator against the live model, and the report (phase S1).

    python -m worker.jobs.run_sim_backtest                 # every test season, 1,000 sims a moment
    python -m worker.jobs.run_sim_backtest --test 2025 --sims 300   # quicker look

Writes docs/nfl-sim-backtest.html. Reads the database (games, plays, for the
pregame projection and the moments the live model was graded at) and
nflverse's public play-by-play (what the simulator learns from). No odds, no
API credits.

SAME ROWS AS THE LIVE MODEL. The moments are `core/live_backtest.checkpoints`
over the same games with the same Massey projection
(`run_live_backtest.nfl_prior_objects`), and both are graded by
`core/live_backtest.score`. A moment the simulator cannot start from (no
matching nflverse snap) is dropped from BOTH before grading.

WALK-FORWARD. For test season S the simulator learns from S−4 … S−1; its
team-strength spread (σθ) and the first-quarter/half baseline are fitted on
S−1; kickoffs are the season's before each game's week. The live model is
fitted on its own earlier seasons exactly as `run_live_backtest` does.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import polars as pl

from worker.adapters.nflverse.sim_plays import load_seasons, period_scores, sim_events, snap_states
from worker.config import REPO_ROOT, ConfigError, get_settings
from worker.core.live_backtest import (
    benchmark_frame,
    build_timelines,
    by_checkpoint,
    calibration,
    checkpoints,
    fit,
    fit_ep_table,
    predictions,
    pregame_only,
    score,
)
from worker.core.play_sim import (
    SimTables,
    concat,
    fit_quarter_offsets,
    fit_strength_curve,
    pregame,
    simulate,
    snap_start,
    solve_thetas,
)
from worker.core.sim_backtest import (
    PeriodBaseline,
    apply_scales,
    choose_sigma,
    fit_scales,
    period_calibration,
    period_rows,
    smooth,
    summarize,
    win_log_loss,
)
from worker.core.sim_backtest import (
    period_scores as grade_periods,
)
from worker.core.sim_report import render
from worker.db import get_config_value
from worker.jobs.run_live_backtest import _attach_benchmark, load, nfl_prior_objects
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

REPORT_PATH = REPO_ROOT / "docs" / "nfl-sim-backtest.html"

FIRST_SEASON = 2023  # the live backtest's first NFL season with a projection
TRAIN_SEASONS = 4
SIGMA_GRID = (0.0, 0.1, 0.2, 0.3, 0.4)
SIGMA_SIMS = 400
#: Games per moment on the season the win % scales are fitted on.
CAL_SIMS = 300
#: Moments per simulate() call: 40 × 1,000 sims keeps memory near 100 MB.
CHUNK = 40


def build_engine(test: int, current: int, pbp: pl.DataFrame, rng: np.random.Generator) -> dict:
    t0 = time.time()
    ev = sim_events(pbp.filter(pl.col("season") < test))
    ev.kickoffs = sim_events(pbp.filter(pl.col("season") >= test - 1)).kickoffs
    tables = SimTables.fit(ev)
    ref = tables.kick_pool(test - 1, 99)
    train = pbp.filter((pl.col("season") < test) & (pl.col("season_type") == "REG"))
    ps = period_scores(train)
    real_q = [
        float((ps["h1"] + ps["a1"]).mean()),
        float((ps["h2"] + ps["a2"] - ps["h1"] - ps["a1"]).mean()),
        float((ps["h3"] + ps["a3"] - ps["h2"] - ps["a2"]).mean()),
        float((ps["h4"] + ps["a4"] - ps["h3"] - ps["a3"]).mean()),
    ]
    offsets = fit_quarter_offsets(tables, ref, real_q, rng)
    curve = fit_strength_curve(tables, ref, rng)
    log.info(
        "engine for %d: fitted in %.0fs; quarter θ %s; curve %s",
        test,
        time.time() - t0,
        np.round(offsets, 3),
        np.round(curve.coef, 2),
    )
    return {
        "tables": tables,
        "curve": curve,
        "ref_pool": ref,
        "level": {ref: _level(tables, ref, rng)},
        "offsets": offsets,
        "real_q": real_q,
        "train_seasons": sorted(train["season"].unique().to_list()),
    }


#: Even-strength games per kickoff pool when measuring its scoring level.
LEVEL_SIMS = 8000


def _level(tables: SimTables, kick_pool: int, rng: np.random.Generator) -> float:
    res = simulate(tables, pregame(LEVEL_SIMS, rng, 0.0, 0.0, kick_pool), rng)
    return float((res.home.mean() + res.away.mean()) / 2)


def _thetas(engine: dict, prior, kick_pool: int, rng: np.random.Generator) -> tuple[float, float]:
    """θ for both offenses so the simulated means are the projection, in this week's kickoff environment.

    The strength curve is fitted with last season's kickoffs. A season whose
    kickoff rule moved drive starts (2024, 2025) scores differently at the
    same θ; the projection already carries that, so the curve is shifted by
    the difference, measured at even strength, or it would be counted twice.
    """
    levels = engine["level"]
    if kick_pool not in levels:
        levels[kick_pool] = _level(engine["tables"], kick_pool, rng)
    curve = engine["curve"].shifted(levels[kick_pool] - levels[engine["ref_pool"]])
    home = (prior.total_mean + prior.margin_mean) / 2
    away = (prior.total_mean - prior.margin_mean) / 2
    return solve_thetas(curve, home, away)


def fit_sigma(engine, games, priors, weeks, finals, rng, n: int = SIGMA_SIMS) -> tuple[float, dict]:
    """σθ with the best pregame log loss on the season before the test season."""
    tables = engine["tables"]
    out = {}
    th = {
        g: _thetas(engine, priors[g], tables.kick_pool(finals[g].season, weeks[g]), rng) for g in games
    }
    margins = np.array([finals[g].home_points - finals[g].away_points for g in games])
    for sigma in SIGMA_GRID:
        starts = []
        for g in games:
            h, a = th[g]
            kp = tables.kick_pool(finals[g].season, weeks[g])
            starts.append(
                pregame(
                    n,
                    rng,
                    h + sigma * rng.standard_normal(n),
                    a + sigma * rng.standard_normal(n),
                    kp,
                )
            )
        res = simulate(tables, concat(starts), rng)
        s = summarize(res, n)
        out[sigma] = win_log_loss(s.p_home_win, margins)
        log.info("σθ %.2f: pregame log loss %.4f", sigma, out[sigma])
    return choose_sigma(out), out


def period_baseline(engine, games, priors, nfl_id, ps_by_game) -> PeriodBaseline:
    pm, pt, q1, h1, hm = [], [], [], [], []
    for g in games:
        p = ps_by_game.get(nfl_id[g])
        if p is None:
            continue
        pm.append(priors[g].margin_mean)
        pt.append(priors[g].total_mean)
        q1.append(p["h1"] + p["a1"])
        h1.append(p["h2"] + p["a2"])
        hm.append(p["h2"] - p["a2"])
    return PeriodBaseline.fit(pm, pt, np.array(q1), np.array(h1), np.array(hm))


def simulate_moments(
    engine, season_rows, season_pbp, sims, sigma, rng, nfl_id, weeks, keep_pregame=False
):
    """Play every moment of a season `sims` times. Returns (frame, pregame summaries, dropped, mismatched).

    A moment is started from its nflverse snap; one with none is dropped.
    """
    tables = engine["tables"]
    states = snap_states(season_pbp)
    index = {
        (g, int(p)): i
        for i, (g, p) in enumerate(
            zip(states["game_id"].to_list(), states["play_id"].to_list(), strict=True)
        )
    }
    st = {c: states[c].to_numpy() for c in states.columns if c != "game_id"}
    usable, starts, missing, mismatch = [], [], 0, 0
    theta_cache: dict[int, tuple[float, float]] = {}
    for r in season_rows:
        gid = r.game_id
        kp = tables.kick_pool(r.season, weeks[gid])
        if gid not in theta_cache:
            theta_cache[gid] = _thetas(engine, r.prior, kp, rng)
        h, a = theta_cache[gid]
        th_h = h + sigma * rng.standard_normal(sims)
        th_a = a + sigma * rng.standard_normal(sims)
        if r.elapsed == 0:
            starts.append(pregame(sims, rng, th_h, th_a, kp))
            usable.append(r)
            continue
        i = index.get((nfl_id.get(gid), int(r.play_seq)))
        if i is None:
            missing += 1
            continue
        if (
            int(st["home_before"][i]) != r.state.home_points
            or int(st["away_before"][i]) != r.state.away_points
        ):
            mismatch += 1
        starts.append(
            snap_start(
                sims,
                gsr=st["gsr"][i],
                pos=st["pos"][i],
                down=st["down"][i],
                togo=st["togo"][i],
                ytg=st["ytg"][i],
                home=st["home_before"][i],
                away=st["away_before"][i],
                recv2=st["recv2"][i],
                theta_home=th_h,
                theta_away=th_a,
                kick_pool=kp,
            )
        )
        usable.append(r)
    season = season_rows[0].season if season_rows else None
    log.info(
        "%s: %d moments, %d with no nflverse snap (dropped), %d score mismatches",
        season,
        len(usable),
        missing,
        mismatch,
    )

    out_rows, pre = [], []
    t0 = time.time()
    for c0 in range(0, len(usable), CHUNK):
        chunk = usable[c0 : c0 + CHUNK]
        res = simulate(tables, concat(starts[c0 : c0 + CHUNK]), rng)
        s = summarize(res, sims)
        for j, r in enumerate(chunk):
            out_rows.append(
                {
                    "game_id": r.game_id,
                    "season": r.season,
                    "elapsed": r.elapsed,
                    "p_home_win": float(s.p_home_win[j]),
                    "margin_mean": float(s.margin_mean[j]),
                    "margin_hw": float(s.margin_hw[j]),
                    "total_mean": float(s.total_mean[j]),
                    "total_hw": float(s.total_hw[j]),
                    "final_margin": r.final_margin,
                    "final_total": r.final_total,
                }
            )
            if keep_pregame and r.elapsed == 0:
                pre.append((r, type(s)(**{k: v[j : j + 1] for k, v in s.__dict__.items()})))
        if (c0 // CHUNK) % 20 == 0:
            log.info(
                "%s: %d/%d moments simulated (%.0fs)",
                season,
                c0 + len(chunk),
                len(usable),
                time.time() - t0,
            )
    return pl.DataFrame(out_rows), pre, missing, mismatch


def run_test(
    test,
    current,
    rows,
    finals,
    finals_df,
    timelines,
    priors,
    sims,
    rng,
    sigma_sims=SIGMA_SIMS,
    cal_sims=None,
) -> dict:
    cal_sims = cal_sims or CAL_SIMS
    t_start = time.time()
    test_rows = [r for r in rows if r.season == test]
    train_rows = [r for r in rows if r.season < test]
    nfl_id = {int(r["game_id"]): r["nflverse_id"] for r in finals_df.iter_rows(named=True)}
    weeks = {int(r["game_id"]): int(r["week"]) for r in finals_df.iter_rows(named=True)}

    pbp = load_seasons(range(test - TRAIN_SEASONS, test + 1), current=current)
    engine = build_engine(test, current, pbp, rng)

    # σθ and the period baseline, on the season before.
    prev_games = [g for g in priors if finals[g].season == test - 1 and g in nfl_id]
    sigma, sigma_table = fit_sigma(engine, prev_games, priors, weeks, finals, rng, sigma_sims)
    prev_ps = period_scores(pbp.filter(pl.col("season") == test - 1))
    ps_prev = {r["game_id"]: r for r in prev_ps.iter_rows(named=True)}
    base = period_baseline(engine, prev_games, priors, nfl_id, ps_prev)
    log.info("σθ for %d = %.2f; period baseline %s", test, sigma, base)

    # The win % is fitted to wins on the season before (as the live model's is):
    # one scale per moment of the game, on the simulator's own earlier calls.
    prev_rows = [r for r in rows if r.season == test - 1]
    prev_frame, _, _, _ = simulate_moments(
        engine,
        prev_rows,
        pbp.filter(pl.col("season") == test - 1),
        cal_sims,
        sigma,
        rng,
        nfl_id,
        weeks,
    )
    scales = fit_scales(prev_frame, cal_sims)
    log.info(
        "win %% scales by moment for %d: %s", test, {e: round(k, 2) for e, k in scales.items()}
    )

    # The test season.
    season_pbp = pbp.filter(pl.col("season") == test)
    ps_test = {r["game_id"]: r for r in period_scores(season_pbp).iter_rows(named=True)}
    raw_frame, summaries, missing, mismatch = simulate_moments(
        engine, test_rows, season_pbp, sims, sigma, rng, nfl_id, weeks, keep_pregame=True
    )
    period_parts = []
    for r, s in summaries:
        act = ps_test.get(nfl_id.get(r.game_id))
        if act is None:
            continue
        period_parts.append(
            period_rows(
                s,
                base,
                [r.prior.margin_mean],
                [r.prior.total_mean],
                {
                    "q1_total": np.array([act["h1"] + act["a1"]], float),
                    "h1_total": np.array([act["h2"] + act["a2"]], float),
                    "h1_margin": np.array([act["h2"] - act["a2"]], float),
                },
            )
        )
    raw_frame = raw_frame.with_columns(
        p_home_win=pl.Series(smooth(raw_frame["p_home_win"].to_numpy(), sims))
    )
    sim_frame = apply_scales(raw_frame, scales)
    kick = raw_frame.filter(pl.col("elapsed") == 0)
    projected = {r.game_id: r.prior for r in test_rows if r.elapsed == 0}
    bias_t = np.mean([t - projected[g].total_mean for g, t in zip(kick["game_id"], kick["total_mean"], strict=True)])
    bias_m = np.mean([m - projected[g].margin_mean for g, m in zip(kick["game_id"], kick["margin_mean"], strict=True)])
    log.info("%d at kickoff: simulated minus projected total %+.2f, margin %+.2f", test, bias_t, bias_m)
    keys = sim_frame.select("game_id", "elapsed")

    ep = fit_ep_table([t for g, t in timelines.items() if finals[g].season < test], finals)
    _attach_benchmark(test_rows, finals_df, test)
    usable_set = set(zip(keys["game_id"].to_list(), keys["elapsed"].to_list(), strict=True))
    graded_rows = [r for r in test_rows if (r.game_id, r.elapsed) in usable_set]
    frames = {
        "simulator": sim_frame,
        "simulator, raw win %": raw_frame,
        "A: live model": predictions(graded_rows, fit(train_rows, "A", ep), ep),
        "pregame only": pregame_only(graded_rows),
    }
    bench = benchmark_frame(graded_rows)
    if bench.height:
        frames["nflverse win prob (benchmark)"] = bench
    periods = pl.concat(period_parts) if period_parts else pl.DataFrame()
    block = {
        "test": test,
        "train_seasons": engine["train_seasons"],
        "games": len({r.game_id for r in graded_rows}),
        "moments": len(graded_rows),
        "dropped": missing,
        "mismatch": mismatch,
        "sims": sims,
        "sigma": sigma,
        "sigma_table": sigma_table,
        "scales": scales,
        "cal_sims": cal_sims,
        "offsets": [float(x) for x in engine["offsets"]],
        "real_q": engine["real_q"],
        "curve": engine["curve"].coef,
        "baseline": base,
        "overall": {k: score(f.filter(pl.col("elapsed") > 0)) for k, f in frames.items()},
        "pregame": {
            k: score(f.filter(pl.col("elapsed") == 0))
            for k, f in frames.items()
            if k != "pregame only"
        },
        "by_checkpoint": {k: by_checkpoint(f) for k, f in frames.items()},
        "calibration": {
            k: calibration(f.filter(pl.col("elapsed") > 0))
            for k in ("simulator", "A: live model")
            for f in [frames[k]]
        },
        "periods": grade_periods(periods) if periods.height else [],
        "period_cal": {n: period_calibration(periods, n) for n in ("q1_total", "h1_total")}
        if periods.height
        else {},
        "frames": frames,
        "seconds": time.time() - t_start,
    }
    for k, o in block["overall"].items():
        log.info(
            "%d %-32s brier %.4f logloss %.4f %s",
            test,
            k,
            o["brier"],
            o["log_loss"],
            f"margin MAE {o['margin_mae']:.2f} total MAE {o['total_mae']:.2f}"
            if "margin_mae" in o
            else "",
        )
    return block


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sims", type=int, default=1000, help="Simulated games per moment.")
    parser.add_argument(
        "--test", type=int, action="append", help="Test season(s); default 2025 on."
    )
    parser.add_argument(
        "--sigma-sims", type=int, default=SIGMA_SIMS, help="Games per game when fitting σθ."
    )
    parser.add_argument(
        "--cal-sims", type=int, default=CAL_SIMS, help="Games per moment for the win %% scales."
    )
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigError as exc:
        configure_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    configure_logging(settings.log_level)

    current = int(get_config_value("current_season") or 2026)
    seasons = list(range(FIRST_SEASON, current + 1))
    finals_df, finals, plays = load("nfl", seasons)
    timelines = build_timelines(plays, finals, scores_after_play=False)
    priors, _, _ = nfl_prior_objects(finals_df, finals, FIRST_SEASON)
    rows = checkpoints(timelines, finals, priors)
    tests = args.test or [s for s in seasons if s >= 2025]
    rng = np.random.default_rng(20261020)
    blocks = [
        run_test(
            t,
            current,
            rows,
            finals,
            finals_df,
            timelines,
            priors,
            args.sims,
            rng,
            args.sigma_sims,
            args.cal_sims,
        )
        for t in tests
        if any(r.season == t for r in rows)
    ]
    REPORT_PATH.write_text(render(blocks), encoding="utf-8")
    log.info("Wrote %s", REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
