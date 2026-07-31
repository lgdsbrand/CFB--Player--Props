"""Tests for the odds adapter seam (Phase 3a).

All offline. The probe job talks to the network; the parsing, mapping and key
hygiene that the probe's conclusions rest on are pinned here against captured
payload shapes, so a provider response change surfaces as a failing test rather
than as a quietly under-counted coverage report.
"""

from __future__ import annotations

import pytest

from worker.adapters.odds import (
    NullOddsAdapter,
    OddsAdapter,
    OddsAdapterError,
    TheOddsApiAdapter,
    get_adapter,
)
from worker.adapters.odds.http import redact
from worker.adapters.odds.markets import (
    OUR_KEY_TO_PROVIDER,
    PROVIDER_TO_OUR_KEY,
    our_key,
    provider_keys,
)
from worker.adapters.odds.theoddsapi import parse_event_odds


def _event(bookmakers: list[dict]) -> dict:
    return {
        "id": "evt_1",
        "sport_key": "americanfootball_ncaaf",
        "commence_time": "2025-10-18T18:00:00Z",
        "home_team": "Georgia Bulldogs",
        "away_team": "Ole Miss Rebels",
        "bookmakers": bookmakers,
    }


def _book(key: str, title: str, markets: list[dict]) -> dict:
    return {"key": key, "title": title, "markets": markets}


class TestMarketMapping:
    def test_mapping_is_bijective(self):
        # A collision would make provider->ours lossy and silently merge two
        # markets into one.
        assert len(PROVIDER_TO_OUR_KEY) == len(OUR_KEY_TO_PROVIDER)

    def test_every_seeded_market_is_mapped(self):
        # Mirrors the nine rows seeded in migration 0009. If a market is added
        # there without a mapping here, coverage would be understated.
        expected = {
            "pass_yards",
            "pass_tds",
            "pass_attempts",
            "pass_completions",
            "rush_yards",
            "rush_attempts",
            "receptions",
            "rec_yards",
            "anytime_td",
        }
        assert set(OUR_KEY_TO_PROVIDER) == expected

    def test_unknown_key_raises_rather_than_dropping(self):
        with pytest.raises(KeyError, match="no_such_market"):
            provider_keys(["pass_yards", "no_such_market"])

    def test_provider_keys_preserves_order(self):
        assert provider_keys(["rec_yards", "pass_yards"]) == [
            "player_reception_yds",
            "player_pass_yds",
        ]

    def test_our_key_returns_none_for_unrecognized(self):
        assert our_key("player_field_goals") is None


class TestParseTwoWay:
    def test_over_and_under_become_one_two_way_price(self):
        payload = _event([
            _book("draftkings", "DraftKings", [{
                "key": "player_pass_yds",
                "outcomes": [
                    {"name": "Over", "description": "Carson Beck",
                     "price": -115, "point": 245.5},
                    {"name": "Under", "description": "Carson Beck",
                     "price": -105, "point": 245.5},
                ],
            }]),
        ])
        quotes, diagnostics = parse_event_odds(payload)

        assert len(quotes) == 1
        quote = quotes[0]
        assert quote.market_key == "pass_yards"
        assert quote.player_name == "Carson Beck"
        assert len(quote.prices) == 1

        price = quote.prices[0]
        assert price.is_two_way
        assert (price.over_price, price.under_price) == (-115, -105)
        assert price.line == 245.5
        assert diagnostics.quotes_two_way == 1
        assert diagnostics.quotes_one_sided == 0

    def test_two_books_at_the_same_line_stay_separate(self):
        # Regression: merging them would pair one book's Over against another's
        # Under and de-vig a market that exists at neither book.
        outcomes_dk = [
            {"name": "Over", "description": "Carson Beck", "price": -115, "point": 245.5},
            {"name": "Under", "description": "Carson Beck", "price": -105, "point": 245.5},
        ]
        outcomes_fd = [
            {"name": "Over", "description": "Carson Beck", "price": -120, "point": 245.5},
            {"name": "Under", "description": "Carson Beck", "price": 100, "point": 245.5},
        ]
        payload = _event([
            _book("draftkings", "DraftKings",
                  [{"key": "player_pass_yds", "outcomes": outcomes_dk}]),
            _book("fanduel", "FanDuel",
                  [{"key": "player_pass_yds", "outcomes": outcomes_fd}]),
        ])
        quotes, diagnostics = parse_event_odds(payload)

        assert len(quotes) == 1
        prices = {p.sportsbook_key: p for p in quotes[0].prices}
        assert set(prices) == {"draftkings", "fanduel"}
        assert (prices["draftkings"].over_price, prices["draftkings"].under_price) == (-115, -105)
        assert (prices["fanduel"].over_price, prices["fanduel"].under_price) == (-120, 100)
        assert diagnostics.quotes_two_way == 2

    def test_alternate_lines_from_one_book_are_kept_separate(self):
        payload = _event([
            _book("draftkings", "DraftKings", [{
                "key": "player_receptions",
                "outcomes": [
                    {"name": "Over", "description": "Ryan Williams", "price": -130, "point": 4.5},
                    {"name": "Under", "description": "Ryan Williams", "price": 105, "point": 4.5},
                    {"name": "Over", "description": "Ryan Williams", "price": 140, "point": 5.5},
                    {"name": "Under", "description": "Ryan Williams", "price": -170, "point": 5.5},
                ],
            }]),
        ])
        quotes, _ = parse_event_odds(payload)
        assert len(quotes) == 1
        assert sorted(p.line for p in quotes[0].prices) == [4.5, 5.5]


