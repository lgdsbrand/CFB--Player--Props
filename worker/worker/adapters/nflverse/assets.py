"""Which nflverse release assets this project reads, and what is in them.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The mirror image of
`worker/adapters/cfbd/`: everything nflverse-shaped belongs under this package,
and `worker/core/` stays untouched.

nflverse publishes as GitHub release assets — one file per season, per dataset,
under a release tag. There is no API, no key, no quota and no rate limit, which
is why this package has no `quota.py` and no pacing: the three things that stand
between a CFBD backfill and a suspended key have no counterpart here. What it
does have is a staleness problem of its own, described below.

---------------------------------------------------------------------------
THE TRAP THAT COST AN HOUR ON 2026-09-05, RECORDED SO IT COSTS NOBODY ELSE ONE
---------------------------------------------------------------------------
nflverse has BOTH a legacy and a current asset for weekly player stats, and the
legacy one still resolves with a 200:

    player_stats/player_stats.csv        -> 200, 134,470 rows, ENDS AT 2024
    stats_player/stats_player_week_2025  -> 200, 19,422 rows, all 22 weeks

The legacy file is frozen, not empty. Reading it in 2026 yields a complete,
well-formed, entirely plausible dataset that is two seasons out of date, and
nothing about the response says so. A model built on it would train on 2024,
project 2026, and look fine.

`REQUIRE_SEASON_PRESENT` below is the guard: every seasonal read asserts the
season it asked for is actually in the file it got back. An asset that answers
200 with the wrong season is treated as a failure, not as data.

---------------------------------------------------------------------------
WHAT EACH ASSET IS FOR (measured 2026-09-05, not assumed)
---------------------------------------------------------------------------
  * `stats_player_week_{season}` — the box scores. Maps very nearly 1:1 onto
    `player_game_stats`: completions, attempts, passing_yards, passing_tds,
    carries, rushing_yards, rushing_tds, receptions, targets, receiving_yards,
    receiving_tds, plus position_group, opponent_team and a game_id. Every
    market in CLAUDE.md §6 is a column here.

  * `roster_{season}` — 2,946 rows and 32 teams for 2026, carrying
    `depth_chart_position` and `gsis_id`. This is the NFL equivalent of the
    roster blocker that held the college build up until 2026-08-08, and it is
    already published for the season about to start.

  * `play_by_play_{season}` — 372 columns, and the reason the NFL split engine
    is a fraction of the college one. It carries `rusher_player_id`,
    `receiver_player_id` and `passer_player_id` as EXPLICIT, PRE-RESOLVED
    columns, alongside `defteam`, `yards_gained` and `yardline_100`. The CFBD
    adapter had to derive all of that from a per-game `/plays/stats` fan-out
    with a silent 2,000-row cap, and then repair CFBD labelling the passer as
    the receiver on touchdown plays (`fix_swapped_pass_attribution`). None of
    that machinery has an equivalent here.

  * `players` — the master player table, for the handful of ids a roster misses.

The 2026 SCHEDULE is deliberately absent from this list. nflverse's schedule
asset is not where this project gets it: The Odds API already serves all 272
events of the 2026 season for zero credits, resolved to the same team names the
odds ingest matches on. Taking the schedule from the source that also prices it
removes a whole class of mismatch between our games and the provider's events.
"""

from __future__ import annotations

BASE_URL = "https://github.com/nflverse/nflverse-data/releases/download"

#: Release tag -> filename template. `{season}` is substituted where present.
ASSETS: dict[str, tuple[str, str]] = {
    "weekly_stats": ("stats_player", "stats_player_week_{season}.csv"),
    "roster": ("rosters", "roster_{season}.csv"),
    "play_by_play": ("pbp", "play_by_play_{season}.csv"),
    "players": ("players", "players.csv"),
}

#: Assets that are per-season and must contain the season they were asked for.
#: See the module docstring: the legacy weekly-stats asset answers 200 with data
#: that stops at 2024, and only this assertion tells the two apart.
REQUIRE_SEASON_PRESENT: frozenset[str] = frozenset(
    {"weekly_stats", "roster", "play_by_play"}
)

#: Assets with no season dimension at all.
SEASONLESS: frozenset[str] = frozenset({"players"})


def asset_url(asset: str, season: int | None = None) -> str:
    """The download URL for one release asset.

    Raises KeyError for an unknown asset rather than building a URL that would
    404 — a typo should fail here, not as an empty ingest three steps later.
    """
    tag, template = ASSETS[asset]
    if asset in SEASONLESS:
        return f"{BASE_URL}/{tag}/{template}"
    if season is None:
        raise ValueError(f"{asset!r} is per-season; a season is required")
    return f"{BASE_URL}/{tag}/{template.format(season=season)}"
