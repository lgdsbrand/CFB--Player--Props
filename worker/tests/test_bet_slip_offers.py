"""Bet links for the slip (migration 0089) — parsing and orientation, offline.

The failure worth catching is a link attached to the wrong side: the slip
would open the Under at the book while showing the Over's price. So each test
checks that a side's link travels with that side's price, including at a
neutral site where the provider's home team is our away team.
"""

from __future__ import annotations

from typing import Any

from worker.adapters.odds.base import GameBookMarket, GameOutcome
from worker.adapters.odds.theoddsapi import (
    LINK_PARAMS,
    TheOddsApiAdapter,
    parse_event_odds,
    parse_game_odds,
)
from worker.jobs.ingest_game_odds import offer_rows, orient

FD = "https://sportsbook.fanduel.com/addToBetslip?marketId=42.1&selectionId="
FD_EVENT = "https://sportsbook.fanduel.com/football/ncaa-football-games/a-%40-b-1"

PROPS_PAYLOAD = {
    "id": "evt1",
    "bookmakers": [
        {
            "key": "fanduel",
            "title": "FanDuel",
            "link": FD_EVENT,
            "sid": "1",
            "markets": [
                {
                    "key": "player_pass_yds",
                    "outcomes": [
                        {"name": "Under", "description": "Rickie Collins",
                         "price": -114, "point": 193.5, "link": FD + "27"},
                        {"name": "Over", "description": "Rickie Collins",
                         "price": -106, "point": 193.5, "link": FD + "35"},
                    ],
                }
            ],
        },
        {
            "key": "betonlineag",
            "title": "BetOnline.ag",
            "link": "https://sports.betonline.ag/sportsbook/football/ncaa/game/9",
            "markets": [
                {
                    "key": "player_pass_yds",
                    "outcomes": [
                        {"name": "Over", "description": "Rickie Collins",
                         "price": -110, "point": 195.5, "link": None},
                    ],
                }
            ],
        },
    ],
}


def test_a_prop_side_keeps_its_own_link():
    quotes, _ = parse_event_odds(PROPS_PAYLOAD)
    prices = {p.sportsbook_key: p for p in quotes[0].prices}
    fd = prices["fanduel"]
    assert (fd.over_price, fd.over_link) == (-106, FD + "35")
    assert (fd.under_price, fd.under_link) == (-114, FD + "27")
    assert fd.event_link == FD_EVENT


def test_a_book_with_only_a_game_page_keeps_that():
    quotes, _ = parse_event_odds(PROPS_PAYLOAD)
    bol = next(p for p in quotes[0].prices if p.sportsbook_key == "betonlineag")
    assert bol.over_link is None and bol.under_link is None
    assert bol.event_link.endswith("/game/9")


GAME_PAYLOAD = [
    {
        "id": "evt2",
        "home_team": "Georgia Bulldogs",
        "away_team": "Clemson Tigers",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "link": "https://sportsbook.draftkings.com/event/5",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Georgia Bulldogs", "price": -110, "point": -3.5,
                             "link": "https://sportsbook.draftkings.com/?outcomes=G"},
                            {"name": "Clemson Tigers", "price": -110, "point": 3.5,
                             "link": "https://sportsbook.draftkings.com/?outcomes=C"},
                        ],
                    }
                ],
            }
        ],
    }
]


def test_game_outcomes_and_the_game_page_carry_links():
    [event] = parse_game_odds(GAME_PAYLOAD)
    [market] = event.markets
    assert market.event_link == "https://sportsbook.draftkings.com/event/5"
    assert [o.link[-1] for o in market.outcomes] == ["G", "C"]


def test_at_a_neutral_site_each_link_follows_its_team_not_the_label():
    # The provider calls Georgia home; in OUR game Clemson is home.
    [event] = parse_game_odds(GAME_PAYLOAD)
    [market] = event.markets
    side_of = {"Georgia Bulldogs": "away", "Clemson Tigers": "home"}.get
    row, _ = orient(market, side_of)
    assert row.line == 3.5
    assert row.home_link.endswith("=C") and row.away_link.endswith("=G")
    assert row.event_link == "https://sportsbook.draftkings.com/event/5"


def test_totals_links_and_unpriced_sides_in_offer_rows():
    market = GameBookMarket(
        "fanduel", "FanDuel", "totals", None,
        (GameOutcome("Over", -105, 51.5, FD + "o"), GameOutcome("Under", None, 51.5, FD + "u")),
    )
    row, _ = orient(market, {}.get)
    # A side with no price is not an offer, whatever link it came with.
    assert offer_rows(row) == [("over", -105, FD + "o")]


def test_links_do_not_count_as_a_price_move():
    a = GameBookMarket("dk", "DraftKings", "h2h", None,
                       (GameOutcome("A", -150, None, "x"), GameOutcome("B", 130, None, "y")))
    b = GameBookMarket("dk", "DraftKings", "h2h", None,
                       (GameOutcome("A", -150, None, "z"), GameOutcome("B", 130, None, None)))
    side_of = {"A": "home", "B": "away"}.get
    assert orient(a, side_of)[0].price_key() == orient(b, side_of)[0].price_key()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append(params)
        return {} if "/events/" in path else []


def test_every_live_capture_asks_for_links():
    client = _Recorder()
    adapter = TheOddsApiAdapter("k", client=client)
    adapter.fetch_props_raw("e1", ["pass_yards"])
    adapter.fetch_game_odds()
    adapter.fetch_period_odds("e1")
    assert len(client.calls) == 3
    for params in client.calls:
        assert {k: params[k] for k in LINK_PARAMS} == LINK_PARAMS
