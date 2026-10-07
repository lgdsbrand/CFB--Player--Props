"""Price-based +EV: which books beat the sharp book's fair price (migration 0087).

SPORT-AGNOSTIC CORE. Quotes in, wagers out; no database, no model. This is
the client's "+EV wagers" (2026-10-06), decided by the user as PRICE-BASED:
it says a book is paying more than the market's fair price, and nothing about
who wins. The game model is not read here and must not be.

THE FAIR PRICE IS PINNACLE'S, POWER-DE-VIGGED, AT THE SAME LINE. Two choices,
both measured on 2026-10-03 (54 games, every capture before kickoff):

  * POWER, NOT PROPORTIONAL. Proportional de-vig hands a longshot too much of
    the margin: five books at +1050 on one game read as 28% +EV against
    Pinnacle's proportional fair, an artefact of the method. The power method
    (find k with p1^k + p2^k = 1) is the standard for this reason; with it,
    regular sportsbooks cleared 2% only 7 times all Saturday. The games
    table's edge keeps the house proportional method (CLAUDE.md §6); this is a
    different question about prices near evens and longshots alike.
  * THE SAME LINE ONLY. A spread of -6.5 against Pinnacle's -7 is a different
    bet, and pricing the half point would need a push model this does not
    have. A book off Pinnacle's line is simply not compared.

PRICES OUTSIDE `PRICE_RANGE` ARE LEFT OUT. Even power de-vig is least sure
of itself at the tails, and a +1500 moneyline "edge" is mostly method.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

SHARP_BOOK = "pinnacle"

#: American prices a wager is considered at, inclusive.
PRICE_RANGE = (-400, 300)

#: A sharp quote outside this overround is not a price (game_picks.OVERROUND_RANGE).
SHARP_OVERROUND = (0.98, 1.15)

SIDES = {"h2h": ("home", "away"), "spreads": ("home", "away"), "totals": ("over", "under")}


@dataclass(frozen=True)
class EvQuote:
    """One book's two-way price on one market: home/away, or over/under."""

    book_key: str
    market: str
    line: float | None
    first_price: int | None
    second_price: int | None


@dataclass(frozen=True)
class EvWager:
    book_key: str
    market: str
    side: str
    line: float | None
    price: int
    fair_prob: float
    ev: float


def implied(price: float) -> float:
    """The probability an American price implies, margin included."""
    return -price / (-price + 100.0) if price < 0 else 100.0 / (price + 100.0)


def power_devig(first: int, second: int) -> float:
    """Fair probability of the FIRST side: p1^k + p2^k = 1, solved for k."""
    a, b = implied(first), implied(second)
    lo, hi = 0.05, 20.0
    for _ in range(80):
        k = (lo + hi) / 2
        if a**k + b**k > 1:
            lo = k
        else:
            hi = k
    return a ** ((lo + hi) / 2)


def decimal_odds(price: int) -> float:
    return 1 + (100 / -price if price < 0 else price / 100)


def expected_value(fair_prob: float, price: int) -> float:
    """Return per unit staked at `price` if `fair_prob` is the true chance."""
    return fair_prob * decimal_odds(price) - 1


def _same_line(a: float | None, b: float | None) -> bool:
    return (a is None and b is None) or (
        a is not None and b is not None and abs(a - b) < 1e-9
    )


def ev_wagers(quotes: Iterable[EvQuote], min_ev: float = 0.0) -> list[EvWager]:
    """Every side of every non-sharp quote whose EV against the sharp fair exceeds `min_ev`."""
    quotes = list(quotes)
    sharp = {
        q.market: q
        for q in quotes
        if q.book_key == SHARP_BOOK and q.first_price and q.second_price
        and SHARP_OVERROUND[0] <= implied(q.first_price) + implied(q.second_price)
        <= SHARP_OVERROUND[1]
    }
    lo, hi = PRICE_RANGE
    out: list[EvWager] = []
    for q in quotes:
        s = sharp.get(q.market)
        if s is None or q.book_key == SHARP_BOOK or not _same_line(q.line, s.line):
            continue
        fair_first = power_devig(s.first_price, s.second_price)
        for side, fair, price in zip(
            SIDES[q.market], (fair_first, 1 - fair_first), (q.first_price, q.second_price),
            strict=True,
        ):
            if price is None or not lo <= price <= hi:
                continue
            ev = expected_value(fair, price)
            if ev > min_ev:
                out.append(EvWager(q.book_key, q.market, side, q.line, int(price), fair, ev))
    return out
