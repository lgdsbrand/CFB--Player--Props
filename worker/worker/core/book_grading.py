"""Grade the model against real book prices, not against synthetic lines.

SPORT-AGNOSTIC CORE (CLAUDE.md §3). No provider types, no SQL — the job supplies
rows, this decides what they are worth.

WHY THIS IS A DIFFERENT QUESTION FROM THE BACKTEST. `core/backtest.py` grades
CALIBRATION: it offsets a synthetic line from the player's own trailing average
and asks whether a stated 60% happens 60% of the time. That is necessary and it
is not sufficient. A perfectly calibrated model loses money against a book whose
price is better than its own estimate — calibration is a claim about the model,
profitability is a claim about the model RELATIVE TO A PRICE. Every edge % on
the board is the second claim, and until this module existed nothing measured it.

THE THREE NUMBERS THAT DECIDE IT.

  * **Win rate** — did the picks land?
  * **Break-even** — what win rate the price itself demanded. At -110 that is
    52.4%, so a 51% win rate is a losing model no matter how well calibrated.
  * **ROI** — win rate against break-even, in units. This is the answer.

A win rate quoted without its break-even is the single most misleading number
available here, so `ThresholdResult` refuses to carry one without the other.

PRICES ARE NOT INTERCHANGEABLE. ROI is reported at the MEDIAN book price as the
headline and at the BEST available as a ceiling. Grading only at the best price
assumes a bettor who always shops every book and always gets filled, which
flatters the result by a margin that grows with the number of books quoting.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Literal

from worker.core.probability import (
    american_to_implied_probability,
    consensus_book_probability,
    edge_on_side,
    prob_over,
    side_and_confidence,
)

Outcome = Literal["over", "under", "push"]


def american_to_decimal(price: int) -> float:
    """Total return per unit staked, stake included.

    Derived from the vig-inclusive implied probability rather than
    reimplemented, so the two can never disagree about what -110 means.
    """
    implied = american_to_implied_probability(price)
    if implied <= 0:
        raise ValueError(f"price {price} implies a non-positive probability")
    return 1.0 / implied


def breakeven_rate(price: int) -> float:
    """The win rate this price demands to break even."""
    return american_to_implied_probability(price)


@dataclass(frozen=True)
class BookPriceRow:
    """One book's two-way price on one line. Plain data, no provider types."""

    sportsbook_key: str
    over_price: int | None
    under_price: int | None

    @property
    def is_two_way(self) -> bool:
        return self.over_price is not None and self.under_price is not None


@dataclass
class BookBet:
    """One gradeable bet: a projection, a real price, and what happened."""

    player_id: int
    game_id: int
    market_key: str
    position_group: str
    season: int
    week: int
    line: float
    model_prob_over: float
    book_prob_over: float
    side: str
    confidence: float
    edge: float
    actual_value: float
    outcome: Outcome
    n_books: int
    median_price: int
    best_price: int

    @property
    def is_push(self) -> bool:
        return self.outcome == "push"

    @property
    def hit(self) -> bool | None:
        """None on a push — a push is not a loss and must not be counted as one."""
        if self.is_push:
            return None
        return self.side == self.outcome

    def profit(self, price: int) -> float:
        """Units won or lost on a 1-unit stake at `price`."""
        if self.is_push:
            return 0.0
        return american_to_decimal(price) - 1.0 if self.hit else -1.0


