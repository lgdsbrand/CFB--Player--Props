"""NFL depth charts -> `depth_charts` (migration 0086).

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). nflverse republishes ESPN's depth
charts twice a day in one season-long file; this takes the NEWEST snapshot
and writes it as each team's depth chart for its next game's week
(core/team_weeks.py), replacing that week's set until the game kicks off.

A STALE FILE IS A FAILURE, NOT DATA. The file has no season column, so the
frozen-legacy guard in assets.py cannot see it; what it does have is the
snapshot time `dt`. If the newest snapshot is older than
`MAX_SNAPSHOT_AGE`, the job fails rather than stamping a week-old depth chart
onto this week. Measured 2026-10-07: two snapshots a day, the newest under
16 hours old.

ONE EXACT JOIN. `gsis_id` is `players.gsis_id`; measured 2,282 of 2,288 rows
resolve, every QB/RB/WR/TE in the top three. A row that does not resolve is
kept with its name and a NULL player_id, never dropped: a missing row on a
depth chart reads as a missing player.

ONLY TEAMS THE SNAPSHOT COVERS ARE REPLACED. If ESPN dropped a team for one
snapshot, clearing that team's week would print an empty depth chart; keeping
the previous run's set is the honest fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl

from worker.adapters.nflverse.client import NflverseClient, NflverseError
from worker.adapters.nflverse.mapping import SPORT, text_or_none
from worker.core.team_weeks import open_team_weeks, replace_team_weeks
from worker.db import fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

TABLE = "depth_charts"

#: Two snapshots a day upstream; two days without one means the publishing job
#: has stopped, and a depth chart that old is no longer this week's.
MAX_SNAPSHOT_AGE = timedelta(hours=48)


def snapshot_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def latest_snapshot(frame: pl.DataFrame, now: datetime) -> tuple[pl.DataFrame, datetime]:
    """The newest snapshot's rows and its time; raises if it is stale."""
    if frame.is_empty():
        raise NflverseError("depth_charts: the file has no rows")
    newest = frame["dt"].max()
    at = snapshot_time(newest)
    if now - at > MAX_SNAPSHOT_AGE:
        raise NflverseError(
            f"depth_charts: newest snapshot is {newest}, older than "
            f"{MAX_SNAPSHOT_AGE}; the upstream job has stopped publishing"
        )
    return frame.filter(pl.col("dt") == newest), at


def build_rows(
    snapshot: pl.DataFrame,
    at: datetime,
    season: int,
    team_weeks: dict[int, int],
    team_ids: dict[str, int],
    players: dict[str, int],
    counts: Any,
) -> list[dict[str, Any]]:
    """depth_charts rows for every team the snapshot covers that has a game left."""
    rows: dict[tuple, dict[str, Any]] = {}
    for r in snapshot.to_dicts():
        team_id = team_ids.get(r["team"])
        if team_id is None:
            counts.skip("depth: team not resolved")
            continue
        week = team_weeks.get(team_id)
        if week is None:
            counts.skip("depth: team has no game left")
            continue
        name = text_or_none(r.get("player_name"))
        position = text_or_none(r.get("pos_abb"))
        group = text_or_none(r.get("pos_grp"))
        if name is None or position is None or group is None:
            counts.skip("depth: blank name, position or group")
            continue
        gsis = text_or_none(r.get("gsis_id"))
        player_id = players.get(gsis) if gsis else None
        if player_id is None:
            counts.skip("depth: player not resolved (kept by name)")
        key = (team_id, group, int(r["pos_slot"]), int(r["pos_rank"]))
        rows[key] = {
            "season": season,
            "week": week,
            "team_id": team_id,
            "position_group": group,
            "slot": key[2],
            "depth": key[3],
            "position": position,
            "player_id": player_id,
            "player_name": name,
            "source_as_of": at,
        }
    return list(rows.values())


def run_nfl_depth_chart_ingest(
    client: NflverseClient,
    season: int,
    counts: Any,
    *,
    max_age: float | None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> int:
    now = now or datetime.now(UTC)
    frame = client.fetch("depth_charts", season, max_age=max_age)
    snapshot, at = latest_snapshot(frame, now)

    team_ids = {
        r["nfl_abbr"]: int(r["id"])
        for r in fetch_all("select id, nfl_abbr from teams where sport = %s", (SPORT,))
    }
    players = {
        r["gsis_id"]: int(r["id"])
        for r in fetch_all(
            "select id, gsis_id from players where sport = %s and gsis_id is not null",
            (SPORT,),
        )
    }
    team_weeks = open_team_weeks(SPORT, season, now)
    rows = build_rows(snapshot, at, season, team_weeks, team_ids, players, counts)
    covered = {r["team_id"]: r["week"] for r in rows}
    log.info(
        "depth charts %d: snapshot %s, %d row(s) for %d team(s) of %d with a game left",
        season, at.isoformat(), len(rows), len(covered), len(team_weeks),
    )
    if dry_run:
        return 0
    written = replace_team_weeks(TABLE, season, covered, rows)
    counts.add(TABLE, written)
    return written


__all__ = [
    "MAX_SNAPSHOT_AGE",
    "build_rows",
    "latest_snapshot",
    "run_nfl_depth_chart_ingest",
]
