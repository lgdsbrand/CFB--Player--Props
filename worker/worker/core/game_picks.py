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
    # Which pick rule made it (migration 0090): v1 raw at 5%, v2 calibrated at 2%,
    # rules the client's line-gap rules (migration 0091).
    engine: str = "v1"
    # Rule plays only (migration 0091): our number in the line's own terms (the
    # home team's fair spread, or the fair total) and its distance from the line.
    model_line: float | None = None
    gap: float | None = None
    # Rule moneylines only: the model's own win % that fired the rule, beside
    # the blended one recorded as model_prob.
    raw_prob: float | None = None

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


# CALIBRATION OF THE TABLE'S EDGE (2026-10-07, on the user's decision: "it's
# necessary to make it honest"). The raw probability at the book's line is
# far too confident: in the 2023-2025 walk-forward backtest a raw 70% spread
# side covered 49-51% and a raw 70% total about 56%. One number per market
# pulls it back toward 50% on the log-odds scale,
#
#     p_calibrated = sigmoid(slope * logit(p_raw)),
#
# fitted by `calibration_slope` (core/game_backtest.py) on every 2023-2025
# out-of-sample prediction; `run_game_backtest` prints the refit. Checked on
# seasons each fit never saw, including 2026 to date: an intercept and an
# evidence-phase term did no better. Spreads at 0.0565 means the spread edge
# is honestly almost nil (a raw 90% is a calibrated 53%); totals at 0.256 keep
# a small real signal (a raw 70% is 55%). Win % was already calibrated (slope
# 1.06) and is left alone.
#
# ONLY THE GAMES TABLE IS CALIBRATED. Shadow picks are still made on the raw
# probability (`evaluate`): they are a test already running, with a totals
# tier pre-registered on raw numbers (grade_game_picks), and changing what is
# frozen mid-season would break both. Refit each offseason, never mid-season.
CALIBRATION_SLOPE: dict[str, float] = {"spreads": 0.0565, "totals": 0.2560}


def calibrate(p_raw: float, market: str) -> float:
    """The calibrated probability of the same side; symmetric, so 50% stays 50%."""
    p = float(np.clip(p_raw, _PROB_FLOOR, 1 - _PROB_FLOOR))
    z = CALIBRATION_SLOPE[market] * np.log(p / (1 - p))
    return float(1 / (1 + np.exp(-z)))


# Where each market's comparison lands on a game_projections row (migration
# 0082). Two-way prices in the quote's own order: home/away, or over/under.
PRICED_COLUMNS: dict[str, tuple[str, str, str, str, str]] = {
    "spreads": ("spread_sportsbook_id", "spread_line", "spread_home_price",
                "spread_away_price", "spread_model_home_prob"),
    "totals": ("total_sportsbook_id", "total_line", "total_over_price",
               "total_under_price", "total_model_over_prob"),
}


def priced_columns(
    quote: Quote | None,
    margin_samples: np.ndarray,
    total_samples: np.ndarray,
    p_home_win: float,
    market: str,
) -> dict[str, object]:
    """The projection row's comparison with one book on `market`, or all NULL.

    THE SAME QUOTE A PICK WOULD BE PRICED FROM — `choose_quote` picks the
    book — but written whatever the edge, because the games table shows the
    edge on every game, not only on the ones that cleared the pick threshold.
    The probability is the pick's (`first_side_probability`) CALIBRATED (see
    CALIBRATION_SLOPE), so the table's edge is smaller than a pick's. The site
    removes the vig and takes the difference (CLAUDE.md §6); this stores the
    inputs.
    """
    columns = PRICED_COLUMNS[market]
    empty: dict[str, object] = dict.fromkeys(columns)
    if quote is None or quote.market != market:
        return empty
    prices = quote.two_way()
    p_model = first_side_probability(quote, margin_samples, total_samples, p_home_win)
    if prices is None or p_model is None or quote.line is None:
        return empty
    book_id, line, first_price, second_price, prob = columns
    return {
        book_id: quote.sportsbook_id,
        line: quote.line,
        first_price: int(prices[0]),
        second_price: int(prices[1]),
        prob: round(calibrate(p_model, market), 5),
    }


