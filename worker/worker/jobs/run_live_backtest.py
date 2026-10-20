"""Backtest of the live (in-game) model, and the report (phase L1).

    python -m worker.jobs.run_live_backtest
    python -m worker.jobs.run_live_backtest --sport nfl
    python -m worker.jobs.run_live_backtest --write-params   # refit what runs live

`--write-params` then refits the recommended candidate on EVERY season with a
prior and writes core/live_params/<sport>.json, which the live poller reads.
Commit the diff: a refit is a reviewed change, done in the offseason (or once
a season has enough games), never automatically.

Writes docs/live-model-backtest.html. Reads the database only, plus nflverse's
public play-by-play file for the NFL benchmark (their own win probability).
No odds, no API credits.

WALK-FORWARD BY SEASON. College: fitted on 2023-2024, scored on 2025; then
fitted on 2023-2025 and scored on 2026's completed games. NFL the same over
its seasons (2023 on). The pregame prior is point in time: college, the game
model fitted on earlier seasons (what `run_game_backtest` scores); NFL, a
Massey rating from the season's games before the week (core/fantasy_inputs),
because the client's fantasy model has no history (CBS keeps none).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
import polars as pl

from worker.adapters.nflverse.assets import RELEASE_BASE
from worker.config import REPO_ROOT, ConfigError, get_settings
from worker.core.fantasy_inputs import FinalScore, massey
from worker.core.game_backtest import predict_season
from worker.core.game_data import load_games
from worker.core.game_model import RatingsModel
from worker.core.live_backtest import (
    Checkpoint,
    Final,
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
from worker.core.live_model import PARAMS_DIR, FittedModel, Prior
from worker.core.live_report import chosen, render
from worker.db import fetch_all, get_config_value
from worker.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

REPORT_PATH = REPO_ROOT / "docs" / "live-model-backtest.html"

#: (sport, first season with plays, scores after play?)
SPORTS = {"cfb": (2022, True), "nfl": (2023, False)}
#: p10-p90 of a normal spans 2 × 1.2816 sd.
P10_P90_SDS = 2 * 1.2815515655446004

_PLAYS_SQL = """
select p.game_id, coalesce(p.cfbd_id, p.nflverse_play_id) as seq, p.period, p.clock_seconds,
       p.offense_team_id, p.down, p.yards_to_goal, p.ppa::float8 as ppa,
       p.offense_score, p.defense_score
  from plays p
  join games g on g.id = p.game_id
 where g.sport = %(sport)s and g.season = any(%(seasons)s) and g.completed
"""

_FINALS_SQL = """
select id as game_id, season, week, season_type::text as season_type, neutral_site,
       home_team_id, away_team_id, home_points, away_points, nflverse_id
  from games
 where sport = %(sport)s and season = any(%(seasons)s) and completed
   and home_points is not null and away_points is not null
"""


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, infer_schema_length=None)


def load(sport: str, seasons: list[int]):
    params = {"sport": sport, "seasons": seasons}
    finals_df = _frame(fetch_all(_FINALS_SQL, params))
    finals = {
        int(r["game_id"]): Final(
            int(r["game_id"]),
            int(r["season"]),
            int(r["home_team_id"]),
            int(r["away_team_id"]),
            int(r["home_points"]),
            int(r["away_points"]),
        )
        for r in finals_df.iter_rows(named=True)
    }
    plays = _frame(fetch_all(_PLAYS_SQL, params))
    log.info("%s: %d finals, %d plays", sport, len(finals), plays.height)
    return finals_df, finals, plays


def cfb_priors(seasons: list[int], test_seasons: list[int]) -> dict[int, Prior]:
    """The college game model's pregame projection, fitted on seasons before each test season."""
    games = load_games(seasons, "cfb")
    done = games.filter(pl.col("completed"))
    priors: dict[int, Prior] = {}
    for season in test_seasons:
        model = RatingsModel().fit(done.filter(pl.col("season") < season))
        frame = predict_season(model, done.filter(pl.col("season") == season))
        for r in frame.iter_rows(named=True):
            if r["pred_margin"] is None or not np.isfinite(r["pred_margin"]):
                continue
            priors[int(r["game_id"])] = Prior(
                float(r["pred_margin"]),
                float((r["p90_margin"] - r["p10_margin"]) / P10_P90_SDS),
                float(r["pred_total"]),
                float((r["p90_total"] - r["p10_total"]) / P10_P90_SDS),
            )
    return priors


