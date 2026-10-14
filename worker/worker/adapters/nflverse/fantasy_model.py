"""Inputs of the client's NFL fantasy game model (migration 0093, phase F1).

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). Three steps, each its own function so
the job can run them separately:

  1. `build_team_inputs` — fantasy_team_inputs for every week of a season,
     from nflverse play-by-play (rates, pace, EPA) and our final scores
     (Massey, SRS). The math is core/fantasy_inputs.py.
  2. `capture_projections` — CBS's projections for each team's next game
     (adapters/cbs/projections.py).
  3. `build_lineups` — each team's five starters for its next game, from the
     captured projections, the depth chart and the injury report
     (core/fantasy_lineup.py). Run after 2, and after the day's depth chart
     and injury ingests.

WHY THE PLAY-BY-PLAY FILE AND NOT OUR `plays` TABLE. `plays` keeps only snaps
with a passer, rusher or receiver and has no drive number
(nflverse/ingest_plays.py), so red-zone TRIPS cannot be counted from it. The
nflverse file has the drive and its result; it is the same download the NFL
results chain already makes, and it costs no quota.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import polars as pl

from worker.adapters.cbs.client import CbsClient
from worker.adapters.cbs.projections import build_rows as projection_rows
from worker.adapters.cbs.projections import fetch_weeks, name_key
from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.mapping import SPORT, canonical_team
from worker.adapters.sleeper.ingest_injuries import RosterBridge
from worker.core.fantasy_inputs import FinalScore, inputs_as_of, round_row
from worker.core.fantasy_lineup import DepthQB, Projected, pick_lineup
from worker.core.team_weeks import open_team_weeks, replace_team_weeks
from worker.db import execute, fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

INPUTS_TABLE = "fantasy_team_inputs"
PROJECTIONS_TABLE = "fantasy_projections"
LINEUPS_TABLE = "fantasy_lineups"

#: Last regular-season week. Inputs are written for as_of_week up to one past
#: it, so week 18's game has a row; postseason is out of the sheet's scope.
LAST_REGULAR_WEEK = 18


def _team_ids() -> dict[str, int]:
    return {
        r["nfl_abbr"]: int(r["id"])
        for r in fetch_all("select id, nfl_abbr from teams where sport = %s", (SPORT,))
    }


# -----------------------------------------------------------------------------
# 1. team inputs
# -----------------------------------------------------------------------------
def normalize_plays(frame: pl.DataFrame, team_ids: Mapping[str, int]) -> pl.DataFrame:
    """The nflverse play-by-play as core/fantasy_inputs.py's PLAY_COLUMNS frame."""
    needed = ("game_id", "week", "season_type", "posteam", "defteam", "down", "epa",
              "play_type", "two_point_attempt", "third_down_converted",
              "fixed_drive", "fixed_drive_result", "yardline_100")
    missing = [c for c in needed if c not in frame.columns]
    if missing:
        raise NflverseError(f"play_by_play is missing column(s) {missing}")

    snaps = frame.filter(
        (pl.col("season_type") == "REG")
        & pl.col("play_type").is_in(["pass", "run"])
        & (pl.col("two_point_attempt").cast(pl.Float64).fill_null(0) == 0)
        & pl.col("posteam").is_not_null()
    )
    abbrs = set(snaps["posteam"].drop_nulls().unique())
    abbrs |= set(snaps["defteam"].drop_nulls().unique())
    ids = {a: team_ids.get(canonical_team(a) or "") for a in abbrs}

    def team_id(column: str) -> pl.Expr:
        return pl.col(column).replace_strict(ids, default=None, return_dtype=pl.Int64)

    return snaps.select(
        pl.col("game_id").alias("game_key"),
        pl.col("week").cast(pl.Int64),
        team_id("posteam").alias("offense_id"),
        team_id("defteam").alias("defense_id"),
        pl.col("down").cast(pl.Int64),
        pl.col("epa").cast(pl.Float64),
        (pl.col("third_down_converted").cast(pl.Float64).fill_null(0) == 1)
        .alias("third_down_converted"),
        pl.col("fixed_drive").cast(pl.Int64).alias("drive"),
        (pl.col("fixed_drive_result") == "Touchdown").fill_null(False).alias("drive_touchdown"),
        pl.col("yardline_100").cast(pl.Int64),
    ).drop_nulls(["offense_id", "defense_id"])


