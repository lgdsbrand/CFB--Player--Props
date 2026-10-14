"""Fantasy game model picks (phase F3) — offline.

His gap rule against one book's quote: spread 4+ points, total 6+, compared in
points, each version of the formula its own engine, one pick per game and
market.
"""

from __future__ import annotations

import pytest

from worker.core.fantasy_picks import evaluate, his_probability
from worker.core.game_picks import Quote
from worker.jobs.nfl_fantasy_picks import PicksReport, picks_for


def _spread(line: float, home: int = -110, away: int = -110, book: str = "pinnacle") -> Quote:
    return Quote(1, book, "spreads", line, home_price=home, away_price=away)


def _total(line: float, over: int = -110, under: int = -110, book: str = "pinnacle") -> Quote:
    return Quote(1, book, "totals", line, over_price=over, under_price=under)


def test_his_probability_is_his_win_formula():
    assert his_probability(3.3) == pytest.approx(0.6811365836, abs=1e-9)  # the sheet's E44
    assert his_probability(0) == 0.5


def test_a_spread_four_points_off_is_a_play_on_the_model_s_side():
    # Model: home by 7.5 (home -7.5). Book: home -3.5. Ours makes home the bigger favourite.
    pick = evaluate(10, _spread(-3.5), home_margin=7.5, total=45.0, version="fixed")
    assert pick is not None
    assert (pick.side, pick.line, pick.model_line, pick.gap) == ("home", -3.5, -7.5, 4.0)
    assert pick.engine == "fantasy"
    assert pick.model_prob == pytest.approx(his_probability(4.0))
    assert pick.edge == pytest.approx(his_probability(4.0) - 0.5)


def test_the_other_way_takes_the_away_side_at_the_away_price():
    pick = evaluate(10, _spread(-6.5, home=-105, away=-115), home_margin=1.0, total=45.0,
                    version="sheet")
    assert pick is not None
    assert (pick.side, pick.price, pick.gap, pick.engine) == ("away", -115, 5.5, "fantasy_sheet")


def test_under_four_points_is_no_play():
    assert evaluate(10, _spread(-4.0), home_margin=7.9, total=45.0, version="fixed") is None


def test_totals_need_six_points():
    assert evaluate(10, _total(44.5), home_margin=0, total=50.4, version="fixed") is None
    over = evaluate(10, _total(44.5), home_margin=0, total=50.5, version="fixed")
    assert over is not None and (over.side, over.gap) == ("over", 6.0)
    under = evaluate(10, _total(51.5), home_margin=0, total=44.0, version="fixed")
    assert under is not None and under.side == "under"


def test_a_one_sided_quote_is_no_play():
    assert evaluate(10, Quote(1, "pinnacle", "spreads", -3.5, home_price=-110),
                    home_margin=10, total=45, version="fixed") is None


def test_each_version_picks_once_per_market_and_a_made_pick_is_not_remade():
    games = [{"id": 10}]
    projections = {
        (10, "fixed"): {"home_margin": 8.0, "total": 52.0},   # spread 4.5 off, total 6.5 off
        (10, "sheet"): {"home_margin": 5.0, "total": 52.0},   # spread 1.5 off: no play
    }
    quotes = {10: [_spread(-3.5, book="draftkings"), _spread(-3.0, book="pinnacle"),
                   _total(45.5, book="fanduel")]}
    report = PicksReport()
    picks = picks_for(games, projections, quotes, {("fantasy", 10, "totals")}, report)

    got = {(p.engine, p.market, p.sportsbook_key) for p in picks}
    # Pinnacle first; the fixed total was already made; the sheet's spread is too close.
    assert got == {("fantasy", "spreads", "pinnacle"), ("fantasy_sheet", "totals", "fanduel")}
    assert report.skipped["already picked"] == 1


def test_a_game_without_a_projection_is_skipped_by_name():
    report = PicksReport()
    assert picks_for([{"id": 11}], {}, {}, set(), report) == []
    assert report.skipped == {"fantasy: no projection": 1, "fantasy_sheet: no projection": 1}