# ENGINE V2 (2026-10-08, user-approved for the public tracker). Picks from the
# CALIBRATED probability the games table shows, at a 2% edge: on week 6 that is
# ~5 spreads and ~30 totals a slate (calibrated spread edges top out near 3%).
# v1 keeps running unchanged beside it; each engine's record stands alone.
# "Edge plays" for v2 is edge >= 3%, fixed here before any v2 pick is graded.
V2_EDGE_THRESHOLD = 0.02
V2_EDGE_PLAY = 0.03
V2_MARKETS = ("spreads", "totals")


def evaluate_calibrated(
    game_id: int,
    quote: Quote,
    margin_samples: np.ndarray,
    total_samples: np.ndarray,
    p_home_win: float,
    threshold: float = V2_EDGE_THRESHOLD,
    *,
    margin_mean: float | None = None,
    total_mean: float | None = None,
) -> Pick | None:
    """An engine-v2 pick: `evaluate` on the calibrated probability.

    The same quote and the same arithmetic the games table's edge uses
    (`priced_columns`), so a v2 pick is exactly a table edge that cleared the
    threshold when the pick was made.

    NEVER AGAINST OUR OWN LINE (2026-10-09). The table names the side where
    our fair number sits against the book's line (web `tableSide`). With the
    spread calibration flattening probabilities to ~50%, the juice alone can
    favour the other side; given the projection's means, a pick on the side
    our number points away from is refused. None of the first 52 did.
    """
    if quote.market not in V2_MARKETS:
        return None
    prices = quote.two_way()
    p_raw = first_side_probability(quote, margin_samples, total_samples, p_home_win)
    if prices is None or p_raw is None:
        return None
    p_model = calibrate(p_raw, quote.market)
    p_book = devig(*prices)
    first, second = ("over", "under") if quote.market == "totals" else ("home", "away")

    if p_model - p_book >= threshold:
        side, prob, book, price = first, p_model, p_book, prices[0]
    elif (1 - p_model) - (1 - p_book) >= threshold:
        side, prob, book, price = second, 1 - p_model, 1 - p_book, prices[1]
    else:
        return None
    if not agrees_with_our_line(quote, side, margin_mean, total_mean):
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
        engine="v2",
    )


# RULE PLAYS (client 2026-10-09: "4 points or more either way" on spreads,
# "6-7 points or more" on totals, moneylines "60 something % or better and odds
# -150 or better"). A third engine, `rules`, graded on its own tracker card and
# public before kickoff (migration 0091): these are meant to be seen.
#
# Backtested before any was made (2023-2025 walk-forward plus 2026 to date,
# closing lines): spreads 4+ were 50.2% then 45.1%, a coin flip; totals 6+
# 56.7% then 56.2%; totals 7+ 57.9% then 60.0% and up in every season, so 7+
# is the pre-registered top tier, graded inside and beside totals.
RULE_SPREAD_GAP = 4.0
RULE_TOTAL_GAP = 6.0
RULE_TOTAL_TOP_GAP = 7.0
RULE_ML_MIN_PROB = 0.60
RULE_ML_WORST_PRICE = -150
# Favourites only: -150 to -101. See the moneyline note below.
RULE_ML_BEST_PRICE = -101
RULE_MARKETS = ("spreads", "totals", "h2h")

# THE MONEYLINE IS PULLED TOWARD THE BOOK (user decision 2026-10-09, an explicit
# exception to "the market never feeds the model", CLAUDE.md §11). On its own
# the model's win % is calibrated over all games (slope 1.06) yet badly
# overconfident exactly where the rule fires: where it disagrees with the
# book, it said 68% and won 52% (2023-2025), and 7-16 in 2026. The fix is a
# logistic blend of the two log-odds, no intercept,
#
#     p = sigmoid(w_model * logit(p_model) + w_book * logit(p_book_novig)),
#
# fitted by `moneyline_blend_weights` (core/game_backtest.py) on 2023-2025.
# The fit puts nearly all the weight on the book, which is the finding: the
# model adds little to a moneyline. Held out it matched the book's own
# accuracy (2026 Brier 0.1157 vs 0.1164). Only rule plays use it; the fair
# line never sees a price.
#
# WHAT FIRES AND WHAT IS SHOWN ARE TWO NUMBERS (user, 2026-10-09: fire more
# often "without ruining the overconfidence again"). Requiring the BLEND to
# reach 60% at -150 or better fired ~2 games a season, because an honest win %
# sits on the book's and -150 is itself ~59%. So the rule fires on the
# MODEL'S OWN 60%+ (the client's rule as he states it) on a FAVOURITE priced
# -150 to -101, and the probability recorded, shown and graded is the BLEND.
# Plus money is out: there the model was badly wrong (25-37 in 2023-2025,
# 4-13 in 2026). Walk-forward, 2023-2025: 109 plays (36 a season), 64-45,
# 58.7% won against 55.6% shown, +3.2% at the closing price; 2026 to date 3-3.
ML_BLEND_WEIGHTS: tuple[float, float] = (0.073, 1.045)


