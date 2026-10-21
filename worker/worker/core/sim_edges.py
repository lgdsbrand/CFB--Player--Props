"""The game simulator's quarters and halves against the book (phase S3).

SPORT-AGNOSTIC CORE. No database, no provider: a stored simulation, the
period baseline and the books' quotes in; one priced row per market out.

WHICH NUMBER PRICES WHICH MARKET is `SOURCE`, decided by the S1 backtest
(docs/nfl-sim-backtest.html) before any price was compared: for each market,
the method with the lower log loss pooled over the 2025 and 2026 test seasons,
the simulator or the pregame projection times the league's period share. The
rule was fixed before the extra markets were run (2026-10-21); the table only
records its outcome.

The edge is the house definition (CLAUDE.md §6), exactly as the game table's
(`game_picks`): our probability of a side at the BOOK'S line, a push excluded
from both sides, minus the same side's vig-free probability from ONE book's
two-way price, the book chosen by `choose_quote` (Pinnacle, DraftKings,
FanDuel). The price never enters our number (CLAUDE.md §11).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from worker.core.game_picks import Quote, choose_quote, devig
from worker.core.sim_backtest import PeriodBaseline

PERIODS = ("q1", "h1")
MARKETS = ("h2h", "spreads", "totals")

#: (period, market) -> "simulator" | "baseline". The S1 backtest's verdict per
#: market, log loss pooled over 2025+2026 (simulator vs baseline, games):
#:   1Q total   0.6853 vs 0.6901 (365)    1H total   0.7006 vs 0.6959 (365)
#:   1Q spread  0.6881 vs 0.6958 (365)    1H spread  0.6917 vs 0.6928 (365)
#:   1Q winner  0.6795 vs 0.6783 (283)    1H winner  0.6680 vs 0.6689 (334)
#: Every one is close to a coin flip's 0.6931 on lines set from our own
#: projection: the choice matters little, and none of it is evidence of an edge
#: against a book.
SOURCE: dict[tuple[str, str], str] = {
    ("q1", "totals"): "simulator",
    ("q1", "spreads"): "simulator",
    ("q1", "h2h"): "baseline",
    ("h1", "totals"): "baseline",
    ("h1", "spreads"): "simulator",
    ("h1", "h2h"): "simulator",
}

# model_prob is numeric(6, 5) and must be strictly inside (0, 1).
_FLOOR = 1e-5


@dataclass(frozen=True)
class PeriodEdge:
    game_id: int
    period: str
    market: str
    source: str
    line: float | None
    sportsbook_id: int
    sportsbook_key: str
    first_price: int
    second_price: int
    model_prob: float  # first side: home, or over
    book_prob: float
    model_mean: float  # the period's total, or the home margin

    @property
    def side(self) -> str:
        """The side our number is better on than the book's, as a game pick takes it."""
        first, second = ("over", "under") if self.market == "totals" else ("home", "away")
        return first if self.model_prob >= self.book_prob else second

    @property
    def edge(self) -> float:
        if self.side in ("home", "over"):
            return self.model_prob - self.book_prob
        return (1 - self.model_prob) - (1 - self.book_prob)


def samples(hist: dict) -> np.ndarray:
    """The simulated values behind a stored histogram ({"lo", "n"})."""
    counts = np.asarray(hist["n"], dtype=np.int64)
    return np.repeat(hist["lo"] + np.arange(len(counts)), counts)


def _share(x: np.ndarray) -> float | None:
    """P(x > 0) with pushes (x == 0) removed from both sides."""
    win, lose = float((x > 0).mean()), float((x < 0).mean())
    return None if win + lose == 0 else win / (win + lose)


def segment(summary: dict, period: str) -> dict:
    """The stored game_sims summary's first quarter or first half."""
    return summary["quarters"][0] if period == "q1" else summary["halves"]["first"]


def model_probability(
    period: str,
    market: str,
    line: float | None,
    summary: dict,
    baseline: PeriodBaseline | None,
    prior_margin: float,
    prior_total: float,
    source: str,
) -> tuple[float, float] | None:
    """(our probability of the first side, our mean) at this line; None if it cannot be priced."""
    if source == "simulator":
        seg = segment(summary, period)
        if market == "totals":
            x = samples(seg["total"])
            return _with_mean(_share(x - line), x.mean())
        m = samples(seg["margin"])
        return _with_mean(_share(m if market == "h2h" else m + line), m.mean())
    if baseline is None:
        return None
    mean, sd = baseline.mean_sd(period, market, prior_margin, prior_total)
    if market == "totals":
        z = (mean - line) / sd
    elif market == "spreads":
        z = (mean + line) / sd
    else:
        z = mean / sd
    return float(norm.cdf(z)), float(mean)


def _with_mean(p: float | None, mean: float) -> tuple[float, float] | None:
    return None if p is None else (p, float(mean))


def price_game(
    game_id: int,
    summary: dict,
    prior_home: float,
    prior_away: float,
    quotes: dict[str, list[Quote]],
    baseline: PeriodBaseline | None,
) -> list[PeriodEdge]:
    """Every period market with a usable quote, priced. `quotes` is keyed by period."""
    out: list[PeriodEdge] = []
    pm, pt = prior_home - prior_away, prior_home + prior_away
    for period in PERIODS:
        for market in MARKETS:
            q = choose_quote(quotes.get(period, []), market)
            if q is None:
                continue
            source = SOURCE[(period, market)]
            priced = model_probability(period, market, q.line, summary, baseline, pm, pt, source)
            if priced is None:
                continue
            p, mean = priced
            first, second = q.two_way()
            out.append(
                PeriodEdge(
                    game_id=game_id,
                    period=period,
                    market=market,
                    source=source,
                    line=q.line if market != "h2h" else None,
                    sportsbook_id=q.sportsbook_id,
                    sportsbook_key=q.sportsbook_key,
                    first_price=first,
                    second_price=second,
                    model_prob=float(np.clip(p, _FLOOR, 1 - _FLOOR)),
                    book_prob=float(np.clip(devig(first, second), _FLOOR, 1 - _FLOOR)),
                    model_mean=round(mean, 1),
                )
            )
    return out
