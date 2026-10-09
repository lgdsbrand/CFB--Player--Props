"""Shadow picks and their grading (CLAUDE.md §11, G4) — offline.

The whole shadow test rests on three things getting their sign right: which
side a spread favours, which side the edge is on, and which way a line move
counts as value. Each is pinned here with a hand-worked case.
"""

from __future__ import annotations

import numpy as np

from worker.core.game_grading import clv_points, clv_probability, grade, payout, result
from worker.core.game_picks import (
    CALIBRATION_SLOPE,
    PRICED_COLUMNS,
    V2_EDGE_PLAY,
    V2_EDGE_THRESHOLD,
    Pick,
    Quote,
    calibrate,
    choose_quote,
    devig,
    evaluate,
    evaluate_calibrated,
    is_new,
    priced_columns,
)


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


def test_the_game_model_freezes_no_moneyline_picks():
    """The spread pick's opinion at long odds: variance, no information (week 4:
    9 wins on 44 picks where the book's own prices expected 17)."""
    from worker.jobs.run_game_model import PICK_MARKETS

    assert "h2h" not in PICK_MARKETS
    assert set(PICK_MARKETS) == {"spreads", "totals"}


def test_the_grade_reports_the_pre_registered_totals_tier_separately():
    """Totals at 64%+ are graded beside, and inside, all totals (2026-10-03)."""
    from worker.core.game_grading import Graded
    from worker.jobs.grade_game_picks import TOTALS_HIGH_CONFIDENCE, summarise

    assert TOTALS_HIGH_CONFIDENCE == 0.64

    def g(market, result):
        profit = 100 / 110 if result == "win" else -1.0
        return Graded(market, "over", result, profit, None, None)

    text = summarise([
        (g("totals", "win"), 1.0, 0.70, None),
        (g("totals", "loss"), 1.0, 0.64, None),
        (g("totals", "loss"), 1.0, 0.60, None),
        (g("spreads", "win"), 1.0, 0.90, None),
    ])
    lines = {
        line.split(" picks ")[0].strip(): line
        for line in text.splitlines() if " picks " in line
    }
    assert "W-L-P 1-2-0" in lines["totals"]
    assert "W-L-P 1-1-0" in lines["totals 64%+"]
    assert "64%+" not in lines["spreads"]


# --- The games table's comparison (migration 0082) ---------------------------

def test_the_table_comparison_is_the_pick_s_quote_and_calibrated_probability():
    margins = np.array([4.0, 7, 10, 13, 16, 2, 20, 10, 8, 12])
    q = _q("pinnacle", "spreads", -3.0, home_price=-105, away_price=-115)
    cols = priced_columns(q, margins, np.zeros(10), 0.8, "spreads")
    pick = evaluate(1, q, margins, np.zeros(10), 0.8)
    assert cols == {
        "spread_sportsbook_id": q.sportsbook_id,
        "spread_line": -3.0,
        "spread_home_price": -105,
        "spread_away_price": -115,
        "spread_model_home_prob": round(calibrate(pick.model_prob, "spreads"), 5),
    }
    # The pick is still made on the raw probability; the table shows the
    # calibrated one, which is far closer to 50%.
    assert pick.model_prob == 0.9
    assert 0.5 < cols["spread_model_home_prob"] < 0.54


def test_calibration_shrinks_toward_half_and_keeps_the_side():
    assert calibrate(0.5, "spreads") == 0.5
    for market in CALIBRATION_SLOPE:
        for p in (0.55, 0.7, 0.9, 0.999):
            c = calibrate(p, market)
            assert 0.5 < c < p
            # Symmetric: the other side's probability calibrates to 1 - c.
            assert abs(calibrate(1 - p, market) - (1 - c)) < 1e-9
    # Spreads carry almost nothing, totals a little (2023-2025 backtest).
    assert calibrate(0.9, "spreads") < 0.54 < calibrate(0.9, "totals")


def test_the_comparison_is_written_even_when_no_pick_clears_the_threshold():
    totals = np.array([49.0, 51, 48, 52, 50])  # 2 over, 2 under, 1 push
    q = _q("draftkings", "totals", 50.0, over_price=-110, under_price=-110)
    assert evaluate(1, q, np.zeros(5), totals, 0.5) is None
    cols = priced_columns(q, np.zeros(5), totals, 0.5, "totals")
    assert cols["total_line"] == 50.0 and cols["total_model_over_prob"] == 0.5
    assert cols["total_over_price"] == -110 and cols["total_under_price"] == -110


def test_no_usable_quote_writes_every_column_null():
    for market in ("spreads", "totals"):
        cols = priced_columns(None, np.zeros(3), np.zeros(3), 0.5, market)
        assert set(cols) == set(PRICED_COLUMNS[market])
        assert all(v is None for v in cols.values())
    one_sided = _q("pinnacle", "spreads", -3.0, home_price=-105, away_price=None)
    assert all(v is None for v in priced_columns(
        one_sided, np.ones(3), np.zeros(3), 0.5, "spreads").values())


