"""Backtest of the game simulator against the live model (phase S1).

SPORT-AGNOSTIC CORE, pure and database-free. The job
(`jobs/run_sim_backtest.py`) loads the games and the plays; this module turns
simulated games into the columns `core/live_backtest.score` grades, so the
simulator and the live model are scored by the same code on the same rows,
and grades the first quarter and first half on their own.

THE GATE (agreed 2026-10-20): the simulator must be at least as good as the
live model (candidate A) on full-game numbers to supply them. If it is not,
the live model keeps the full game and the simulator supplies the animation
and the quarter and half numbers, once those are shown to be calibrated here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl
from scipy.optimize import minimize_scalar
from scipy.stats import norm

from worker.core.play_sim import Result

#: An 80% interval is p10 to p90.
LO_Q, HI_Q = 0.10, 0.90


def half_line(x: np.ndarray) -> np.ndarray:
    """A line the way a book posts one from a projection: the nearest x.5 below-or-at."""
    return np.floor(np.asarray(x, dtype=float)) + 0.5


@dataclass
class Summary:
    """Per checkpoint, from its k simulated games."""

    p_home_win: np.ndarray
    margin_mean: np.ndarray
    margin_hw: np.ndarray
    total_mean: np.ndarray
    total_hw: np.ndarray
    q1_total: np.ndarray  # (C, k) kept for line probabilities
    h1_total: np.ndarray
    h1_margin: np.ndarray
    q1_margin: np.ndarray


def summarize(res: Result, k: int) -> Summary:
    """Reshape a simulate() result of C×k rows (k per checkpoint, contiguous) into per-checkpoint numbers."""
    h = res.home.reshape(-1, k).astype(float)
    a = res.away.reshape(-1, k).astype(float)
    m = h - a
    t = h + a
    q = res.quarters.reshape(-1, k, 4, 2).astype(float)
    return Summary(
        p_home_win=(m > 0).mean(axis=1) + 0.5 * (m == 0).mean(axis=1),
        margin_mean=m.mean(axis=1),
        margin_hw=(np.quantile(m, HI_Q, axis=1) - np.quantile(m, LO_Q, axis=1)) / 2,
        total_mean=t.mean(axis=1),
        total_hw=(np.quantile(t, HI_Q, axis=1) - np.quantile(t, LO_Q, axis=1)) / 2,
        q1_total=q[:, :, 0, :].sum(axis=2),
        h1_total=q[:, :, :2, :].sum(axis=(2, 3)),
        h1_margin=q[:, :, :2, 0].sum(axis=2) - q[:, :, :2, 1].sum(axis=2),
        q1_margin=q[:, :, 0, 0] - q[:, :, 0, 1],
    )


def smooth(p: np.ndarray, k: int) -> np.ndarray:
    """A share of k games, pulled half a game toward even: never exactly 0 or 1."""
    return (np.asarray(p, float) * k + 0.5) / (k + 1)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_scales(frame: pl.DataFrame, k: int) -> dict[int, float]:
    """One scale s, with sigmoid(s * logit(p)) best fitting who won, for every moment of the game.

    The live model's win % is fitted to wins too (core/live_model.py header).
    ONE scale, not one per moment: fitted per moment on a season of
    simulations it chased noise (scales 0.82 to 1.35 between neighbouring
    moments, 2026-10-20) and made the held-out season worse. `frame` holds
    the raw shares of k games; they are smoothed here first. Returned per
    moment so callers need not know it is shared.
    """
    m = frame["final_margin"].to_numpy()
    decided = m != 0
    x = _logit(smooth(frame["p_home_win"].to_numpy(), k))[decided]
    y = (m[decided] > 0).astype(float)

    def loss(s: float) -> float:
        q = np.clip(1 / (1 + np.exp(-s * x)), 1e-9, 1 - 1e-9)
        return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))

    s = float(minimize_scalar(loss, bounds=(0.2, 3.0), method="bounded").x)
    return {int(e): s for e in sorted(set(frame["elapsed"].to_list()))}


def apply_scales(frame: pl.DataFrame, scales: dict[int, float]) -> pl.DataFrame:
    """The fitted scale applied to each moment's (already smoothed) win %."""
    s = np.array([scales.get(int(e), 1.0) for e in frame["elapsed"].to_list()])
    p = 1 / (1 + np.exp(-s * _logit(frame["p_home_win"].to_numpy())))
    return frame.with_columns(p_home_win=pl.Series(p))


