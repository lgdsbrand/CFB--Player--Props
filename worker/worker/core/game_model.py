"""Game outcome models — distributions, not points (CLAUDE.md §11, G3).

Two candidates, compared by the backtest rather than chosen up front:

  * `RatingsModel` — the classic approach. Each side's CURRENT-season
    opponent-adjusted strength is weighted by games played, g / (g + k), and
    the remainder of the weight goes to last season's SP+. A ridge regression
    on a handful of engineered differences (net strength, prior, Elo, home
    field) turns that into an expected margin and total. `k` is chosen by
    cross-validation inside the training seasons, never on the test season.
  * `BoostedModel` — gradient boosting (scikit-learn) on the raw features. It
    can find interactions the ratings model cannot, and with ~900 games a
    season it can also overfit; that is what the backtest is for.

BOTH PRODUCE A DISTRIBUTION. A model predicts a mean; the outcome
distribution around it is the model's own OUT-OF-FOLD residuals from its
training seasons, pooled by how much current-season evidence the game had
(early-season games miss by more). Each simulated outcome is rounded to a
whole number of points, so a push has real probability mass on a whole-
number line. That handles pushes, though it does not model the key-number
clustering at 3 and 7 on its own; the calibration report is where that
shows up if it matters.

Neither model ever sees a `mkt_` column: they train on `FEATURE_COLUMNS` and
the engineered features derived from them, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold, KFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from worker.core.game_data import FEATURE_COLUMNS

MARGIN_TARGETS = ("margin", "q1_margin", "h1_margin")
TOTAL_TARGETS = ("total", "q1_total", "h1_total")
TARGETS = MARGIN_TARGETS + TOTAL_TARGETS

SHRINK_GRID = (1.0, 2.0, 3.0, 4.0, 6.0, 9.0)

# Residual pools. A game whose sides have little current-season evidence is
# predicted mostly from last season, and misses by more.
EARLY_GAMES = 2


def evidence_phase(h_games: np.ndarray, a_games: np.ndarray) -> np.ndarray:
    return np.where(np.minimum(h_games, a_games) <= EARLY_GAMES, "early", "later")


# =============================================================================
# Features
# =============================================================================
def _col(df: pl.DataFrame, name: str) -> np.ndarray:
    return df[name].cast(pl.Float64).fill_null(np.nan).to_numpy()


def _z(x: np.ndarray) -> np.ndarray:
    return np.nan_to_num(x, nan=0.0)


def ratings_features(df: pl.DataFrame, k: float) -> dict[str, np.ndarray]:
    """Engineered inputs for the ratings model: margin block and total block."""
    hg, ag = _col(df, "h_games"), _col(df, "a_games")
    sh, sa = hg / (hg + k), ag / (ag + k)

    def net(side: str, metric: str) -> np.ndarray:
        return _z(_col(df, f"{side}_off_{metric}_rel") - _col(df, f"{side}_def_{metric}_rel"))

    def env(side: str, metric: str) -> np.ndarray:
        return _z(_col(df, f"{side}_off_{metric}_rel") + _col(df, f"{side}_def_{metric}_rel"))

    h_prior, a_prior = _col(df, "h_prior"), _col(df, "a_prior")
    h_env_prior = _col(df, "h_prior_off") + _col(df, "h_prior_def")
    a_env_prior = _col(df, "a_prior_off") + _col(df, "a_prior_def")

    margin = {
        "home": 1.0 - _col(df, "neutral"),
        "cur_points": sh * net("h", "points") - sa * net("a", "points"),
        "cur_ppa": sh * net("h", "ppa") - sa * net("a", "ppa"),
        "cur_success": sh * net("h", "success") - sa * net("a", "success"),
        "prior": (1 - sh) * _z(h_prior) - (1 - sa) * _z(a_prior),
        "h_no_prior": np.isnan(h_prior).astype(float),
        "a_no_prior": np.isnan(a_prior).astype(float),
        "elo": _z(_col(df, "h_elo") - _col(df, "a_elo")) / 25.0,
    }
    total = {
        "cur_points": sh * env("h", "points") + sa * env("a", "points"),
        "cur_ppa": sh * env("h", "ppa") + sa * env("a", "ppa"),
        "cur_plays": sh * env("h", "plays") + sa * env("a", "plays"),
        "prior": (1 - sh) * _z(h_env_prior) + (1 - sa) * _z(a_env_prior),
        "no_prior": (np.isnan(h_env_prior) | np.isnan(a_env_prior)).astype(float),
        "league_points": _z(_col(df, "league_points")),
    }
    return {
        "margin": np.column_stack(list(margin.values())),
        "total": np.column_stack(list(total.values())),
    }


def boosted_features(df: pl.DataFrame) -> np.ndarray:
    """Raw features plus the two differences a tree would otherwise need many splits for."""
    base = np.column_stack([_col(df, c) for c in FEATURE_COLUMNS])
    extra = np.column_stack([
        _col(df, "h_elo") - _col(df, "a_elo"),
        _col(df, "h_prior") - _col(df, "a_prior"),
    ])
    return np.column_stack([base, extra])


# =============================================================================
# Models
# =============================================================================
def _ridge():
    return make_pipeline(StandardScaler(), Ridge(alpha=1.0))


def _booster():
    return HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=250,
        max_leaf_nodes=15,
        min_samples_leaf=40,
        l2_regularization=1.0,
        random_state=0,
    )


def _folds(df: pl.DataFrame):
    seasons = df["season"].to_numpy()
    if len(np.unique(seasons)) >= 2:
        return GroupKFold(n_splits=len(np.unique(seasons))).split(seasons, groups=seasons)
    return KFold(n_splits=5, shuffle=True, random_state=0).split(seasons)


@dataclass
class FittedTarget:
    model: object
    residuals: dict[str, np.ndarray]  # phase -> out-of-fold residuals


@dataclass
class GameModel:
    name: str
    fitted: dict[str, FittedTarget] = field(default_factory=dict)
    k: float | None = None

    # -- per-model hooks ------------------------------------------------------
    def _x(self, df: pl.DataFrame, target: str) -> np.ndarray:
        raise NotImplementedError

    def _new(self):
        raise NotImplementedError

    # -- shared ---------------------------------------------------------------
    def _fit_target(self, df: pl.DataFrame, target: str) -> FittedTarget:
        train = df.filter(pl.col(target).is_not_null())
        x, y = self._x(train, target), _col(train, target)
        oof = np.full(len(y), np.nan)
        for tr, te in _folds(train):
            m = self._new().fit(x[tr], y[tr])
            oof[te] = m.predict(x[te])
        phase = evidence_phase(_col(train, "h_games"), _col(train, "a_games"))
        resid = y - oof
        pools = {p: resid[(phase == p) & ~np.isnan(resid)] for p in ("early", "later")}
        return FittedTarget(model=self._new().fit(x, y), residuals=pools)

    def fit(self, df: pl.DataFrame) -> GameModel:
        for target in TARGETS:
            self.fitted[target] = self._fit_target(df, target)
        return self

    def predict_mean(self, df: pl.DataFrame, target: str) -> np.ndarray:
        return self.fitted[target].model.predict(self._x(df, target))

    def outcome_samples(self, df: pl.DataFrame, target: str) -> np.ndarray:
        """(games, residuals) matrix of whole-number simulated outcomes."""
        mean = self.predict_mean(df, target)
        phase = evidence_phase(_col(df, "h_games"), _col(df, "a_games"))
        pools = self.fitted[target].residuals
        width = min(len(pools["early"]), len(pools["later"]))
        # Equal-sized draws from each pool so every game gets the same count.
        grid = {p: np.quantile(pools[p], np.linspace(0.0025, 0.9975, width)) for p in pools}
        resid = np.stack([grid[p] for p in phase])
        return np.round(mean[:, None] + resid)


class RatingsModel(GameModel):
    def __init__(self) -> None:
        super().__init__(name="ratings")

    def _x(self, df, target):
        block = "margin" if target in MARGIN_TARGETS else "total"
        return ratings_features(df, self.k)[block]

    def _new(self):
        return _ridge()

    def fit(self, df: pl.DataFrame) -> RatingsModel:
        # Choose k on the FULL-GAME margin by out-of-fold error, inside training.
        train = df.filter(pl.col("margin").is_not_null())
        y = _col(train, "margin")
        best = None
        for k in SHRINK_GRID:
            x = ratings_features(train, k)["margin"]
            err = []
            for tr, te in _folds(train):
                pred = _ridge().fit(x[tr], y[tr]).predict(x[te])
                err.append(np.abs(pred - y[te]))
            mae = float(np.mean(np.concatenate(err)))
            if best is None or mae < best[1]:
                best = (k, mae)
        self.k = best[0]
        super().fit(df)
        return self


class BoostedModel(GameModel):
    def __init__(self) -> None:
        super().__init__(name="boosted")

    def _x(self, df, target):
        return boosted_features(df)

    def _new(self):
        return _booster()


# =============================================================================
# Probabilities
# =============================================================================
def side_probabilities(samples: np.ndarray, line: np.ndarray) -> dict[str, np.ndarray]:
    """P(outcome + line > 0), P(== 0), P(< 0) per game.

    For a margin sample and a HOME spread, `> 0` is a home cover. For a total
    sample pass `-total_line`, and `> 0` is an over.
    """
    x = samples + line[:, None]
    return {
        "win": (x > 0).mean(axis=1),
        "push": (x == 0).mean(axis=1),
        "lose": (x < 0).mean(axis=1),
    }


def devig_moneyline(home: np.ndarray, away: np.ndarray) -> np.ndarray:
    """Vig-free home win probability from two American prices (proportional)."""
    def implied(p):
        # np.where evaluates both branches; -100 would divide by zero in the
        # branch it then discards.
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(p < 0, -p / (-p + 100.0), 100.0 / (p + 100.0))

    h, a = implied(home), implied(away)
    return h / (h + a)
