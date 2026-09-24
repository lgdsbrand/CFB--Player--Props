"""Game odds capture (CLAUDE.md §11, G1) — parsing and orientation, offline.

The failure this file exists to catch is the invisible one: a spread stored
from the wrong team's perspective. Every number still renders and the favourite
is silently the wrong team. The neutral-site case is where the provider's home
team and ours disagree, so it gets its own tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

from worker.adapters.odds.base import GameBookMarket, GameOutcome
from worker.adapters.odds.theoddsapi import (
    DEFAULT_REGIONS,
    GAME_REGIONS,
    parse_game_odds,
)
from worker.core.name_match import TeamResolver
from worker.jobs.ingest_game_odds import orient, side_resolver

TEAMS = [
    {"id": 10, "school": "Georgia", "mascot": "Bulldogs", "alt_name": None},
    {"id": 20, "school": "Clemson", "mascot": "Tigers", "alt_name": None},
    {"id": 30, "school": "Alabama", "mascot": "Crimson Tide", "alt_name": None},
]

PAYLOAD = [
    {
        "id": "evt1",
        "sport_key": "americanfootball_ncaaf",
        "commence_time": "2026-09-26T19:30:00Z",
        "home_team": "Georgia Bulldogs",
        "away_team": "Clemson Tigers",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "last_update": "2026-09-23T12:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-09-23T12:01:00Z",
                        "outcomes": [
                            {"name": "Georgia Bulldogs", "price": -250},
                            {"name": "Clemson Tigers", "price": 210},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Georgia Bulldogs", "price": -108, "point": -7.0},
                            {"name": "Clemson Tigers", "price": -102, "point": 7.0},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -105, "point": 51.5},
                            {"name": "Under", "price": -105, "point": 51.5},
                        ],
                    },
                    {"key": "h2h_lay", "outcomes": []},
                ],
            }
        ],
    }
]


def _game(home: int, away: int) -> dict:
    return {"id": 1, "home_team_id": home, "away_team_id": away}


def _markets():
    (event,) = parse_game_odds(PAYLOAD)
    return event, {m.market: m for m in event.markets}


def test_sharp_regions_never_reach_the_props_default():
    # Props bill per region too; widening the default would triple them.
    assert DEFAULT_REGIONS == "us"
    assert set(GAME_REGIONS.split(",")) == {"us", "us_ex", "eu"}


def test_parse_keeps_game_markets_and_drops_the_rest():
    event, markets = _markets()
    assert event.event.home_team == "Georgia Bulldogs"
    assert event.event.commence_time == datetime(2026, 9, 26, 19, 30, tzinfo=UTC)
    assert set(markets) == {"h2h", "spreads", "totals"}
    # The market's own timestamp wins over the bookmaker's.
    assert markets["h2h"].book_updated_at == datetime(2026, 9, 23, 12, 1, tzinfo=UTC)
    assert markets["spreads"].book_updated_at == datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def test_orient_when_provider_and_we_agree_on_home():
    event, markets = _markets()
    side_of = side_resolver(event, _game(home=10, away=20), TeamResolver(TEAMS))

    h2h, _ = orient(markets["h2h"], side_of)
    assert (h2h.home_price, h2h.away_price, h2h.line) == (-250, 210, None)

    spread, _ = orient(markets["spreads"], side_of)
    assert spread.line == -7.0  # home (Georgia) favoured
    assert (spread.home_price, spread.away_price) == (-108, -102)

    total, _ = orient(markets["totals"], side_of)
    assert (total.line, total.over_price, total.under_price) == (51.5, -105, -105)
    assert total.home_price is None


def test_neutral_site_flip_puts_every_price_on_the_right_team():
    """Our home team is the provider's AWAY team. Nothing may follow its label."""
    event, markets = _markets()
    side_of = side_resolver(event, _game(home=20, away=10), TeamResolver(TEAMS))

    h2h, _ = orient(markets["h2h"], side_of)
    assert (h2h.home_price, h2h.away_price) == (210, -250)

    spread, _ = orient(markets["spreads"], side_of)
    assert spread.line == 7.0  # our home (Clemson) is the underdog
    assert (spread.home_price, spread.away_price) == (-102, -108)


def test_one_unresolvable_name_takes_the_remaining_side():
    event, markets = _markets()
    # Only Georgia is known; the matcher already pinned the game on it.
    resolver = TeamResolver([TEAMS[0]])
    side_of = side_resolver(event, _game(home=10, away=99), resolver)
    spread, _ = orient(markets["spreads"], side_of)
    assert spread.line == -7.0
    assert spread.away_price == -102


def test_names_that_resolve_to_neither_team_refuse_the_quote():
    event, markets = _markets()
    side_of = side_resolver(event, _game(home=30, away=99), TeamResolver(TEAMS))
    row, reason = orient(markets["h2h"], side_of)
    assert row is None and reason == "h2h: unknown team"


def _market(kind: str, *outcomes: GameOutcome) -> GameBookMarket:
    return GameBookMarket("dk", "DraftKings", kind, None, tuple(outcomes))


def _fixed(mapping):
    return mapping.get


def test_spread_sides_that_do_not_mirror_are_refused():
    m = _market(
        "spreads",
        GameOutcome("A", -110, -3.5),
        GameOutcome("B", -110, 3.0),
    )
    row, reason = orient(m, _fixed({"A": "home", "B": "away"}))
    assert row is None and reason == "spreads: sides do not mirror"


def test_a_one_sided_spread_keeps_home_perspective():
    m = _market("spreads", GameOutcome("B", 120, 6.5))
    row, _ = orient(m, _fixed({"A": "home", "B": "away"}))
    assert row.line == -6.5
    assert (row.home_price, row.away_price) == (None, 120)


def test_totals_with_disagreeing_points_are_refused():
    m = _market(
        "totals",
        GameOutcome("Over", -110, 50.5),
        GameOutcome("Under", -110, 51.5),
    )
    row, reason = orient(m, _fixed({}))
    assert row is None and reason == "totals: no single point"


def test_price_key_ignores_timestamps_and_float_noise():
    m1 = _market("totals", GameOutcome("Over", -110, 50.5), GameOutcome("Under", -110, 50.5))
    m2 = GameBookMarket("dk", "DraftKings", "totals", datetime(2026, 1, 1, tzinfo=UTC),
                        m1.outcomes)
    a, _ = orient(m1, _fixed({}))
    b, _ = orient(m2, _fixed({}))
    assert a.price_key() == b.price_key()
