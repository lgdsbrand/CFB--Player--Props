"""Grading the shadow picks (CLAUDE.md §11, G4). Pure functions; no database.

Two grades, because they answer on different clocks:

  * RESULT — did the pick win at the line and price it was made against.
    Honest, but at ~52% break-even it takes most of a season of picks to
    separate skill from noise.
  * CLOSING LINE VALUE — did the market move toward the pick by kickoff,
    measured on the SAME book's last captured price before kickoff. A model
    that consistently gets better numbers than the close is beating the market
    in the sense books care about, and that shows in weeks, not seasons.

CLV comes in two forms, and each is only reported where it means something:

  * points, for spreads and totals: how far the line moved in the pick's
    favour (home -3 taken, closed -4.5: +1.5 for a home pick);
  * probability, for any market where the closing line EQUALS the pick's line
    (always for moneylines): the closing vig-free probability of the pick's
    side minus the probability implied by the price taken, vig included. Above
    zero means the price taken was better than the fair closing price.
    Prices at two different lines are not comparable, so a moved spread gets
    points only.
"""

from __future__ import annotations

from dataclasses import dataclass

from worker.core.game_picks import devig, implied


@dataclass(frozen=True)
class Graded:
    market: str
    side: str
    result: str  # win | loss | push
    profit: float  # per unit staked, at the price taken
    clv_points: float | None
    clv_prob: float | None


def payout(price: int) -> float:
    """Profit per unit staked on a win at an American price."""
    return price / 100.0 if price > 0 else 100.0 / -price


def result(market: str, side: str, line: float | None, margin: float, total: float) -> str:
    if market == "totals":
        x = total - line
        x = x if side == "over" else -x
    else:
        x = margin + (line or 0.0) if market == "spreads" else margin
        x = x if side == "home" else -x
    return "win" if x > 0 else "loss" if x < 0 else "push"


def clv_points(market: str, side: str, line: float, close_line: float) -> float:
    """Points the market moved toward the pick (positive = we had the better number)."""
    if market == "spreads":
        # A home line is a handicap on the home side: -3 is better than -4.5.
        moved = line - close_line
        return moved if side == "home" else -moved
    moved = close_line - line  # totals: an over at 50 is good if the close is 52
    return moved if side == "over" else -moved


def clv_probability(side: str, first_side: str, price: int,
                    close_first: int, close_second: int) -> float:
    close_fair = devig(close_first, close_second)
    fair_for_side = close_fair if side == first_side else 1 - close_fair
    return fair_for_side - implied(price)


def grade(pick: dict, margin: float, total: float, close: dict | None) -> Graded:
    """`pick`: market, side, line, price. `close`: the same book's line and two prices."""
    market, side, line, price = pick["market"], pick["side"], pick["line"], pick["price"]
    res = result(market, side, line, margin, total)
    profit = payout(price) if res == "win" else -1.0 if res == "loss" else 0.0

    points = prob = None
    if close is not None:
        first = "over" if market == "totals" else "home"
        a, b = (
            (close["over_price"], close["under_price"])
            if market == "totals"
            else (close["home_price"], close["away_price"])
        )
        close_line = close.get("line")
        if market != "h2h" and line is not None and close_line is not None:
            points = clv_points(market, side, float(line), float(close_line))
        same_line = market == "h2h" or (
            line is not None and close_line is not None
            and abs(float(line) - float(close_line)) < 1e-9
        )
        if same_line and a is not None and b is not None:
            prob = clv_probability(side, first, price, a, b)
    return Graded(market, side, res, profit, points, prob)