def nfl_priors(finals_df: pl.DataFrame) -> dict[int, tuple[float, float]]:
    """(margin mean, total mean) per regular-season game from a Massey fit on games before its week.

    Week 1 has no games yet, so it uses the previous season's final ratings
    pulled halfway to average (or the league average when there is none).
    """
    regular = finals_df.filter(pl.col("season_type") == "regular")
    out: dict[int, tuple[float, float]] = {}
    previous: tuple[dict, dict, float, float] | None = None
    for season in sorted(regular["season"].unique().to_list()):
        rows = regular.filter(pl.col("season") == season)
        scores = [
            FinalScore(
                int(r["week"]),
                int(r["home_team_id"]),
                int(r["away_team_id"]),
                int(r["home_points"]),
                int(r["away_points"]),
                bool(r["neutral_site"]),
            )
            for r in rows.iter_rows(named=True)
        ]
        teams = sorted({s.home_id for s in scores} | {s.away_id for s in scores})
        for week in sorted({s.week for s in scores}):
            before = [s for s in scores if s.week < week]
            if before:
                off, de, hfa = massey(before, teams)
                league = float(np.mean([p for s in before for p in (s.home_points, s.away_points)]))
            elif previous is not None:
                p_off, p_def, hfa, league = previous
                off = {t: league + (p_off.get(t, league) - league) / 2 for t in teams}
                de = {t: p_def.get(t, 0.0) / 2 for t in teams}
            else:
                off, de, hfa, league = {t: 22.0 for t in teams}, {t: 0.0 for t in teams}, 1.7, 22.0
            for s in scores:
                if s.week != week:
                    continue
                home = off[s.home_id] - de[s.away_id] + (0.0 if s.neutral else hfa)
                away = off[s.away_id] - de[s.home_id]
                out[_game_id(rows, s)] = (home - away, home + away)
        off, de, hfa = massey(scores, teams)
        league = float(np.mean([p for s in scores for p in (s.home_points, s.away_points)]))
        previous = (off, de, hfa, league)
    return out


def nfl_prior_objects(
    finals_df: pl.DataFrame, finals: dict[int, Final], first_season: int
) -> tuple[dict[int, Prior], float, float]:
    """`nfl_priors` as Prior objects, with one league sd from the first season's misses.

    Shared with the game simulator's backtest (`run_sim_backtest`), so both
    are graded from the same pregame projection.
    """
    means = nfl_priors(finals_df)
    base = [g for g, f in finals.items() if g in means and f.season == first_season]
    sd_m = float(
        np.std([finals[g].home_points - finals[g].away_points - means[g][0] for g in base])
    )
    sd_t = float(
        np.std([finals[g].home_points + finals[g].away_points - means[g][1] for g in base])
    )
    return {g: Prior(m, sd_m, t, sd_t) for g, (m, t) in means.items()}, sd_m, sd_t


def _game_id(rows: pl.DataFrame, s: FinalScore) -> int:
    hit = rows.filter(
        (pl.col("week") == s.week)
        & (pl.col("home_team_id") == s.home_id)
        & (pl.col("away_team_id") == s.away_id)
    )
    return int(hit["game_id"][0])


def nflverse_wp(season: int) -> pl.DataFrame:
    """nflverse's own home win probability per play (their model, no market)."""
    cache = Path(tempfile.gettempdir()) / f"nflverse_pbp_{season}.parquet"
    if not cache.exists():
        url = f"{RELEASE_BASE}/pbp/play_by_play_{season}.parquet"
        log.info("Downloading %s", url)
        # Standard library, as the nflverse adapter does: no new dependency.
        partial = cache.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=300) as r, partial.open("wb") as fh:
            shutil.copyfileobj(r, fh)
        partial.replace(cache)
    return pl.read_parquet(cache, columns=["game_id", "play_id", "home_wp", "vegas_home_wp"])


