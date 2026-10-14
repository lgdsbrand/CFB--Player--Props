"""Picks from the client's NFL fantasy game model (phase F3).

SPORT-AGNOSTIC CORE, pure: a projection and a book's quote in, a pick or None
out. The rule is the client's own, the one his rule plays use: a play when the
model's number sits far enough from the book's line,

    spread  4+ points  (RULE_SPREAD_GAP)
    total   6+ points  (RULE_TOTAL_GAP)

compared in POINTS, the way his sheet's "Edge" column does. Each version of
the formula (`fantasy_model.VERSIONS`) picks as its own engine, so the fixed
one and his sheet as-is are graded side by side and never pooled.

THE PROBABILITY RECORDED IS HIS SCALE, NOT A CALIBRATED ONE. `game_picks`
needs a model probability, and his model has none for a cover. The user chose
his raw 0.23 formula for the win % (2026-10-14), so the pick records the same
logistic applied to the points past the line: 4 points is 72%, 6 is 80%. That
is far more confident than any line-gap rule has earned in testing, which is
why selection stays on points and the tracker grades wins and units.
"""

from __future__ import annotations

import math

from worker.core.fantasy_model import WIN_SLOPE
from worker.core.game_picks import (
    _PROB_FLOOR,
    RULE_SPREAD_GAP,
    RULE_TOTAL_GAP,
    Pick,
    Quote,
    devig,
)

#: One pick engine per version of the formula (migration 0095).
ENGINES: dict[str, str] = {"fixed": "fantasy", "sheet": "fantasy_sheet"}
MARKETS: tuple[str, ...] = ("spreads", "totals")


def his_probability(points: float) -> float:
    """His win % formula, 1 / (1 + e^(-0.23 x points)), on any margin in points."""
    p = 1.0 / (1.0 + math.exp(-WIN_SLOPE * points))
    return min(max(p, _PROB_FLOOR), 1 - _PROB_FLOOR)


def evaluate(
    game_id: int, quote: Quote, home_margin: float, total: float, version: str
) -> Pick | None:
    """A play against this quote if the gap rule for its market fires."""
    prices = quote.two_way()
    if prices is None or quote.line is None or quote.market not in MARKETS:
        return None
    if quote.market == "spreads":
        # Both in the home team's terms: a margin of +3.3 is a home spread of -3.3.
        model_line = round(-home_margin, 1)
        first_is_ours = model_line < quote.line  # home the bigger favourite
        needed = RULE_SPREAD_GAP
        first, second = "home", "away"
    else:
        model_line = round(total, 1)
        first_is_ours = model_line > quote.line
        needed = RULE_TOTAL_GAP
        first, second = "over", "under"
    gap = round(abs(model_line - quote.line), 1)
    if gap < needed:
        return None

    p_book_first = devig(*prices)
    if first_is_ours:
        side, book, price = first, p_book_first, prices[0]
    else:
        side, book, price = second, 1 - p_book_first, prices[1]
    return Pick(
        game_id=game_id,
        market=quote.market,
        side=side,
        line=quote.line,
        price=int(price),
        sportsbook_id=quote.sportsbook_id,
        sportsbook_key=quote.sportsbook_key,
        model_prob=his_probability(gap),
        book_prob=book,
        engine=ENGINES[version],
        model_line=model_line,
        gap=gap,
    )


__all__ = ["ENGINES", "MARKETS", "evaluate", "his_probability"]