# --- Engine v2: the calibrated table edge as a pick (migration 0090) ---------

def test_v2_picks_on_the_calibrated_probability_at_two_percent():
    assert V2_EDGE_THRESHOLD == 0.02 and V2_EDGE_PLAY == 0.03
    # Raw 70% over at a -110/-110 total: calibrated ~55%, a ~5-point edge.
    totals = np.array([60.0] * 7 + [40.0] * 3)
    q = _q("pinnacle", "totals", 50.0, over_price=-110, under_price=-110)
    pick = evaluate_calibrated(1, q, np.zeros(10), totals, 0.5)
    assert pick is not None and pick.engine == "v2" and pick.side == "over"
    assert abs(pick.model_prob - calibrate(0.7, "totals")) < 1e-12
    assert abs(pick.edge - (calibrate(0.7, "totals") - 0.5)) < 1e-12
    # v1 on the same game is the raw 70%.
    assert evaluate(1, q, np.zeros(10), totals, 0.5).engine == "v1"


def test_v2_makes_no_pick_inside_two_percent_and_no_moneyline():
    # Raw 60% on a spread calibrates to ~50.6%: no v2 pick, though v1 has one.
    margins = np.array([5.0] * 6 + [-5.0] * 4)
    q = _q("pinnacle", "spreads", 0.5, home_price=-110, away_price=-110)
    assert evaluate_calibrated(1, q, margins, np.zeros(10), 0.5) is None
    assert evaluate(1, q, margins, np.zeros(10), 0.5) is not None
    h2h = _q("pinnacle", "h2h", None, home_price=150, away_price=-170)
    assert evaluate_calibrated(1, h2h, margins, np.zeros(10), 0.9) is None


def test_v2_grade_reports_its_own_edge_tier():
    from worker.core.game_grading import Graded
    from worker.jobs.grade_game_picks import summarise

    def g(market, result):
        return Graded(market, "over", result, 100 / 110 if result == "win" else -1.0, None, None)

    text = summarise([
        (g("totals", "win"), 1.0, 0.56, 0.06),
        (g("spreads", "loss"), 1.0, 0.53, 0.031),
        (g("totals", "loss"), 1.0, 0.53, 0.025),
    ], "v2")
    lines = {
        line.split(" picks ")[0].strip(): line
        for line in text.splitlines() if " picks " in line
    }
    assert "W-L-P 1-1-0" in lines["edge 3%+"]
    assert "totals 64%+" not in lines


# --- Rule plays: the client's line-gap rules (migration 0091) ----------------

def test_rule_thresholds_are_the_clients():
    from worker.core.game_picks import (
        RULE_ML_MIN_PROB,
        RULE_ML_WORST_PRICE,
        RULE_SPREAD_GAP,
        RULE_TOTAL_GAP,
        RULE_TOTAL_TOP_GAP,
    )

    assert (RULE_SPREAD_GAP, RULE_TOTAL_GAP, RULE_TOTAL_TOP_GAP) == (4.0, 6.0, 7.0)
    assert (RULE_ML_MIN_PROB, RULE_ML_WORST_PRICE) == (0.60, -150)


def test_a_spread_rule_play_takes_the_side_our_line_favours():
    from worker.core.game_picks import evaluate_rules

    margins = np.array([7.0] * 6 + [-3.0] * 4)
    # Book: home -2.5. Ours: home by 6.9, a fair -6.9, 4.4 points away: home.
    q = _q("pinnacle", "spreads", -2.5, home_price=-108, away_price=-112)
    pick = evaluate_rules(1, q, 6.9, 50.0, margins, np.zeros(10), 0.6)
    assert pick is not None and pick.engine == "rules"
    assert (pick.side, pick.price, pick.model_line, pick.gap) == ("home", -108, -6.9, 4.4)
    assert abs(pick.model_prob - calibrate(0.6, "spreads")) < 1e-12
    # Ours home by 6.4: 3.9 points away, no play.
    assert evaluate_rules(1, q, 6.4, 50.0, margins, np.zeros(10), 0.6) is None
    # Ours AWAY by 2.0 against home -2.5: 4.5 points the other way, away.
    away = evaluate_rules(1, q, -2.0, 50.0, margins, np.zeros(10), 0.6)
    assert (away.side, away.price, away.model_line, away.gap) == ("away", -112, 2.0, 4.5)