def grade_bet(
    *,
    player_id: int,
    game_id: int,
    market_key: str,
    position_group: str,
    season: int,
    week: int,
    line: float,
    distribution: str,
    params: dict,
    prices: Sequence[BookPriceRow],
    actual_value: float | None,
) -> BookBet | None:
    """Turn one player-market-line into a graded bet, or None if it cannot be.

    Returns None rather than guessing whenever the bet is not really gradeable:

      * **No two-way price.** A one-sided quote cannot be de-vigged, so there is
        no book probability to compare against and therefore no edge. Grading it
        against the vig-inclusive number would credit the model with beating a
        price that includes the book's margin — edge manufactured out of hold.
      * **No actual.** A player who did not record a line in the box score has
        no outcome. Treating a missing row as zero would score every inactive
        player as an UNDER hit, which is a large and entirely fake edge.
    """
    two_way = [p for p in prices if p.is_two_way]
    if not two_way:
        return None
    if actual_value is None:
        return None

    book_prob = consensus_book_probability(
        [(p.over_price, p.under_price) for p in two_way]
    )
    if book_prob is None:
        return None

    model_prob = prob_over(distribution, params, line)
    side, confidence = side_and_confidence(model_prob)
    edge = edge_on_side(model_prob, book_prob, side)
    if edge is None:
        return None

    # A PUSH IS ITS OWN OUTCOME. Books post integer lines on low-count markets
    # (rush attempts, receptions), so exact ties are not hypothetical. Folding
    # them into "under" would invent a loss on a bet that returned the stake.
    if actual_value > line:
        outcome: Outcome = "over"
    elif actual_value < line:
        outcome = "under"
    else:
        outcome = "push"

    on_side = [
        (p.over_price if side == "over" else p.under_price) for p in two_way
    ]
    on_side = [p for p in on_side if p is not None]
    if not on_side:
        return None

    return BookBet(
        player_id=player_id,
        game_id=game_id,
        market_key=market_key,
        position_group=position_group,
        season=season,
        week=week,
        line=line,
        model_prob_over=model_prob,
        book_prob_over=book_prob,
        side=side,
        confidence=confidence,
        edge=edge,
        actual_value=actual_value,
        outcome=outcome,
        n_books=len(two_way),
        # Best = longest odds on the side taken, which is the highest decimal
        # return. Not max() of the American number: -105 beats -110 but 100
        # beats both, and the sign flip makes the raw comparison wrong.
        median_price=_median_price(on_side),
        best_price=max(on_side, key=american_to_decimal),
    )


