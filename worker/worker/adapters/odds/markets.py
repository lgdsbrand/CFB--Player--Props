"""Market vocabulary translation: our `markets.key` <-> The Odds API's keys.

SPORT- AND PROVIDER-SPECIFIC (CLAUDE.md §3, §9.1). This table is the single
place a provider's naming leaks into the project, so swapping providers is an
edit here plus a new client — not a search across the modelling code.

Treat the right-hand column as a HYPOTHESIS until the probe confirms it. The
provider's documented market keys are not the same thing as the market keys its
plan actually serves for NCAAF, and CFBD already taught us that documentation
and behaviour diverge silently (see the 2,000-row truncation and the string-vs-int
athlete ids found in Phase 2). `probe_odds` reports which of these came back with
real data; anything unconfirmed stays unconfirmed.
"""

from __future__ import annotations

# our markets.key -> The Odds API market key
OUR_KEY_TO_PROVIDER: dict[str, str] = {
    "pass_yards": "player_pass_yds",
    "pass_tds": "player_pass_tds",
    "pass_attempts": "player_pass_attempts",
    "pass_completions": "player_pass_completions",
    "rush_yards": "player_rush_yds",
    "rush_attempts": "player_rush_attempts",
    "receptions": "player_receptions",
    "rec_yards": "player_reception_yds",
    "anytime_td": "player_anytime_td",
}

PROVIDER_TO_OUR_KEY: dict[str, str] = {
    provider: ours for ours, provider in OUR_KEY_TO_PROVIDER.items()
}

# The provider's key for college football.
NCAAF_SPORT_KEY = "americanfootball_ncaaf"

# ...and for the NFL. The MARKET keys above are shared between the two -- The
# Odds API names `player_pass_yds` the same in both -- so a second sport is this
# one line plus a sport key, not a second vocabulary. That is the whole reason
# the translation table sits in one module.
NFL_SPORT_KEY = "americanfootball_nfl"

SPORT_KEY_BY_SPORT: dict[str, str] = {
    "cfb": NCAAF_SPORT_KEY,
    "nfl": NFL_SPORT_KEY,
}


def sport_key_for(sport: str) -> str:
    """Our `sport` -> the provider's sport key.

    Raises on an unknown sport rather than defaulting to college. A backfill
    spends real credits per call, so a typo that quietly bought the wrong
    sport's slate would cost money to discover.
    """
    try:
        return SPORT_KEY_BY_SPORT[sport]
    except KeyError:
        raise KeyError(
            f"No Odds API sport key for sport {sport!r}. "
            f"Known: {sorted(SPORT_KEY_BY_SPORT)}"
        ) from None

# Binary markets price their two sides as Yes/No rather than Over/Under. Our
# schema stores anytime TD as "over 0.5 offensive TDs" (migration 0006), so Yes
# maps to over and No to under — which is what keeps every market speaking the
# same language (CLAUDE.md §1) instead of anytime TD needing its own code path.
YES_NO_MARKETS = frozenset({"anytime_td"})

# Line to record for binary markets, matching markets.default_line.
BINARY_LINE = 0.5

OVER_LABELS = frozenset({"over", "yes"})
UNDER_LABELS = frozenset({"under", "no"})


def provider_keys(our_keys: list[str] | None = None) -> list[str]:
    """Translate our market keys into the provider's, preserving order.

    Unknown keys raise rather than being dropped: silently requesting eight
    markets when nine were asked for would understate coverage in exactly the
    place we are trying to measure it.
    """
    keys = our_keys if our_keys is not None else list(OUR_KEY_TO_PROVIDER)
    missing = [k for k in keys if k not in OUR_KEY_TO_PROVIDER]
    if missing:
        raise KeyError(
            f"No Odds API mapping for market key(s) {missing}. "
            f"Known: {sorted(OUR_KEY_TO_PROVIDER)}"
        )
    return [OUR_KEY_TO_PROVIDER[k] for k in keys]


def our_key(provider_key: str) -> str | None:
    """Translate a provider market key back to ours, or None if unrecognized."""
    return PROVIDER_TO_OUR_KEY.get(provider_key)
