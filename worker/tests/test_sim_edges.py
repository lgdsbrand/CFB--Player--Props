"""The game simulator's quarters and halves against the book (phase S3)."""

from __future__ import annotations

import numpy as np
import pytest

from worker.core import sim_edges as se
from worker.core.game_picks import Quote, devig
from worker.core.sim_backtest import PeriodBaseline
from worker.core.sim_runtime import histogram


def _seg(total: list[int], margin: list[int]) -> dict:
    return {"total": histogram(np.array(total)), "margin": histogram(np.array(margin))}


# 10 simulated games: Q1 totals and home margins, first-half totals and margins.
Q1 = _seg([0, 3, 7, 7, 10, 10, 14, 3, 7, 0], [0, 3, 7, -7, 3, -3, 0, 3, 7, 0])
H1 = _seg([10, 17, 20, 24, 21, 13, 27, 17, 20, 14], [3, 7, -3, 10, 0, -7, 14, 3, -3, 7])
SUMMARY = {"quarters": [Q1, {}, {}, {}], "halves": {"first": H1, "second": {}}}
BASE = PeriodBaseline(0.2, 0.5, 0.55, 5.0, 7.0, 8.0, 0.3, 5.5)


def quote(key, market, line, a, b, book_id=1):
    if market == "totals":
        return Quote(book_id, key, market, line, over_price=a, under_price=b)
    return Quote(book_id, key, market, line, home_price=a, away_price=b)


def test_a_histogram_gives_back_its_games():
    values = np.array([3, 7, 7, -3, 0])
    assert sorted(se.samples(histogram(values)).tolist()) == sorted(values.tolist())


def test_a_push_is_excluded_from_both_sides():
    # Home +3 in the first quarter: a game the home side loses by exactly 3 lands
    # on the line, a push, and counts for neither side.
    p, mean = se.model_probability("q1", "spreads", 3.0, SUMMARY, None, 0, 0, "simulator")
    m = np.array([0, 3, 7, -7, 3, -3, 0, 3, 7, 0]) + 3
    assert p == pytest.approx((m > 0).sum() / (m != 0).sum())
    assert mean == pytest.approx(1.3)


def test_a_period_moneyline_ignores_ties():
    p, _ = se.model_probability("q1", "h2h", None, SUMMARY, None, 0, 0, "simulator")
    assert p == pytest.approx(5 / 7)  # 5 home leads, 2 away leads, 3 ties


def test_the_baseline_prices_from_the_projection():
    # 1H total: 0.5 x 44 = 22, sd 7; over 22.5 is a little under half.
    p, mean = se.model_probability("h1", "totals", 22.5, SUMMARY, BASE, 3.0, 44.0, "baseline")
    assert mean == pytest.approx(22.0) and 0.45 < p < 0.5
    assert se.model_probability("h1", "totals", 22.5, SUMMARY, None, 3.0, 44.0, "baseline") is None


def test_each_market_is_priced_against_the_first_listed_book():
    quotes = {
        "q1": [
            quote("fanduel", "totals", 8.5, -110, -110, 27),
            quote("draftkings", "totals", 9.5, -105, -115, 26),
        ],
        "h1": [quote("draftkings", "h2h", None, -150, 125, 26)],
    }
    edges = {(e.period, e.market): e for e in se.price_game(7, SUMMARY, 24.0, 20.0, quotes, BASE)}
    assert set(edges) == {("q1", "totals"), ("h1", "h2h")}
    q1 = edges[("q1", "totals")]
    assert q1.sportsbook_key == "draftkings" and q1.line == 9.5 and q1.source == "simulator"
    assert q1.model_prob == pytest.approx(0.3)  # 10, 10, 14 of 10 games over 9.5
    assert q1.book_prob == pytest.approx(devig(-105, -115))
    assert q1.side == "under" and q1.edge == pytest.approx(0.7 - (1 - q1.book_prob))
    h1 = edges[("h1", "h2h")]
    assert h1.line is None and h1.side == "home"
    assert h1.edge == pytest.approx(h1.model_prob - devig(-150, 125))


def test_the_side_is_where_our_number_beats_the_book():
    # We say home 59%, the book 66%: the value is on the away side.
    e = se.PeriodEdge(
        1, "h1", "h2h", "simulator", None, 26, "draftkings", -190, 160, 0.59, 0.66, 2.0
    )
    assert e.side == "away" and e.edge == pytest.approx(0.07)


def test_a_price_that_is_not_a_price_is_skipped():
    quotes = {"q1": [quote("draftkings", "totals", 9.5, -100000, -100000)]}
    assert se.price_game(7, SUMMARY, 24.0, 20.0, quotes, BASE) == []


def test_every_market_has_a_source():
    assert set(se.SOURCE) == {(p, m) for p in se.PERIODS for m in se.MARKETS}
    assert set(se.SOURCE.values()) <= {"simulator", "baseline"}


def test_a_baseline_survives_the_engine_meta():
    assert PeriodBaseline.from_dict(BASE.to_dict()) == BASE
