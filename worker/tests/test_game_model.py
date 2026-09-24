"""The game model (CLAUDE.md §11, G3) — offline.

What the backtest's conclusions rest on: the market never reaches a model,
a test season is never in its own training data, and the probability maths
treats pushes and the moneyline de-vig correctly.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from worker.core import game_backtest
from worker.core.game_data import FEATURE_COLUMNS, STRENGTH_METRICS
from worker.core.game_model import (
    RatingsModel,
    devig_moneyline,
    evidence_phase,
    side_probabilities,
)


def test_no_market_column_or_outcome_is_a_feature():
    assert not [c for c in FEATURE_COLUMNS if c.startswith("mkt_")]
    outcomes = {"margin", "total", "home_points", "away_points", "completed"}
    assert not outcomes & set(FEATURE_COLUMNS)
    assert not [c for c in FEATURE_COLUMNS if "q1" in c or "h1" in c]


def test_side_probabilities_give_pushes_their_own_mass():
    samples = np.array([[-7, -3, 0, 3, 7]], dtype=float)
    p = side_probabilities(samples, np.array([3.0]))  # home +3
    # outcomes + 3: -4, 0, 3, 6, 10
    assert p["win"][0] == 0.6
    assert p["push"][0] == 0.2
    assert p["lose"][0] == 0.2


def test_devig_moneyline_matches_the_proportional_method():
    # -150 / +130: 0.6 and 0.4348 raw; proportional share of home = 0.5798
    p = devig_moneyline(np.array([-150.0]), np.array([130.0]))
    assert abs(p[0] - 0.6 / (0.6 + 100 / 230)) < 1e-9
    assert abs(devig_moneyline(np.array([-110.0]), np.array([-110.0]))[0] - 0.5) < 1e-12


def test_evidence_phase_uses_the_thinner_side():
    phase = evidence_phase(np.array([0, 5, 3]), np.array([5, 2, 3]))
    assert list(phase) == ["early", "early", "later"]


def _synthetic(seasons=(2022, 2023, 2024), n=240, seed=0) -> pl.DataFrame:
    """Games whose margin really is driven by the strength and prior columns."""
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for i in range(n):
            h_net, a_net = rng.normal(0, 8, 2)
            h_prior, a_prior = h_net + rng.normal(0, 4), a_net + rng.normal(0, 4)
            games = float(i % 10)
            row = {
                "game_id": season * 1000 + i, "season": season, "week": games + 1,
                "completed": True, "neutral": 0.0, "h_games": games, "a_games": games,
                "h_elo": 1500 + 20 * h_net, "a_elo": 1500 + 20 * a_net,
                "h_prior": h_prior, "a_prior": a_prior,
                "h_prior_off": 30 + h_prior / 2, "h_prior_def": 25 - h_prior / 2,
                "a_prior_off": 30 + a_prior / 2, "a_prior_def": 25 - a_prior / 2,
                "league_points": 27.0, "league_plays": 66.0,
            }
            for side, net in (("h", h_net), ("a", a_net)):
                for m in STRENGTH_METRICS:
                    scale = 1.0 if "points" in m or "plays" in m else 0.02
                    sign = 1 if m.startswith("off") else -1
                    row[f"{side}_{m}"] = sign * net / 2 * scale
            margin = 3 + h_net - a_net + rng.normal(0, 10)
            total = 55 + rng.normal(0, 12)
            row.update(
                margin=round(margin), total=round(total),
                q1_margin=round(margin / 4), q1_total=round(total / 4),
                h1_margin=round(margin / 2), h1_total=round(total / 2),
            )
            rows.append(row)
    return pl.DataFrame(rows)


def test_the_ratings_model_learns_a_real_signal():
    df = _synthetic()
    train, test = df.filter(pl.col("season") < 2024), df.filter(pl.col("season") == 2024)
    model = RatingsModel().fit(train)
    pred = model.predict_mean(test, "margin")
    truth = test["margin"].to_numpy()
    assert np.corrcoef(pred, truth)[0, 1] > 0.5
    samples = model.outcome_samples(test, "margin")
    assert samples.shape[0] == test.height
    assert np.all(samples == np.round(samples))


def test_walk_forward_never_trains_on_the_test_season(monkeypatch):
    seen: list[tuple[int, set]] = []

    class Spy(RatingsModel):
        def fit(self, df):
            seen.append(set(df["season"].unique().to_list()))
            return super().fit(df)

    monkeypatch.setattr(game_backtest, "RatingsModel", Spy)
    monkeypatch.setattr(game_backtest, "BoostedModel", Spy)
    df = _synthetic().with_columns(
        pl.lit(-3.0).alias("mkt_spread"), pl.lit(55.0).alias("mkt_total"),
        pl.lit(-150.0).alias("mkt_home_ml"), pl.lit(130.0).alias("mkt_away_ml"),
    )
    game_backtest.walk_forward(df, [2023, 2024])
    assert seen[0] == {2022} and seen[2] == {2022, 2023}
    assert all(2024 not in s for s in seen[:2])
