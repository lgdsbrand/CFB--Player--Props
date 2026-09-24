"""Shadow picks and their grading (CLAUDE.md §11, G4) — offline.

The whole shadow test rests on three things getting their sign right: which
side a spread favours, which side the edge is on, and which way a line move
counts as value. Each is pinned here with a hand-worked case.
"""

from __future__ import annotations

import numpy as np

from worker.core.game_grading import clv_points, clv_probability, grade, payout, result
from worker.core.game_picks import Pick, Quote, choose_quote, devig, evaluate, is_new


def _q(key, market, line=None, **prices) -> Quote:
    return Quote(sportsbook_id=hash(key) % 1000, sportsbook_key=key, market=market,
                 line=line, **prices)


def test_pinnacle_first_then_retail_and_never_a_one_sided_price():
    quotes = [
        _q("fanduel", "spreads", -3.5, home_price=-110, away_price=-110),
        _q("pinnacle", "spreads", -3.0, home_price=-105, away_price=None),  # one-sided
        _q("draftkings", "spreads", -3.5, home_price=-112, away_price=-108),
        _q("betmgm", "totals", 50.5, over_price=-110, under_price=-110),
    ]
    assert choose_quote(quotes, "spreads").sportsbook_key == "draftkings"
    # A book outside PICK_BOOKS is never the one a pick is made against.
    assert choose_quote(quotes, "totals") is None


def test_an_off_board_price_is_not_a_price():
    # FanDuel, 2026-09-24: -100000 both ways on a game it had taken down.
    quotes = [
        _q("fanduel", "h2h", home_price=-100000, away_price=-100000),
        _q("draftkings", "h2h", home_price=-5000, away_price=1800),
    ]
    assert choose_quote(quotes, "h2h").sportsbook_key == "draftkings"


def test_devig_is_proportional():
    assert abs(devig(-110, -110) - 0.5) < 1e-12
    assert abs(devig(-150, 130) - 0.6 / (0.6 + 100 / 230)) < 1e-12


def test_a_spread_pick_takes_the_model_probability_at_the_books_line():
    # Home favoured by 3; model's margins centre on +10: home covers -3 easily.
    margins = np.array([4.0, 7, 10, 13, 16, 2, 20, 10, 8, 12])
    q = _q("pinnacle", "spreads", -3.0, home_price=-105, away_price=-105)
    pick = evaluate(1, q, margins, np.zeros(10), 0.8)
    assert pick.side == "home" and pick.price == -105 and pick.line == -3.0
    assert abs(pick.model_prob - 0.9) < 1e-12  # the +2 does not cover -3
    everyone_covers = evaluate(1, q, margins + 5, np.zeros(10), 0.8)
    assert everyone_covers.model_prob == 1 - 1e-5  # clipped strictly inside (0, 1)


def test_pushes_leave_both_sides_of_a_spread():
    # home -3; outcomes +3 push twice, +4 covers, +1 fails -> 1 of 2 decided.
    margins = np.array([3.0, 3, 4, 1])
    q = _q("pinnacle", "spreads", -3.0, home_price=-110, away_price=-110)
    assert evaluate(1, q, margins, np.zeros(4), 0.5) is None  # 50% vs 50%


def test_an_under_pick_and_no_pick_inside_the_threshold():
    totals = np.array([40.0, 42, 44, 46, 48, 50, 52, 38, 41, 43])  # 1 of 10 over 50.5
    q = _q("draftkings", "totals", 50.5, over_price=-110, under_price=-110)
    pick = evaluate(1, q, np.zeros(10), totals, 0.5)
    assert pick.side == "under" and abs(pick.model_prob - 0.9) < 1e-12
    assert abs(pick.book_prob - 0.5) < 1e-12
    # Model 53% on a home moneyline priced 50/50: a 3-point edge is not a pick.
    ml = _q("pinnacle", "h2h", home_price=-110, away_price=-110)
    assert evaluate(1, ml, np.zeros(1), np.zeros(1), 0.53) is None
    assert evaluate(1, ml, np.zeros(1), np.zeros(1), 0.40).side == "away"


def test_a_pick_stands_until_the_model_switches_side():
    p = Pick(1, "spreads", "home", -3.0, -110, 7, "pinnacle", 0.6, 0.5)
    assert is_new(p, None)
    assert not is_new(p, "home")
    assert is_new(p, "away")


def test_results_on_each_market():
    assert result("spreads", "home", -3.0, 4, 50) == "win"
    assert result("spreads", "home", -3.0, 3, 50) == "push"
    assert result("spreads", "away", -3.0, 2, 50) == "win"  # away +3, lost by 2
    assert result("totals", "over", 50.5, 0, 51) == "win"
    assert result("totals", "under", 50.5, 0, 51) == "loss"
    assert result("h2h", "away", None, -1, 50) == "win"


def test_clv_points_count_a_move_toward_the_pick_as_value():
    assert clv_points("spreads", "home", -3.0, -4.5) == 1.5    # took home -3, closed -4.5
    assert clv_points("spreads", "away", -3.0, -4.5) == -1.5   # took away +3, closed +4.5
    assert clv_points("totals", "over", 50.0, 52.0) == 2.0
    assert clv_points("totals", "under", 50.0, 52.0) == -2.0


def test_clv_probability_and_payout():
    # Took home +120 (45.5% implied); closed home -110/-110 (50% fair): +4.5 pts.
    assert abs(clv_probability("home", "home", 120, -110, -110) - (0.5 - 100 / 220)) < 1e-12
    assert payout(120) == 1.2 and abs(payout(-110) - 100 / 110) < 1e-12


def test_grade_reports_prob_clv_only_when_the_line_did_not_move():
    pick = {"market": "spreads", "side": "home", "line": -3.0, "price": -105}
    moved = {"line": -4.5, "home_price": -110, "away_price": -110}
    same = {"line": -3.0, "home_price": -120, "away_price": 100}
    g = grade(pick, 7, 50, moved)
    assert g.result == "win" and g.clv_points == 1.5 and g.clv_prob is None
    g = grade(pick, 1, 50, same)
    assert g.result == "loss" and g.profit == -1.0 and g.clv_points == 0.0
    assert g.clv_prob > 0  # -105 taken, fair close ~52.4%
    assert grade(pick, 7, 50, None).clv_points is None
