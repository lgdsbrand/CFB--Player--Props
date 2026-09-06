"""N3 -- NFL box scores into `player_game_stats`.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The mirror of
`worker/adapters/cfbd/ingest_stats.py`, and much smaller than it, for reasons
worth stating rather than leaving as a pleasant surprise.

WHAT THE COLLEGE VERSION HAS TO DO THAT THIS DOES NOT.

  * **A per-game fan-out.** CFBD's `/plays/stats` truncates week-level responses
    at 2,000 rows *silently*, so the college adapter asks per game -- 888 calls
    a season, which is what forced the incremental daily load. nflverse ships
    one file per season.

  * **Derive `targets` from play attribution.** It is a column here.

  * **Repair swapped passer/receiver labels.** CFBD assigns 'Reception' to the
    PASSER on a subset of touchdown plays, so the college build carries
    `fix_swapped_pass_attribution`. nflverse's box score is already correct.

WHAT IS GENUINELY HARDER HERE: NOTHING IN THE MAPPING, ONE THING IN THE JOIN.
The file identifies a game by nflverse's `game_id` ('2025_01_PIT_NYJ') and a
player by `gsis_id`. Both are keys we now store (migration 0049), so the join is
exact rather than name-matched -- which removes the whole class of problem
`worker/core/name_match.py` exists to manage on the college side.

THE WEEK AND SEASON_TYPE COME FROM OUR `games` ROW, NOT FROM THIS FILE, AND
THAT IS DELIBERATE. The stats file carries its own `week` and `season_type`, and
they agree with ours today. Taking them from the game we are already joining to
means there is exactly one place in this project that decides where a game sits
on the season axis -- the schedule ingest, which the axis CHECK constraint
guards. Two independent sources for the same axis is how the college build
ended up with bowl games in week 1, leaking December results into every
"through week N" aggregation.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from worker.adapters.nflverse.client import NflverseClient
from worker.adapters.nflverse.mapping import (
    SPORT,
    PositionMapper,
    canonical_team,
    int_or_none,
    text_or_none,
)
from worker.db import fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

#: our `player_game_stats` column -> nflverse `stats_player_week` column.
#:
#: Every market in CLAUDE.md §6 is a straight column read. The two absences are
#: recorded below rather than silently left null.
STAT_COLUMNS: dict[str, str] = {
    "pass_attempts": "attempts",
    "pass_completions": "completions",
    "pass_yards": "passing_yards",
    "pass_tds": "passing_tds",
    "interceptions": "passing_interceptions",
    "rush_attempts": "carries",
    "rush_yards": "rushing_yards",
    "rush_tds": "rushing_tds",
    "targets": "targets",
    "receptions": "receptions",
    "rec_yards": "receiving_yards",
    "rec_tds": "receiving_tds",
}

#: Fumbles lost, summed across the three ways a skill player loses one. The
#: source splits them by how the fumble happened; our column does not care.
FUMBLE_COLUMNS = (
    "rushing_fumbles_lost",
    "receiving_fumbles_lost",
    "sack_fumbles_lost",
)

# `snaps` and `started` STAY NULL, and this is a note not an oversight. Neither
# is in this file; nflverse publishes snap counts as a separate release. They
# are used by the college usage filter, so when the NFL projection step needs a
# usage floor this is the first thing to add -- see N5. Leaving them null is
# honest; inventing them from `carries + targets` would make a usage filter that
# silently means something different per sport.


def _as_int(value: Any) -> int | None:
    """A stat cell as an int, treating blank and NA as absence.

    Absence matters and is not zero: a quarterback with no `targets` did not
    catch zero passes, he was never a receiver on any play. The distinction
    reaches the model as a null, which `_blend_rate` already treats as missing
    rather than as a measured zero.
    """
    return int_or_none(value)


def load_game_index(season: int) -> dict[str, dict[str, Any]]:
    """nflverse game_id -> the fields the stats rows need from our games row.

    Carries `week` and `season_type` so the caller never reads them from the
    stats file -- see the module docstring on why one source for the season axis
    matters.
    """
    rows = fetch_all(
        """
        select g.id, g.nflverse_id, g.week, g.season_type::text as season_type,
               g.home_team_id, g.away_team_id
          from games g
         where g.sport = %s and g.season = %s and g.nflverse_id is not null
        """,
        (SPORT, season),
    )
    return {r["nflverse_id"]: r for r in rows}


def load_player_index() -> dict[str, int]:
    """gsis_id -> our player id. Exact, not name-matched."""
    rows = fetch_all(
        "select id, gsis_id from players where sport = %s and gsis_id is not null",
        (SPORT,),
    )
    return {r["gsis_id"]: r["id"] for r in rows}


def load_team_index() -> dict[str, int]:
    rows = fetch_all(
        "select id, nfl_abbr from teams where sport = %s and nfl_abbr is not null",
        (SPORT,),
    )
    return {r["nfl_abbr"]: r["id"] for r in rows}


def build_rows(
    frame: pl.DataFrame,
    season: int,
    games: dict[str, dict[str, Any]],
    players: dict[str, int],
    teams: dict[str, int],
    counts: Any,
) -> list[dict[str, Any]]:
    """One `player_game_stats` payload row per (player, game) in the frame."""
    mapper = PositionMapper()
    payload: list[dict[str, Any]] = []

    for row in frame.to_dicts():
        game = games.get(text_or_none(row.get("game_id")) or "")
        if game is None:
            # A stat line for a game we did not ingest. Counted, because a large
            # number here means the schedule and the stats disagree about what
            # was played -- not that a few players were missed.
            counts.skip("stat: game not in database")
            continue

        player_id = players.get(text_or_none(row.get("player_id")) or "")
        if player_id is None:
            counts.skip("stat: player not on any roster")
            continue

        team_abbr = canonical_team(row.get("team"))
        opponent_abbr = canonical_team(row.get("opponent_team"))
        team_id = teams.get(team_abbr) if team_abbr else None
        opponent_id = teams.get(opponent_abbr) if opponent_abbr else None
        if team_id is None or opponent_id is None:
            counts.skip("stat: unresolved team")
            continue
        if team_id == opponent_id:
            # `player_game_stats_teams_differ` would reject the batch; catching
            # it here names the row instead of failing 19,000 of them.
            counts.skip("stat: team equals opponent")
            continue

        entry: dict[str, Any] = {
            "player_id": player_id,
            "game_id": game["id"],
            "team_id": team_id,
            "opponent_team_id": opponent_id,
            "season": season,
            # From the game, never from this file. See the module docstring.
            "week": game["week"],
            "position_group": mapper.group_for(row.get("position")),
            "is_home": team_id == game["home_team_id"],
        }
        for ours, theirs in STAT_COLUMNS.items():
            entry[ours] = _as_int(row.get(theirs))

        fumbles = [_as_int(row.get(c)) for c in FUMBLE_COLUMNS]
        entry["fumbles_lost"] = (
            sum(v for v in fumbles if v is not None)
            if any(v is not None for v in fumbles)
            else None
        )
        # Return touchdowns are a special-teams outcome and are excluded from
        # `offensive_tds` by the generated column, which is what the anytime-TD
        # market grades against. Stored for completeness, not used as a label.
        entry["return_tds"] = _as_int(row.get("special_teams_tds"))

        payload.append(entry)

    mapper.report()
    return payload


def run_nfl_stats_ingest(
    client: NflverseClient, season: int, counts: Any, *, max_age: float | None
) -> int:
    """Load one season of NFL box scores. Returns rows written."""
    frame = client.fetch("weekly_stats", season, max_age=max_age)

    games = load_game_index(season)
    if not games:
        raise ValueError(
            f"no NFL games stored for {season} -- run nfl_ingest_reference first"
        )
    players = load_player_index()
    teams = load_team_index()

    log.info(
        "stats %d: %d source row(s) against %d game(s), %d player(s)",
        season, frame.height, len(games), len(players),
    )

    payload = build_rows(frame, season, games, players, teams, counts)
    n = upsert(
        "player_game_stats", payload, conflict_columns=["player_id", "game_id"]
    )
    counts.add("player_game_stats", n)
    log.info("stats %d: %d rows", season, n)
    return n


__all__ = [
    "FUMBLE_COLUMNS",
    "STAT_COLUMNS",
    "build_rows",
    "load_game_index",
    "load_player_index",
    "load_team_index",
    "run_nfl_stats_ingest",
]