def load_final_scores(season: int) -> list[FinalScore]:
    rows = fetch_all(
        """
        select week, home_team_id, away_team_id, home_points, away_points, neutral_site
          from games
         where sport = %s and season = %s and completed
           and home_points is not null and away_points is not null
           and week <= %s
        """,
        (SPORT, season, LAST_REGULAR_WEEK),
    )
    return [
        FinalScore(
            week=int(r["week"]), home_id=int(r["home_team_id"]), away_id=int(r["away_team_id"]),
            home_points=int(r["home_points"]), away_points=int(r["away_points"]),
            neutral=bool(r["neutral_site"]),
        )
        for r in rows
    ]


def build_team_inputs(
    client: NflverseClient, season: int, *, max_age: float | None, dry_run: bool = False
) -> list[dict[str, Any]]:
    """Rebuild every week of one season's fantasy_team_inputs. Returns the rows."""
    team_ids = _team_ids()
    games = load_final_scores(season)
    if not games:
        log.warning("fantasy inputs %d: no completed games", season)
        return []
    plays = normalize_plays(client.fetch("play_by_play", season, max_age=max_age), team_ids)
    last_week = min(max(g.week for g in games), LAST_REGULAR_WEEK)

    rows: list[dict[str, Any]] = []
    for as_of_week in range(2, last_week + 2):
        rows.extend(round_row(r) for r in inputs_as_of(
            games, plays, team_ids.values(), season, as_of_week
        ))
    pbp_last = int(plays["week"].max()) if not plays.is_empty() else 0
    log.info(
        "fantasy inputs %d: %d games through week %d, play-by-play through week %d, "
        "%d rows for weeks 2-%d",
        season, len(games), last_week, pbp_last, len(rows), last_week + 1,
    )
    if dry_run:
        return rows
    execute(
        f"""
        delete from {INPUTS_TABLE} r
         using teams t
         where t.id = r.team_id and t.sport = %s and r.season = %s
        """,
        (SPORT, season),
    )
    upsert(INPUTS_TABLE, rows, conflict_columns=["team_id", "season", "as_of_week"])
    return rows


# -----------------------------------------------------------------------------
# 2. CBS projections
# -----------------------------------------------------------------------------
def player_resolver(
    client: NflverseClient, season: int, *, max_age: float | None
) -> dict[tuple[str, str], int]:
    """(name key, nfl_abbr) -> our player id, through nflverse's roster (unique names only)."""
    bridge = RosterBridge.from_roster(client.fetch("roster", season, max_age=max_age))
    by_gsis = {
        r["gsis_id"]: int(r["id"])
        for r in fetch_all(
            "select id, gsis_id from players where sport = %s and gsis_id is not null", (SPORT,)
        )
    }
    return {key: by_gsis[g] for key, g in bridge.by_name.items() if g in by_gsis}


