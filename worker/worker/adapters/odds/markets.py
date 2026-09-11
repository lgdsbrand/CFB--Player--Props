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

from collections.abc import Sequence

# our markets.key -> The Odds API market key.
#
# THE FULL-GAME MARKETS. What every run requests by default, for both sports,
# and the only markets `backfill_odds` and `probe_odds` ever ask for.
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

# First-quarter markets: NFL ONLY, and OPT-IN.
#
# Probed 2026-09-09: these three are posted by DraftKings alone, and college
# posts no quarter player market at all. `player_anytime_td_q1` exists too but
# is one-way on every book, so it could never be de-vigged or graded -- it is
# deliberately absent.
#
# OPT-IN because the scheduled odds crons bill the shared paid pool: nothing
# requests these unless `ingest_odds --markets` names them, so adding them here
# changes no cron's spend. Their `markets` rows are inactive (migration 0065),
# so a stored line reaches /no-vig and nothing else.
FIRST_QUARTER_KEY_TO_PROVIDER: dict[str, str] = {
    "q1_pass_yards": "player_pass_yds_q1",
    "q1_rush_yards": "player_rush_yds_q1",
    "q1_rec_yards": "player_reception_yds_q1",
}

# Markets a sport may request on top of the full-game set, by name only.
OPT_IN_MARKETS_BY_SPORT: dict[str, dict[str, str]] = {
    "nfl": FIRST_QUARTER_KEY_TO_PROVIDER,
}

_ALL_KEYS_TO_PROVIDER: dict[str, str] = {
    **OUR_KEY_TO_PROVIDER,
    **FIRST_QUARTER_KEY_TO_PROVIDER,
}

# Parsing must recognise EVERY market we can ask for, or a first-quarter
# response would be counted as unmapped and dropped.
PROVIDER_TO_OUR_KEY: dict[str, str] = {
    provider: ours for ours, provider in _ALL_KEYS_TO_PROVIDER.items()
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


def markets_for(sport: str, requested: Sequence[str] | None = None) -> list[str]:
    """Our market keys one run should request for one sport.

    None means the full-game markets, sorted -- exactly what every run asked for
    before first-quarter markets existed, which is what keeps the crons'
    spend unchanged.

    An explicit list is checked against what THIS sport can carry, and a key it
    cannot carry raises instead of being dropped. `q1_rec_yards` for college
    would bill nothing (no book posts it) and store nothing, and a run that
    succeeded while capturing nothing is indistinguishable from "the book had
    no lines" -- the absence-of-evidence mistake this adapter was built to avoid.
    """
    sport_key_for(sport)
    if requested is None:
        return sorted(OUR_KEY_TO_PROVIDER)
    keys = sorted(set(requested))
    if not keys:
        raise ValueError("No markets requested.")
    allowed = {**OUR_KEY_TO_PROVIDER, **OPT_IN_MARKETS_BY_SPORT.get(sport, {})}
    wrong = [key for key in keys if key not in allowed]
    if wrong:
        raise ValueError(
            f"Market key(s) {wrong} are not available for sport {sport!r}. "
            f"Allowed: {sorted(allowed)}"
        )
    return keys


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
    place we are trying to measure it. None means the full-game markets.
    """
    keys = our_keys if our_keys is not None else list(OUR_KEY_TO_PROVIDER)
    missing = [k for k in keys if k not in _ALL_KEYS_TO_PROVIDER]
    if missing:
        raise KeyError(
            f"No Odds API mapping for market key(s) {missing}. "
            f"Known: {sorted(_ALL_KEYS_TO_PROVIDER)}"
        )
    return [_ALL_KEYS_TO_PROVIDER[k] for k in keys]


def our_key(provider_key: str) -> str | None:
    """Translate a provider market key back to ours, or None if unrecognized."""
    return PROVIDER_TO_OUR_KEY.get(provider_key)
