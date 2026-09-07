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

  * `schedule` — 272 games for 2026 across 18 weeks, carrying the same
    `game_id` the stats and play-by-play files join on, plus kickoff date and
    time, stadium and the home/away pair. Its opener, `2026_01_NE_SEA` at
    2026-09-09 20:20 Eastern, is the same kickoff The Odds API reports as
    2026-09-10T00:20Z, which is the cross-check that settled the timezone.

  * `team_meta` / `team_seasons` — the 32 franchises: conference, division,
    full name, nickname and the two team colours the chips are drawn from.

TWO DIFFERENT HOSTS, AND THE SPLIT IS THEIRS NOT OURS. The per-season datasets
are GitHub *release assets* under `nflverse-data`; the schedule and the team
tables are plain files in the `nfldata` and `nflfastR-data` repositories, which
is where `nflreadr` itself reads them from. Hence full URL templates below
rather than a single base and a tag.

WHAT IS DELIBERATELY NOT READ FROM `team_meta`: every logo, wordmark and
conference-mark URL in it. CLAUDE.md §7 is explicit that real marks are
trademarked and that this project ships colour chips until the client licenses
otherwise. The columns are listed in `TEAM_META_LOGO_COLUMNS` so the exclusion
is a stated rule with a test behind it, rather than something that holds only
while nobody adds a convenient `logo` field.
"""

from __future__ import annotations

RELEASE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
NFLDATA_BASE = "https://raw.githubusercontent.com/nflverse/nfldata/master/data"
NFLFASTR_BASE = "https://raw.githubusercontent.com/nflverse/nflfastR-data/master"

#: Asset name -> full URL template. `{season}` is substituted where present.
ASSETS: dict[str, str] = {
    "weekly_stats": f"{RELEASE_BASE}/stats_player/stats_player_week_{{season}}.csv",
    "roster": f"{RELEASE_BASE}/rosters/roster_{{season}}.csv",
    "play_by_play": f"{RELEASE_BASE}/pbp/play_by_play_{{season}}.csv",
    "snap_counts": f"{RELEASE_BASE}/snap_counts/snap_counts_{{season}}.csv",
    "players": f"{RELEASE_BASE}/players/players.csv",
    "schedule": f"{NFLDATA_BASE}/games.csv",
    "team_seasons": f"{NFLDATA_BASE}/teams.csv",
    "team_meta": f"{NFLFASTR_BASE}/teams_colors_logos.csv",
}

#: Assets whose URL carries the season, so one file is one season.
SEASON_IN_URL: frozenset[str] = frozenset(
    {"weekly_stats", "roster", "play_by_play", "snap_counts"}
)

#: Assets whose contents must include the season asked for.
#:
#: DELIBERATELY NOT THE SAME SET AS `SEASON_IN_URL`. `schedule` and
#: `team_seasons` are single all-seasons files, so their URL cannot be wrong —
#: but they are living files in a git repository, updated as a season is
#: announced, and asking them for a season they do not yet carry is exactly the
#: stale read this guard exists for. Coupling the two ideas would have left the
#: schedule, the most volatile file here, unchecked.
REQUIRE_SEASON_PRESENT: frozenset[str] = frozenset(
    {"weekly_stats", "roster", "play_by_play", "schedule", "team_seasons",
     "snap_counts"}
)

#: Columns in `team_meta` that must never be ingested — CLAUDE.md §7.
TEAM_META_LOGO_COLUMNS: frozenset[str] = frozenset({
    "team_logo_wikipedia",
    "team_logo_espn",
    "team_wordmark",
    "team_conference_logo",
    "team_league_logo",
    "team_logo_squared",
})


def asset_url(asset: str, season: int | None = None) -> str:
    """The download URL for one asset.

    Raises KeyError for an unknown asset rather than building a URL that would
    404 — a typo should fail here, not as an empty ingest three steps later.
    """
    template = ASSETS[asset]
    if asset not in SEASON_IN_URL:
        return template
    if season is None:
        raise ValueError(f"{asset!r} is one file per season; a season is required")
    return template.format(season=season)