def _logit(p: float) -> float:
    q = float(np.clip(p, _PROB_FLOOR, 1 - _PROB_FLOOR))
    return float(np.log(q / (1 - q)))


def blend_win_prob(p_model_home: float, p_book_home: float) -> float:
    """The home team's win probability, the model's pulled toward the book's."""
    w_model, w_book = ML_BLEND_WEIGHTS
    z = w_model * _logit(p_model_home) + w_book * _logit(p_book_home)
    return float(1 / (1 + np.exp(-z)))


def evaluate_rules(
    game_id: int,
    quote: Quote,
    margin_mean: float,
    total_mean: float,
    margin_samples: np.ndarray,
    total_samples: np.ndarray,
    p_home_win: float,
) -> Pick | None:
    """A rule play against this quote, if the client's rule for its market fires.

    Spreads and totals compare POINTS: the fair line the games table shows
    against the book's line, so a play is exactly a row where the two columns
    sit that far apart. The probability recorded is the calibrated one the
    table's edge uses. Moneylines fire on the model's own win % and a
    favourite's price, and record the blended win % (see ML_BLEND_WEIGHTS).
    """
    prices = quote.two_way()
    if prices is None:
        return None
    p_book_first = devig(*prices)

    raw_prob = None
    if quote.market == "h2h":
        p_home = blend_win_prob(p_home_win, p_book_first)
        # The side the MODEL prefers, at its own probability.
        if p_home_win >= 0.5:
            side, raw_prob, prob, book, price = (
                "home", p_home_win, p_home, p_book_first, prices[0])
        else:
            side, raw_prob, prob, book, price = (
                "away", 1 - p_home_win, 1 - p_home, 1 - p_book_first, prices[1])
        if raw_prob < RULE_ML_MIN_PROB or not (
            RULE_ML_WORST_PRICE <= price <= RULE_ML_BEST_PRICE
        ):
            return None
        model_line = gap = None
    elif quote.market in ("spreads", "totals") and quote.line is not None:
        if quote.market == "spreads":
            # Both on the home team's side: a fair spread of -6.9 is home by 6.9.
            model_line = round(-margin_mean, 1)
            first_is_ours = model_line < quote.line  # we make home the bigger favourite
            needed = RULE_SPREAD_GAP
        else:
            model_line = round(total_mean, 1)
            first_is_ours = model_line > quote.line  # over
            needed = RULE_TOTAL_GAP
        gap = round(abs(model_line - quote.line), 1)
        if gap < needed:
            return None
        p_raw = first_side_probability(quote, margin_samples, total_samples, p_home_win)
        p_first = calibrate(p_raw if p_raw is not None else 0.5, quote.market)
        first, second = ("over", "under") if quote.market == "totals" else ("home", "away")
        if first_is_ours:
            side, prob, book, price = first, p_first, p_book_first, prices[0]
        else:
            side, prob, book, price = second, 1 - p_first, 1 - p_book_first, prices[1]
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
        engine="rules",
        model_line=model_line,
        gap=gap,
        raw_prob=(
            None if raw_prob is None
            else float(np.clip(raw_prob, _PROB_FLOOR, 1 - _PROB_FLOOR))
        ),
    )


def agrees_with_our_line(
    quote: Quote, side: str, margin_mean: float | None, total_mean: float | None
) -> bool:
    """False only when our fair number points to the OTHER side of the line.

    Equal numbers, or no means given, are no contradiction.
    """
    if quote.line is None:
        return True
    if quote.market == "spreads" and margin_mean is not None:
        ours = -margin_mean
        if ours == quote.line:
            return True
        return side == ("home" if ours < quote.line else "away")
    if quote.market == "totals" and total_mean is not None:
        if total_mean == quote.line:
            return True
        return side == ("over" if total_mean > quote.line else "under")
    return True


def is_new(pick: Pick, standing_side: str | None) -> bool:
    """Write a row only for a first pick or a switch of side (see module docstring)."""
    return standing_side is None or standing_side != pick.side