def _median_price(prices: Sequence[int]) -> int:
    """Median by RETURN, then reported as the American price at that point.

    Taking a median of American odds directly is meaningless across the sign
    boundary — the scale is discontinuous at ±100.
    """
    ordered = sorted(prices, key=american_to_decimal)
    return ordered[(len(ordered) - 1) // 2]


@dataclass
class ThresholdResult:
    """What the picks above one edge threshold actually returned."""

    threshold: float
    n: int = 0
    n_players: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    profit_median: float = 0.0
    profit_best: float = 0.0
    breakeven_sum: float = 0.0
    by_market: dict[str, int] = field(default_factory=dict)

    @property
    def decided(self) -> int:
        """Bets that resolved. Pushes stake nothing and settle nothing."""
        return self.wins + self.losses

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.decided if self.decided else None

    @property
    def breakeven(self) -> float | None:
        """Mean win rate the prices taken demanded.

        THE NUMBER A WIN RATE IS MEANINGLESS WITHOUT. 52% wins is profitable at
        +100 and loses money at -120.
        """
        return self.breakeven_sum / self.decided if self.decided else None

    @property
    def roi_median(self) -> float | None:
        return self.profit_median / self.n if self.n else None

    @property
    def roi_best(self) -> float | None:
        return self.profit_best / self.n if self.n else None

    def summary(self) -> str:
        if not self.decided:
            return f"edge >= {self.threshold:.0%}: no decided bets"
        return (
            f"edge >= {self.threshold:>4.0%}: n={self.n:>5,} "
            f"({self.n_players:>4,} players)  win={self.win_rate:.1%} "
            f"vs breakeven {self.breakeven:.1%}  "
            f"ROI {self.roi_median:+.1%} (best price {self.roi_best:+.1%})"
            + (f"  {self.pushes} push" if self.pushes else "")
        )


def summarise(bets: Sequence[BookBet], threshold: float) -> ThresholdResult:
    """Aggregate the bets whose edge clears `threshold`."""
    result = ThresholdResult(threshold=threshold)
    players: set[int] = set()

    for bet in bets:
        if bet.edge < threshold:
            continue
        result.n += 1
        players.add(bet.player_id)
        result.by_market[bet.market_key] = result.by_market.get(bet.market_key, 0) + 1

        if bet.is_push:
            result.pushes += 1
            continue

        if bet.hit:
            result.wins += 1
        else:
            result.losses += 1
        result.breakeven_sum += breakeven_rate(bet.median_price)
        result.profit_median += bet.profit(bet.median_price)
        result.profit_best += bet.profit(bet.best_price)

    result.n_players = len(players)
    return result


def edge_thresholds(bets: Sequence[BookBet], thresholds: Sequence[float]):
    """One `ThresholdResult` per threshold, in the order given."""
    return [summarise(bets, t) for t in thresholds]


def by_market(bets: Sequence[BookBet], threshold: float) -> dict[str, ThresholdResult]:
    """Per-market results at one threshold.

    Kept separate from the headline because the markets are not one population:
    `rec_yards` is 40% of the sample and would carry any blended figure on its
    own.
    """
    markets = sorted({bet.market_key for bet in bets})
    return {
        market: summarise([b for b in bets if b.market_key == market], threshold)
        for market in markets
    }


def median_edge(bets: Sequence[BookBet]) -> float | None:
    return median(bet.edge for bet in bets) if bets else None


@dataclass
class ConfidenceBand:
    """Model confidence against what actually happened, at the book's line."""

    lower: float
    upper: float
    n: int
    mean_model: float
    mean_book: float
    observed: float

    @property
    def model_error(self) -> float:
        """Positive means OVERCONFIDENT — claimed more than it delivered."""
        return self.mean_model - self.observed

    @property
    def book_error(self) -> float:
        return self.mean_book - self.observed


def confidence_bands(
    bets: Sequence[BookBet], edges: Sequence[float] = (0.5, 0.6, 0.7, 0.8, 1.01)
) -> list[ConfidenceBand]:
    """Reliability of the model AND of the book, side by side.

    THIS IS THE DIAGNOSTIC THAT SPLITS TWO VERY DIFFERENT FAILURES, which a
    plain ROI number cannot tell apart:

      * **Calibrated but not better than the market.** The model says 60% and
        60% happens; the book simply prices it as well or better. There is no
        bug — the model has no edge, and finding one means new information, not
        new arithmetic.
      * **Overconfident.** The model says 60% and 53% happens. Then the "edge"
        is mostly the overstatement, filtering on it selects the worst rows,
        and the confidence % shown on the board is wrong in a way users can see.

    Measured on the CALLED side's confidence rather than P(over), because that
    is the number the product displays and the number a bettor acts on. The
    book's de-vigged probability for the same side is carried alongside so the
    two can be compared on identical rows — the point is not whether the model
    is well calibrated in the abstract, but whether it is worse than the price
    it is being asked to beat.
    """
    bands: list[ConfidenceBand] = []
    decided = [b for b in bets if not b.is_push]

    for lower, upper in zip(edges, edges[1:], strict=False):
        rows = [b for b in decided if lower <= b.confidence < upper]
        if not rows:
            continue
        # The book's probability for the side WE took, not for the over.
        book_on_side = [
            b.book_prob_over if b.side == "over" else 1.0 - b.book_prob_over
            for b in rows
        ]
        bands.append(
            ConfidenceBand(
                lower=lower,
                upper=upper,
                n=len(rows),
                mean_model=sum(b.confidence for b in rows) / len(rows),
                mean_book=sum(book_on_side) / len(rows),
                observed=sum(1 for b in rows if b.hit) / len(rows),
            )
        )
    return bands