def test_a_total_rule_play_needs_six_points_either_way():
    from worker.core.game_picks import evaluate_rules

    q = _q("pinnacle", "totals", 52.5, over_price=-110, under_price=-110)
    totals = np.full(10, 45.0)
    under = evaluate_rules(1, q, 0.0, 45.5, np.zeros(10), totals, 0.5)
    assert (under.side, under.gap, under.model_line) == ("under", 7.0, 45.5)
    assert evaluate_rules(1, q, 0.0, 47.0, np.zeros(10), totals, 0.5) is None
    over = evaluate_rules(1, q, 0.0, 58.5, np.zeros(10), totals, 0.5)
    assert (over.side, over.gap) == ("over", 6.0)


def test_the_moneyline_is_pulled_toward_the_book():
    from worker.core.game_picks import ML_BLEND_WEIGHTS, blend_win_prob

    assert ML_BLEND_WEIGHTS == (0.073, 1.045)
    # The model at 80% against a book at 55%: the blend sits by the book.
    p = blend_win_prob(0.80, 0.55)
    assert 0.55 < p < 0.58
    assert abs(blend_win_prob(0.5, 0.5) - 0.5) < 1e-12


def test_a_moneyline_rule_play_fires_on_the_model_and_records_the_blend():
    from worker.core.game_picks import RULE_ML_BEST_PRICE, blend_win_prob, evaluate_rules

    assert RULE_ML_BEST_PRICE == -101
    z = np.zeros(10)
    # -140/+120: a favourite. The model's own 66% fires it; the blend is recorded.
    q = _q("pinnacle", "h2h", None, home_price=-140, away_price=120)
    pick = evaluate_rules(1, q, 5.0, 50.0, z, z, 0.66)
    assert pick is not None and (pick.side, pick.price, pick.gap) == ("home", -140, None)
    assert abs(pick.raw_prob - 0.66) < 1e-12
    assert abs(pick.model_prob - blend_win_prob(0.66, devig(-140, 120))) < 1e-12
    assert pick.model_prob < 0.60  # honest number, below the trigger
    # The model under 60%: no play.
    assert evaluate_rules(1, q, 2.0, 50.0, z, z, 0.58) is None
    # The away side, when the model prefers it at a favourite's price.
    away = evaluate_rules(1, _q("pinnacle", "h2h", None, home_price=110, away_price=-130),
                          -5.0, 50.0, z, z, 0.30)
    assert (away.side, away.price) == ("away", -130)
    # Past -150, and plus money, are no play however sure the model is.
    heavy = _q("pinnacle", "h2h", None, home_price=-400, away_price=320)
    assert evaluate_rules(1, heavy, 20.0, 50.0, z, z, 0.99) is None
    dog = _q("pinnacle", "h2h", None, home_price=130, away_price=-150)
    assert evaluate_rules(1, dog, 5.0, 50.0, z, z, 0.70) is None


def test_the_rules_grade_reports_totals_seven_plus():
    from worker.core.game_grading import Graded
    from worker.jobs.grade_game_picks import summarise

    def g(market, result):
        return Graded(market, "over", result, 100 / 110 if result == "win" else -1.0, None, None)

    text = summarise([
        (g("totals", "win"), 1.0, 0.55, 0.03, 7.5),
        (g("totals", "loss"), 1.0, 0.55, 0.03, 6.2),
        (g("spreads", "loss"), 1.0, 0.51, 0.01, 9.0),
        (g("h2h", "win"), 1.0, 0.61, 0.02, None),
    ], "rules")
    lines = {
        line.split(" picks ")[0].strip(): line
        for line in text.splitlines() if " picks " in line
    }
    assert "W-L-P 1-1-0" in lines["totals"]
    assert "W-L-P 1-0-0" in lines["totals 7+"]
    assert "W-L-P 1-0-0" in lines["h2h"]


def test_v2_never_picks_against_our_own_line():
    # Totals: raw 70% over at 50 -> a v2 over at ~5 points of edge. With our
    # fair total at 48 (under), the pick is refused; at 53 it stands.
    totals = np.array([60.0] * 7 + [40.0] * 3)
    q = _q("pinnacle", "totals", 50.0, over_price=-110, under_price=-110)
    assert evaluate_calibrated(1, q, np.zeros(10), totals, 0.5, total_mean=48.0) is None
    assert evaluate_calibrated(1, q, np.zeros(10), totals, 0.5, total_mean=53.0).side == "over"
    # No means: unchanged behaviour.
    assert evaluate_calibrated(1, q, np.zeros(10), totals, 0.5) is not None


def test_agrees_with_our_line_reads_spreads_from_the_home_side():
    from worker.core.game_picks import agrees_with_our_line

    q = _q("pinnacle", "spreads", -7.5, home_price=-103, away_price=-113)
    # UTSA -7.5, ours UTSA -1.6 (margin 1.6): our line says the away side.
    assert agrees_with_our_line(q, "away", 1.6, None)
    assert not agrees_with_our_line(q, "home", 1.6, None)
    assert agrees_with_our_line(q, "home", 9.0, None)
    assert agrees_with_our_line(q, "home", 7.5, None)  # equal: no contradiction