def run_sport(sport: str, current: int, *, write_params: bool = False) -> dict:
    first, after = SPORTS[sport]
    seasons = list(range(first, current + 1))
    finals_df, finals, plays = load(sport, seasons)
    timelines = build_timelines(plays, finals, scores_after_play=after)
    log.info("%s: %d game timelines", sport, len(timelines))

    prior_seasons = [s for s in seasons if s >= 2023]
    if sport == "cfb":
        priors = cfb_priors(seasons, prior_seasons)
    else:
        priors, sd_m, sd_t = nfl_prior_objects(finals_df, finals, prior_seasons[0])
    rows = checkpoints(timelines, finals, priors)
    prior_sds = (sd_m, sd_t) if sport == "nfl" else (None, None)
    log.info("%s: %d checkpoint rows from %d games with a prior", sport, len(rows), len(priors))

    blocks = []
    for test in [s for s in prior_seasons if s >= 2025]:
        train_seasons = [s for s in prior_seasons if s < test]
        train = [r for r in rows if r.season in train_seasons]
        test_rows = [r for r in rows if r.season == test]
        if not test_rows:
            continue
        ep = fit_ep_table([t for g, t in timelines.items() if finals[g].season < test], finals)
        if sport == "nfl":
            _attach_benchmark(test_rows, finals_df, test)
        frames = {
            "pregame only": pregame_only(test_rows),
            "score + clock, no prior": predictions(test_rows, fit(train, "base", ep), ep),
            "A: score, clock, field position": predictions(test_rows, fit(train, "A", ep), ep),
            "B: A + how the game has played": predictions(test_rows, fit(train, "B", ep), ep),
        }
        bench = benchmark_frame(test_rows)
        if bench.height:
            frames["nflverse win prob (benchmark)"] = bench
        params = {c: fit(train, c, ep) for c in ("A", "B")}
        blocks.append(
            {
                "test": test,
                "train": train_seasons,
                "games": len({r.game_id for r in test_rows}),
                "overall": {k: score(f.filter(pl.col("elapsed") > 0)) for k, f in frames.items()},
                "by_checkpoint": {k: by_checkpoint(f) for k, f in frames.items()},
                "calibration": {
                    k: calibration(f.filter(pl.col("elapsed") > 0))
                    for k, f in frames.items()
                    if not k.startswith("pregame")
                },
                "params": params,
                "ep": ep,
            }
        )
        for k, f in frames.items():
            o = score(f.filter(pl.col("elapsed") > 0))
            log.info(
                "%s %d %-34s brier %.4f logloss %.4f %s",
                sport,
                test,
                k,
                o["brier"],
                o["log_loss"],
                f"margin MAE {o['margin_mae']:.2f} total MAE {o['total_mae']:.2f} "
                f"cover80 {o['margin_cover80']:.3f}/{o['total_cover80']:.3f}"
                if "margin_mae" in o
                else "",
            )
    result = {"sport": sport, "blocks": blocks}
    if write_params:
        candidate = chosen(result)
        fitted_on = sorted({r.season for r in rows})
        ep = fit_ep_table(list(timelines.values()), finals)
        model = FittedModel(sport, fit(rows, candidate, ep), ep, fitted_on, *prior_sds)
        path = PARAMS_DIR / f"{sport}.json"
        PARAMS_DIR.mkdir(exist_ok=True)
        path.write_text(model.to_json() + "\n", encoding="utf-8")
        log.info("%s: wrote candidate %s fitted on %s to %s", sport, candidate, fitted_on, path)
    return result


def _attach_benchmark(rows: list[Checkpoint], finals_df: pl.DataFrame, season: int) -> None:
    try:
        wp = nflverse_wp(season)
    except Exception as exc:  # the benchmark is optional; the report says when it is missing
        log.warning("nflverse play-by-play for %d unavailable: %s", season, exc)
        return
    ids = {int(r["game_id"]): r["nflverse_id"] for r in finals_df.iter_rows(named=True)}
    lookup = {
        (g, int(p)): w
        for g, p, w in zip(
            wp["game_id"].to_list(), wp["play_id"].to_list(), wp["home_wp"].to_list(), strict=True
        )
        if p is not None and w is not None
    }
    for r in rows:
        r.benchmark = lookup.get((ids.get(r.game_id), r.play_seq))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=("cfb", "nfl", "both"), default="both")
    parser.add_argument(
        "--write-params",
        action="store_true",
        help="Refit the recommended candidate on every season and write core/live_params/.",
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
    sports = ("cfb", "nfl") if args.sport == "both" else (args.sport,)
    results = [run_sport(s, current, write_params=args.write_params) for s in sports]
    REPORT_PATH.write_text(render(results), encoding="utf-8")
    log.info("Wrote %s", REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
