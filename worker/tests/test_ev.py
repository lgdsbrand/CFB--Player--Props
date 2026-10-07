"""Price-based +EV (migration 0087) — offline.

The two things measured on 2026-10-03 that made this the method, each
pinned: proportional de-vig manufactures longshot "edges" that power de-vig
does not, and a book off the sharp line is a different bet. Plus the capture
side: every captured game gets an entry, so a game whose +EV price has gone
is cleared rather than left standing.
"""

from __future__ import annotations

import pytest

from worker.core.ev import (
    EvQuote,
    ev_wagers,
    expected_value,
    implied,
    power_devig,
)
from worker.jobs.ingest_game_odds import GameOddsRow, ev_rows


def test_power_devig_sums_to_one_and_matches_proportional_at_evens():
    p = power_devig(-110, -110)
    assert p == pytest.approx(0.5, abs=1e-9)
    a = power_devig(-250, 210)
    b = power_devig(210, -250)
    assert a + b == pytest.approx(1.0, abs=1e-9)


def test_power_gives_a_longshot_less_than_proportional_does():
    fav, dog = -1600, 900
    proportional = implied(dog) / (implied(fav) + implied(dog))
    power = 1 - power_devig(fav, dog)
    assert power < proportional
    # The 2026-10-03 artefact: a +1050 price looked +EV only under proportional.
    assert expected_value(proportional, 1050) > 0.03
    assert expected_value(power, 1050) < expected_value(proportional, 1050)


def test_expected_value_is_fair_prob_times_decimal_odds_minus_one():
    assert expected_value(0.5, 100) == pytest.approx(0.0)
    assert expected_value(0.5, -110) == pytest.approx(0.5 * (1 + 100 / 110) - 1)
    assert expected_value(0.25, 300) == pytest.approx(0.0)


def _q(book, market, line, first, second):
    return EvQuote(book, market, line, first, second)


def test_only_books_on_the_sharp_line_and_inside_the_price_range_are_compared():
    quotes = [
        _q("pinnacle", "spreads", -7.0, -105, -105),
        _q("draftkings", "spreads", -7.0, 105, -125),     # home +105 at a 50% fair: +2.5%
        _q("fanduel", "spreads", -6.5, 110, -130),        # different line: not compared
        _q("pinnacle", "h2h", None, -2000, 1100),
        _q("betmgm", "h2h", None, -3000, 1500),            # +1500 is outside the range
    ]
    got = ev_wagers(quotes)
    assert [(w.book_key, w.market, w.side, w.price) for w in got] == [
        ("draftkings", "spreads", "home", 105),
    ]
    assert got[0].fair_prob == pytest.approx(0.5)
    assert got[0].ev == pytest.approx(0.025)


def test_no_sharp_price_or_an_implausible_one_means_no_wagers():
    assert ev_wagers([_q("draftkings", "totals", 50.5, 120, 120)]) == []
    broken = [_q("pinnacle", "totals", 50.5, -100000, -100000),
              _q("draftkings", "totals", 50.5, 120, -140)]
    assert ev_wagers(broken) == []


def _row(book, market, line, home=None, away=None, over=None, under=None, period="full"):
    return GameOddsRow(sportsbook_key=book, sportsbook_name=book, market=market, line=line,
                       home_price=home, away_price=away, over_price=over, under_price=under,
                       book_updated_at=None, period=period)


def test_every_captured_game_gets_an_entry_so_a_vanished_price_is_cleared():
    oriented = [
        (1, _row("pinnacle", "totals", 50.5, over=-105, under=-105)),
        (1, _row("draftkings", "totals", 50.5, over=110, under=-130)),
        (2, _row("pinnacle", "totals", 44.5, over=-105, under=-105)),
        (2, _row("draftkings", "totals", 44.5, over=-115, under=-105)),
        (3, _row("draftkings", "totals", 61.5, over=-110, under=-110, period="h1")),
    ]
    wagers = ev_rows(oriented)
    assert set(wagers) == {1, 2, 3}
    assert [(w.book_key, w.side, w.price) for w in wagers[1]] == [("draftkings", "over", 110)]
    assert wagers[2] == [] and wagers[3] == []
