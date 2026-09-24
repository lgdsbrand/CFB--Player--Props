"""Walk-forward backtest of the game models (CLAUDE.md §11, G3).

POINT IN TIME TWICE OVER. Across seasons: season S is predicted by a model
trained only on seasons before S. Within a season: every feature row was
built from games before that game's week (migration 0077, `game_data`). The
closing line enters ONLY here, after prediction, to grade.

What is measured, per model, per test season and per evidence phase:

  * accuracy against the market: mean absolute error of the predicted margin
    and total, beside the closing line's own error on the same games;
  * cover and over calibration: when the model says 60%, how often it hits;
  * win probability: Brier score beside the vig-free moneyline's;
  * picks at the house edge threshold (CLAUDE.md §6): edge = model probability
    minus the vig-free book probability. CFBD's spread and total carry no
    prices, so they are taken as -110 both ways, i.e. a vig-free 50%;
  * 1Q and 1H: no historical lines exist, so accuracy and 80%-interval
    coverage only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from worker.core.game_model import (
    BoostedModel,
    GameModel,
    RatingsModel,
    devig_moneyline,
    evidence_phase,
    side_probabilities,
)

EDGE_THRESHOLD = 0.05
PRICE_110_PAYOUT = 100.0 / 110.0
CAL_BINS = (0.0, 0.40, 0.45, 0.50, 0.55, 0.60, 1.0)


@dataclass
class SeasonRun:
    season: int
    model: str
    frame: pl.DataFrame  # one row per test game, predictions + outcomes + market


def _num(df: pl.DataFrame, c: str) -> np.ndarray:
    return df[c].cast(pl.Float64).fill_null(np.nan).to_numpy()


def predict_season(model: GameModel, test: pl.DataFrame) -> pl.DataFrame:
    """Every probability the report needs, for one fitted model on one season."""
    out = {
        "game_id": test["game_id"].to_numpy(),
        "season": _num(test, "season"),
        "week": _num(test, "week"),
        "phase": evidence_phase(_num(test, "h_games"), _num(test, "a_games")),
    }
    for target in ("margin", "total", "q1_margin", "q1_total", "h1_margin", "h1_total"):
        out[f"pred_{target}"] = model.predict_mean(test, target)
        samples = model.outcome_samples(test, target)
        out[f"p10_{target}"] = np.quantile(samples, 0.10, axis=1)
        out[f"p90_{target}"] = np.quantile(samples, 0.90, axis=1)
        out[target] = _num(test, target)
        if target == "margin":
            win = side_probabilities(samples, np.zeros(len(test)))
            # A tie is impossible in college football (overtime); split the
            # simulated zero mass evenly rather than let it vanish.
            out["p_home_win"] = win["win"] + win["push"] / 2
            spread = _num(test, "mkt_spread")
            cover = side_probabilities(samples, np.nan_to_num(spread))
            decided = cover["win"] + cover["lose"]
            out["p_home_cover"] = np.where(
                np.isnan(spread), np.nan, cover["win"] / np.where(decided > 0, decided, 1)
            )
        if target == "total":
            line = _num(test, "mkt_total")
            over = side_probabilities(samples, -np.nan_to_num(line))
            decided = over["win"] + over["lose"]
            out["p_over"] = np.where(
                np.isnan(line), np.nan, over["win"] / np.where(decided > 0, decided, 1)
            )
    for c in ("mkt_spread", "mkt_total", "mkt_home_ml", "mkt_away_ml"):
        out[c] = _num(test, c)
    out["mkt_p_home_win"] = devig_moneyline(out["mkt_home_ml"], out["mkt_away_ml"])
    return pl.DataFrame(out)


def walk_forward(games: pl.DataFrame, test_seasons: list[int]) -> list[SeasonRun]:
    done = games.filter(pl.col("completed"))
    runs: list[SeasonRun] = []
    for season in test_seasons:
        train = done.filter(pl.col("season") < season)
        test = done.filter(pl.col("season") == season)
        for cls in (RatingsModel, BoostedModel):
            model = cls().fit(train)
            runs.append(SeasonRun(season, model.name, predict_season(model, test)))
    return runs


# =============================================================================
# Metrics
# =============================================================================
def _mae(pred: np.ndarray, actual: np.ndarray) -> float:
    m = ~np.isnan(pred) & ~np.isnan(actual)
    return float(np.mean(np.abs(pred[m] - actual[m]))) if m.any() else float("nan")


def side_metrics(p: np.ndarray, won: np.ndarray, push: np.ndarray) -> dict:
    """Calibration, Brier and edge picks for a two-way side at -110."""
    m = ~np.isnan(p) & ~push
    p, won = p[m], won[m]
    if len(p) == 0:
        return {"n": 0}
    bins = []
    for lo, hi in zip(CAL_BINS[:-1], CAL_BINS[1:], strict=True):
        sel = (p >= lo) & (p < hi)
        if sel.any():
            bins.append((lo, hi, int(sel.sum()), float(p[sel].mean()), float(won[sel].mean())))
    weights = np.array([b[2] for b in bins], dtype=float)
    ece = float(np.sum(weights * np.abs([b[3] - b[4] for b in bins])) / weights.sum())
    # Picks: the side the model prefers, when its edge over a vig-free 50% is
    # at least the threshold.
    edge = np.abs(p - 0.5)
    pick = edge >= EDGE_THRESHOLD
    pick_won = np.where(p[pick] > 0.5, won[pick], ~won[pick])
    roi = (
        float(np.mean(np.where(pick_won, PRICE_110_PAYOUT, -1.0))) if pick.any() else float("nan")
    )
    return {
        "n": int(len(p)),
        "brier": float(np.mean((p - won) ** 2)),
        "ece": ece,
        "bins": bins,
        "picks": int(pick.sum()),
        "pick_hit": float(np.mean(pick_won)) if pick.any() else float("nan"),
        "roi": roi,
    }


def summarise(frame: pl.DataFrame) -> dict:
    f = {c: _num(frame, c) for c in frame.columns if c not in ("phase",)}
    margin, total = f["margin"], f["total"]
    spread, line = f["mkt_spread"], f["mkt_total"]

    cover_res = margin + spread
    over_res = total - line
    has_ml = ~np.isnan(f["mkt_p_home_win"])
    home_won = (margin > 0).astype(float)

    interval = {}
    for t in ("margin", "total", "q1_margin", "q1_total", "h1_margin", "h1_total"):
        a, lo, hi = f[t], f[f"p10_{t}"], f[f"p90_{t}"]
        ok = ~np.isnan(a)
        inside = (a[ok] >= lo[ok]) & (a[ok] <= hi[ok])
        interval[t] = {
            "mae": _mae(f[f"pred_{t}"], a),
            "coverage80": float(np.mean(inside)) if ok.any() else float("nan"),
        }

    return {
        "games": int(len(frame)),
        "margin_mae": _mae(f["pred_margin"], margin),
        "margin_mae_market": _mae(-spread, margin),
        "total_mae": _mae(f["pred_total"], total),
        "total_mae_market": _mae(line, total),
        "cover": side_metrics(f["p_home_cover"], cover_res > 0, cover_res == 0),
        "over": side_metrics(f["p_over"], over_res > 0, over_res == 0),
        "win_brier": float(np.mean((f["p_home_win"][has_ml] - home_won[has_ml]) ** 2)),
        "win_brier_market": float(np.mean((f["mkt_p_home_win"][has_ml] - home_won[has_ml]) ** 2)),
        "win_n": int(has_ml.sum()),
        "periods": interval,
    }


def summarise_runs(runs: list[SeasonRun]) -> dict:
    """{model: {"all": ..., season: ..., "early"/"later": ...}}."""
    out: dict = {}
    for name in sorted({r.model for r in runs}):
        frames = [r.frame for r in runs if r.model == name]
        pooled = pl.concat(frames)
        entry = {"all": summarise(pooled)}
        for r in runs:
            if r.model == name:
                entry[r.season] = summarise(r.frame)
        for phase in ("early", "later"):
            entry[phase] = summarise(pooled.filter(pl.col("phase") == phase))
        out[name] = entry
    return out