def capture_projections(
    cbs: CbsClient,
    nflverse: NflverseClient,
    season: int,
    counts: Any,
    *,
    max_age: float | None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Capture CBS projections into every team's open week. Returns the rows."""
    now = now or datetime.now(UTC)
    team_weeks = open_team_weeks(SPORT, season, now)
    if not team_weeks:
        log.info("fantasy projections %d: no team has a game left", season)
        return []
    pages = fetch_weeks(cbs, season, team_weeks.values(), counts)
    rows = projection_rows(
        pages, season, team_weeks, _team_ids(),
        player_resolver(nflverse, season, max_age=max_age), now, counts,
    )
    covered = {r["team_id"]: r["week"] for r in rows}
    resolved = sum(1 for r in rows if r["player_id"] is not None)
    log.info(
        "fantasy projections %d: weeks %s, %d row(s) for %d of %d team(s), %d resolved",
        season, sorted(set(team_weeks.values())), len(rows), len(covered), len(team_weeks),
        resolved,
    )
    if not dry_run:
        counts.add(PROJECTIONS_TABLE, replace_team_weeks(PROJECTIONS_TABLE, season, covered, rows))
    return rows


# -----------------------------------------------------------------------------
# 3. lineups
# -----------------------------------------------------------------------------
def _injury_lookup(injuries: list[Mapping[str, Any]]):
    """(player_id, name) -> designation, by id first and by name for unresolved rows."""
    by_id = {i["player_id"]: i["status"] for i in injuries if i["player_id"] is not None}
    by_name = {name_key(i["player_name"]): i["status"] for i in injuries}

    def status_of(player_id: int | None, name: str) -> str | None:
        if player_id is not None and player_id in by_id:
            return by_id[player_id]
        return by_name.get(name_key(name))

    return status_of


def lineup_rows(
    season: int,
    team_weeks: Mapping[int, int],
    projections: list[Mapping[str, Any]],
    depth: list[Mapping[str, Any]],
    injuries: list[Mapping[str, Any]],
    at: datetime,
) -> list[dict[str, Any]]:
    """fantasy_lineups rows for every team-week that has projections."""
    rows: list[dict[str, Any]] = []
    for team_id, week in sorted(team_weeks.items()):
        pool = [
            Projected(p["player_id"], p["player_name"], p["position"], float(p["fantasy_points"]))
            for p in projections if p["team_id"] == team_id and p["week"] == week
        ]
        if not pool:
            continue
        qbs = [
            DepthQB(d["player_id"], d["player_name"], int(d["depth"]))
            for d in depth if d["team_id"] == team_id and d["week"] == week
        ]
        team_injuries = [i for i in injuries if i["team_id"] == team_id and i["week"] == week]
        status_of = _injury_lookup(team_injuries)
        for slot in pick_lineup(pool, qbs, status_of, name_key):
            rows.append({
                "season": season,
                "week": week,
                "team_id": team_id,
                "slot": slot.slot,
                "player_id": slot.player_id,
                "player_name": slot.player_name,
                "fantasy_points": round(slot.fantasy_points, 1),
                "points_source": slot.points_source,
                "picked_by": slot.picked_by,
                "skipped": slot.skipped,
                "source_as_of": at,
            })
    return rows


def build_lineups(
    season: int,
    counts: Any,
    *,
    now: datetime | None = None,
    projections: list[Mapping[str, Any]] | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Each team's five starters for its next game. Returns the rows.

    `projections` lets a dry run hand over the rows it did not write.
    """
    now = now or datetime.now(UTC)
    team_weeks = open_team_weeks(SPORT, season, now)
    if not team_weeks:
        return []
    teams = list(team_weeks)
    params = {"season": season, "teams": teams, "weeks": [team_weeks[t] for t in teams]}
    open_pairs = """
        (select unnest(%(teams)s::bigint[]) as team_id, unnest(%(weeks)s::smallint[]) as week) o
    """
    if projections is None:
        projections = fetch_all(
            f"""
            select p.team_id, p.week, p.player_id, p.player_name, p.position, p.fantasy_points
              from {PROJECTIONS_TABLE} p join {open_pairs}
                on o.team_id = p.team_id and o.week = p.week
             where p.season = %(season)s
            """,
            params,
        )
    depth = fetch_all(
        f"""
        select d.team_id, d.week, d.player_id, d.player_name, d.depth
          from depth_charts d join {open_pairs}
            on o.team_id = d.team_id and o.week = d.week
         where d.season = %(season)s and d.position = 'QB'
        """,
        params,
    )
    injuries = fetch_all(
        f"""
        select i.team_id, i.week, i.player_id, i.player_name, i.status
          from player_injuries i join {open_pairs}
            on o.team_id = i.team_id and o.week = i.week
         where i.season = %(season)s
        """,
        params,
    )
    rows = lineup_rows(season, team_weeks, projections, depth, injuries, now)
    covered = {r["team_id"]: r["week"] for r in rows}
    defaults = sum(1 for r in rows if r["points_source"] == "default")
    log.info(
        "fantasy lineups %d: %d team(s), %d starter(s), %d on default points, "
        "%d team(s) with no depth-chart QB",
        season, len(covered), len(rows), defaults,
        len(covered) - len({d["team_id"] for d in depth} & set(covered)),
    )
    if not dry_run:
        counts.add(LINEUPS_TABLE, replace_team_weeks(LINEUPS_TABLE, season, covered, rows))
    return rows


__all__ = [
    "LAST_REGULAR_WEEK",
    "build_lineups",
    "build_team_inputs",
    "capture_projections",
    "lineup_rows",
    "load_final_scores",
    "normalize_plays",
    "player_resolver",
]
