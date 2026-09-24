"""Turning a game projection into a shadow pick (CLAUDE.md §11, G4).

SPORT-AGNOSTIC CORE. No database, no provider: quotes in, picks out.

A pick is made AGAINST ONE BOOK'S PRICE, never against a blend. That is what
makes it gradeable: the line, the price and the book are recorded with it, and
closing line value is measured against the same book's last price before
kickoff. The book is the sharpest one that priced the market, in the order of
`PICK_BOOKS`: Pinnacle first, because beating Pinnacle's close is the bar that
means something, then the two retail books the client names. A market no
listed book priced gets no pick rather than one against an arbitrary book.

The edge is the house definition (CLAUDE.md §6): the model's probability of a
side minus the book's vig-free probability of the same side, proportional
de-vig, at least `EDGE_THRESHOLD`. For spreads and totals the model's
probability is taken at the BOOK'S line from the simulated outcomes, with a
push excluded from both sides, as the backtest did.

A PICK IS A BET, NOT AN OPINION. Once written it stands at the price it was
made against, even if the line later moves and the edge disappears; that is
exactly what closing line value measures. A new row is written only when the
model moves to the OTHER side of the market before kickoff, and grading uses
the newest row.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from worker.core.game_backtest import EDGE_THRESHOLD

PICK_BOOKS: tuple[str, ...] = ("pinnacle", "draftkings", "fanduel")

# game_picks.model_prob is numeric(6, 5) and must be strictly inside (0, 1).
_PROB_FLOOR = 1e-5

# A real two-way price carries a small margin: implied probabilities summing to
# a little over 1. Measured on the 2026-09-24 captures, 99.8% of quotes sum to
# 1.00-1.09 (Pinnacle 1.03-1.045). Outside this band the quote is not a price:
# FanDuel was carrying -100000 on BOTH sides of a mismatch it had taken off
# the board, which de-vigs to a meaningless 50%. Such a quote is skipped and
# the next book in PICK_BOOKS is used.
OVERROUND_RANGE = (0.98, 1.15)


@dataclass(frozen=True)
class Quote:
    """One book's current two-way price on one full-game market, OUR home/away."""

    sportsbook_id: int
    sportsbook_key: str
    market: str  # h2h | spreads | totals
    line: float | None
    home_price: int | None = None
    away_price: int | None = None
    over_price: int | None = None
    under_price: int | None = None

    def two_way(self) -> tuple[int, int] | None:
        """(first, second) prices: home/away, or over/under for a total."""
        a, b = (
            (self.over_price, self.under_price)
            if self.market == "totals"
            else (self.home_price, self.away_price)
        )
        return None if a is None or b is None else (a, b)


@dataclass(frozen=True)
class Pick:
    game_id: int
    market: str
    side: str
    line: float | None
    price: int
    sportsbook_id: int
    sportsbook_key: str
    model_prob: float
    book_prob: float

    @property
    def edge(self) -> float:
        return self.model_prob - self.book_prob


def implied(price: float) -> float:
    return -price / (-price + 100.0) if price < 0 else 100.0 / (price + 100.0)


def devig(first: int, second: int) -> float:
    """Vig-free probability of the FIRST side of a two-way price (proportional)."""
    a, b = implied(first), implied(second)
    return a / (a + b)


def plausible(first: int, second: int) -> bool:
    lo, hi = OVERROUND_RANGE
    return lo <= implied(first) + implied(second) <= hi


def choose_quote(quotes: list[Quote], market: str) -> Quote | None:
    """The first book in PICK_BOOKS with a complete, plausible two-way price on `market`."""
    usable = {
        q.sportsbook_key: q
        for q in quotes
        if q.market == market
        and q.two_way() is not None
        and plausible(*q.two_way())
        and (market == "h2h" or q.line is not None)
    }
    for key in PICK_BOOKS:
        if key in usable:
            return usable[key]
    return None


def _share(x: np.ndarray) -> float | None:
    """P(x > 0) with pushes (x == 0) removed from both sides."""
    win, lose = float((x > 0).mean()), float((x < 0).mean())
    return None if win + lose == 0 else win / (win + lose)


def first_side_probability(
    quote: Quote, margin_samples: np.ndarray, total_samples: np.ndarray, p_home_win: float
) -> float | None:
    """The model's probability of the quote's FIRST side (home, or over)."""
    if quote.market == "h2h":
        return p_home_win
    if quote.market == "spreads":
        # line is OUR home team's handicap: home covers when margin + line > 0.
        return _share(margin_samples + quote.line)
    if quote.market == "totals":
        return _share(total_samples - quote.line)
    raise ValueError(f"unknown market {quote.market!r}")


def evaluate(
    game_id: int,
    quote: Quote,
    margin_samples: np.ndarray,
    total_samples: np.ndarray,
    p_home_win: float,
    threshold: float = EDGE_THRESHOLD,
) -> Pick | None:
    """The side with at least `threshold` edge against this quote, if either has."""
    prices = quote.two_way()
    p_model = first_side_probability(quote, margin_samples, total_samples, p_home_win)
    if prices is None or p_model is None:
        return None
    p_book = devig(*prices)
    first, second = ("over", "under") if quote.market == "totals" else ("home", "away")

    if p_model - p_book >= threshold:
        side, prob, book, price = first, p_model, p_book, prices[0]
    elif (1 - p_model) - (1 - p_book) >= threshold:
        side, prob, book, price = second, 1 - p_model, 1 - p_book, prices[1]
    else:
        return None
    return Pick(
        game_id=game_id,
        market=quote.market,
        side=side,
        line=quote.line,
        price=int(price),
        sportsbook_id=quote.sportsbook_id,
        sportsbook_key=quote.sportsbook_key,
        model_prob=float(np.clip(prob, _PROB_FLOOR, 1 - _PROB_FLOOR)),
        book_prob=book,
    )


def is_new(pick: Pick, standing_side: str | None) -> bool:
    """Write a row only for a first pick or a switch of side (see module docstring)."""
    return standing_side is None or standing_side != pick.side