class TestParseOneSided:
    def test_one_sided_price_is_kept_but_flagged(self):
        # A longshot anytime-TD Yes with no No. Keeping it preserves the model
        # lean; flagging it is what stops a fabricated de-vig downstream.
        payload = _event([
            _book("draftkings", "DraftKings", [{
                "key": "player_anytime_td",
                "outcomes": [
                    {"name": "Yes", "description": "Nate Frazier", "price": 600},
                ],
            }]),
        ])
        quotes, diagnostics = parse_event_odds(payload)

        assert len(quotes) == 1
        price = quotes[0].prices[0]
        assert not price.is_two_way
        assert price.over_price == 600
        assert price.under_price is None
        assert diagnostics.quotes_one_sided == 1
        assert quotes[0].two_way_prices == []


class TestParseBinaryMarket:
    def test_yes_no_maps_to_over_under_at_line_half(self):
        # Migration 0006 stores anytime TD as "over 0.5 offensive TDs" so every
        # market shares one representation (CLAUDE.md §1).
        payload = _event([
            _book("fanduel", "FanDuel", [{
                "key": "player_anytime_td",
                "outcomes": [
                    {"name": "Yes", "description": "Jeremiah Smith", "price": -150},
                    {"name": "No", "description": "Jeremiah Smith", "price": 120},
                ],
            }]),
        ])
        quotes, _ = parse_event_odds(payload)

        price = quotes[0].prices[0]
        assert quotes[0].market_key == "anytime_td"
        assert price.line == 0.5
        assert (price.over_price, price.under_price) == (-150, 120)


class TestParseDiagnostics:
    def test_unmapped_markets_are_reported_not_dropped_silently(self):
        payload = _event([
            _book("draftkings", "DraftKings", [
                {"key": "player_field_goals", "outcomes": [
                    {"name": "Over", "description": "Kicker", "price": -110, "point": 1.5},
                ]},
                {"key": "player_pass_yds", "outcomes": [
                    {"name": "Over", "description": "Carson Beck", "price": -110, "point": 250.5},
                    {"name": "Under", "description": "Carson Beck", "price": -110, "point": 250.5},
                ]},
            ]),
        ])
        quotes, diagnostics = parse_event_odds(payload)

        assert diagnostics.markets_unmapped == {"player_field_goals"}
        assert "player_pass_yds" in diagnostics.markets_seen
        assert [q.market_key for q in quotes] == ["pass_yards"]

    def test_malformed_outcomes_are_counted(self):
        payload = _event([
            _book("draftkings", "DraftKings", [{
                "key": "player_pass_yds",
                "outcomes": [
                    {"name": "Over", "price": -110, "point": 250.5},  # no player
                    {"name": "Over", "description": "X", "point": 250.5},  # no price
                    {"name": "Push", "description": "X", "price": -110, "point": 250.5},
                ],
            }]),
        ])
        quotes, diagnostics = parse_event_odds(payload)

        assert quotes == []
        assert diagnostics.outcomes_total == 3
        assert diagnostics.outcomes_unparsed == 3

    def test_empty_payload_is_not_an_error(self):
        quotes, diagnostics = parse_event_odds({})
        assert quotes == []
        assert diagnostics.outcomes_total == 0

    def test_bookmakers_are_recorded(self):
        payload = _event([
            _book("draftkings", "DraftKings", []),
            _book("betmgm", "BetMGM", []),
        ])
        _, diagnostics = parse_event_odds(payload)
        assert diagnostics.bookmakers == {"draftkings", "betmgm"}


class TestKeyHygiene:
    """CLAUDE.md §0: the key is a query parameter, so URLs are secret-bearing."""

    def test_redact_strips_the_api_key(self):
        url = "https://api.the-odds-api.com/v4/sports?apiKey=abc123secret&all=true"
        assert "abc123secret" not in redact(url)
        assert "apiKey=<redacted>" in redact(url)

    def test_redact_handles_key_at_end_of_url(self):
        url = "https://api.the-odds-api.com/v4/sports?all=true&apiKey=abc123secret"
        assert "abc123secret" not in redact(url)

    def test_redact_is_case_insensitive(self):
        assert "s3cret" not in redact("https://x/?APIKEY=s3cret")

    def test_empty_key_refuses_to_build_a_client(self):
        with pytest.raises(OddsAdapterError, match="ODDS_API_KEY is empty"):
            TheOddsApiAdapter("")


class TestAdapterRegistry:
    def test_null_adapter_satisfies_the_protocol(self):
        assert isinstance(NullOddsAdapter(), OddsAdapter)

    def test_live_adapter_satisfies_the_protocol(self):
        assert isinstance(TheOddsApiAdapter("dummy-key"), OddsAdapter)

    def test_null_adapter_serves_nothing_and_never_raises(self):
        adapter = NullOddsAdapter()
        assert adapter.list_events() == []
        assert adapter.fetch_props("evt_1") == []
        assert adapter.quota.remaining is None

    def test_get_adapter_resolves_known_names(self):
        assert isinstance(get_adapter("none"), NullOddsAdapter)
        assert isinstance(
            get_adapter("theoddsapi", api_key="dummy-key"), TheOddsApiAdapter
        )

    def test_unknown_adapter_raises_rather_than_falling_back(self):
        # A silent fallback would render a config typo identically to a genuine
        # "no lines available".
        with pytest.raises(OddsAdapterError, match="Unknown odds adapter"):
            get_adapter("bovada-scraper")