def choose_sigma(log_losses: dict[float, float]) -> float:
    """The θ spread with the lowest pregame log loss on the season before the test season."""
    return min(log_losses, key=lambda s: (log_losses[s], s))


def win_log_loss(p: np.ndarray, margin: np.ndarray) -> float:
    decided = margin != 0
    p = np.clip(p[decided], 1e-6, 1 - 1e-6)
    won = (margin[decided] > 0).astype(float)
    return float(-np.mean(won * np.log(p) + (1 - won) * np.log(1 - p)))


# =============================================================================
# First quarter and first half
# =============================================================================


@dataclass(frozen=True)
class PeriodBaseline:
    """The simple way to price a period: the pregame projection times the league's share, normal around it.

    Fitted on the training seasons' games with a projection: the share of
    the game's points (and of its margin) that a period carries, and the sd
    of what is left over.
    """

    q1_share: float
    h1_share: float
    h1_margin_share: float
    q1_sd: float
    h1_sd: float
    h1_margin_sd: float
    q1_margin_share: float
    q1_margin_sd: float

    @classmethod
    def fit(
        cls, prior_margin, prior_total, q1_total, h1_total, h1_margin, q1_margin
    ) -> PeriodBaseline:
        prior_margin = np.asarray(prior_margin, float)
        prior_total = np.asarray(prior_total, float)
        q1s = float(np.sum(q1_total) / np.sum(prior_total))
        h1s = float(np.sum(h1_total) / np.sum(prior_total))
        # Least squares through the origin: how much of the projected margin shows by the period.
        denom = max(float(np.sum(prior_margin**2)), 1e-9)
        hms = float(np.sum(prior_margin * h1_margin) / denom)
        qms = float(np.sum(prior_margin * q1_margin) / denom)
        return cls(
            q1s,
            h1s,
            hms,
            float(np.std(q1_total - q1s * prior_total)),
            float(np.std(h1_total - h1s * prior_total)),
            float(np.std(h1_margin - hms * prior_margin)),
            qms,
            float(np.std(q1_margin - qms * prior_margin)),
        )

    def mean_sd(
        self, period: str, market: str, prior_margin: float, prior_total: float
    ) -> tuple[float, float]:
        """(mean, sd) of a period's total (market "totals") or home margin, from the projection."""
        if market == "totals":
            if period == "q1":
                return self.q1_share * prior_total, self.q1_sd
            return self.h1_share * prior_total, self.h1_sd
        share, sd = (
            (self.q1_margin_share, self.q1_margin_sd)
            if period == "q1"
            else (self.h1_margin_share, self.h1_margin_sd)
        )
        return share * prior_margin, sd

    def to_dict(self) -> dict:
        return {k: round(float(v), 5) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> PeriodBaseline:
        return cls(**{k: float(d[k]) for k in cls.__dataclass_fields__})


def period_rows(
    s: Summary, base: PeriodBaseline, prior_margin, prior_total, actual: dict[str, np.ndarray]
) -> pl.DataFrame:
    """One row per pregame checkpoint: simulator and baseline, against what happened.

    Lines are set from the baseline's projection, the way a book would set
    them, so neither side is graded on a line of its own making.
    """
    pm = np.asarray(prior_margin, float)
    pt = np.asarray(prior_total, float)
    out = {}
    # A spread is the home margin over a line, so it is priced like a total.
    for name, sims, mean_b, sd_b in (
        ("q1_total", s.q1_total, base.q1_share * pt, base.q1_sd),
        ("h1_total", s.h1_total, base.h1_share * pt, base.h1_sd),
        ("q1_margin", s.q1_margin, base.q1_margin_share * pm, base.q1_margin_sd),
        ("h1_margin", s.h1_margin, base.h1_margin_share * pm, base.h1_margin_sd),
    ):
        line = half_line(mean_b)
        out[f"{name}_line"] = line
        out[f"{name}_sim_p_over"] = (sims > line[:, None]).mean(axis=1)
        out[f"{name}_base_p_over"] = 1 - norm.cdf((line - mean_b) / sd_b)
        out[f"{name}_sim_mean"] = sims.mean(axis=1)
        out[f"{name}_base_mean"] = mean_b
        out[f"{name}_sim_lo"] = np.quantile(sims, LO_Q, axis=1)
        out[f"{name}_sim_hi"] = np.quantile(sims, HI_Q, axis=1)
        out[f"{name}_base_lo"] = mean_b - 1.2815515655446004 * sd_b
        out[f"{name}_base_hi"] = mean_b + 1.2815515655446004 * sd_b
        out[f"{name}_actual"] = actual[name]
    # The period moneylines: who leads, a tie excluded (a push).
    for period, margin in (("q1", s.q1_margin), ("h1", s.h1_margin)):
        lead = (margin > 0).mean(axis=1)
        trail = (margin < 0).mean(axis=1)
        out[f"{period}_sim_p_home"] = lead / np.maximum(lead + trail, 1e-9)
        out[f"{period}_base_p_home"] = norm.cdf(
            out[f"{period}_margin_base_mean"] / getattr(base, f"{period}_margin_sd")
        )
    return pl.DataFrame(out)


#: Markets graded as P(above a line): totals, and spreads as the home margin over a line.
LINE_MARKETS = (
    ("q1_total", "1st quarter total"),
    ("h1_total", "1st half total"),
    ("q1_margin", "1st quarter spread"),
    ("h1_margin", "1st half spread"),
)
WINNER_MARKETS = (("q1", "1st quarter winner"), ("h1", "1st half winner"))


def _brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def _ll(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def period_scores(frame: pl.DataFrame) -> list[dict]:
    """Per market: Brier and log loss of P(over the line) or P(home leads), mean miss, 80% coverage."""
    rows = []
    for name, label in LINE_MARKETS:
        y = (frame[f"{name}_actual"].to_numpy() > frame[f"{name}_line"].to_numpy()).astype(float)
        act = frame[f"{name}_actual"].to_numpy()
        for who in ("sim", "base"):
            p = frame[f"{name}_{who}_p_over"].to_numpy()
            rows.append(
                {
                    "market": label,
                    "method": "simulator" if who == "sim" else "projection × league share",
                    "n": len(y),
                    "brier": _brier(p, y),
                    "log_loss": _ll(p, y),
                    "mae": float(np.mean(np.abs(frame[f"{name}_{who}_mean"].to_numpy() - act))),
                    "cover80": float(
                        np.mean(
                            (act >= frame[f"{name}_{who}_lo"].to_numpy())
                            & (act <= frame[f"{name}_{who}_hi"].to_numpy())
                        )
                    ),
                    "said_over": float(np.mean(p)),
                    "went_over": float(np.mean(y)),
                }
            )
    for period, label in WINNER_MARKETS:
        hm = frame[f"{period}_margin_actual"].to_numpy()
        decided = hm != 0
        y = (hm[decided] > 0).astype(float)
        for who in ("sim", "base"):
            p = frame[f"{period}_{who}_p_home"].to_numpy()[decided]
            rows.append(
                {
                    "market": label,
                    "method": "simulator" if who == "sim" else "projection × league share",
                    "n": int(decided.sum()),
                    "brier": _brier(p, y),
                    "log_loss": _ll(p, y),
                    "mae": float(
                        np.mean(np.abs(frame[f"{period}_margin_{who}_mean"].to_numpy() - hm))
                    ),
                    "cover80": None,
                    "said_over": float(np.mean(p)),
                    "went_over": float(np.mean(y)),
                }
            )
    return rows


def period_calibration(
    frame: pl.DataFrame, name: str, edges: Sequence[float] = (0, 0.3, 0.4, 0.5, 0.6, 0.7, 1)
) -> list[dict]:
    """The simulator's P(over) for one market in bins: said against happened."""
    p = frame[f"{name}_sim_p_over"].to_numpy()
    y = (frame[f"{name}_actual"].to_numpy() > frame[f"{name}_line"].to_numpy()).astype(float)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if mask.sum():
            out.append(
                {
                    "bin": f"{lo:.0%}–{hi:.0%}",
                    "n": int(mask.sum()),
                    "said": float(p[mask].mean()),
                    "hit": float(y[mask].mean()),
                }
            )
    return out
