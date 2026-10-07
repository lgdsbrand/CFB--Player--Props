"""Per-team, per-week snapshots that freeze at kickoff (migration 0086).

SPORT-AGNOSTIC CORE. Depth charts and injury reports both arrive as "the
state of things now", with no history a later run could re-read. They are
stored one set per team per week: each run writes every team's NEXT game's
week, replacing what an earlier run wrote for it, and a week whose game has
kicked off is never touched again because no run will name it.

That is how a past game's page shows what was known going into it (CLAUDE.md
§4) without a freeze trigger: the week a run writes is chosen by the clock,
and the clock only moves forward.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from worker.db import connect, fetch_all


def open_team_weeks(sport: str, season: int, now: datetime) -> dict[int, int]:
    """team_id -> week of that team's next game that has not kicked off.

    A team with no game left this season has no entry, so nothing is written
    for it. A team on a bye gets its following game's week.
    """
    rows = fetch_all(
        """
        select distinct on (team_id) team_id, week
          from (
            select home_team_id as team_id, week, start_date
              from games
             where sport = %(sport)s and season = %(season)s and start_date > %(now)s
            union all
            select away_team_id, week, start_date
              from games
             where sport = %(sport)s and season = %(season)s and start_date > %(now)s
          ) t
         order by team_id, start_date
        """,
        {"sport": sport, "season": season, "now": now},
    )
    return {int(r["team_id"]): int(r["week"]) for r in rows}


def replace_team_weeks(
    table: str,
    season: int,
    team_weeks: Mapping[int, int],
    rows: Sequence[Mapping[str, Any]],
) -> int:
    """Replace `table`'s rows for exactly these (team, week) pairs, in one transaction.

    Every row must belong to one of the pairs: a row for any other week would
    be a write into a frozen set, and is refused rather than filtered. A pair
    with no rows is cleared, which is right for injuries (a team with no one
    designated) and is why the depth-chart caller passes only the teams the
    source actually covered.
    """
    for row in rows:
        if team_weeks.get(row["team_id"]) != row["week"] or row["season"] != season:
            raise ValueError(
                f"{table}: row for team {row['team_id']} season {row['season']} "
                f"week {row['week']} is outside the open weeks being replaced"
            )
    if not team_weeks:
        return 0
    teams = list(team_weeks)
    weeks = [team_weeks[t] for t in teams]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            delete from {table} d
             using (select unnest(%s::bigint[]) as team_id,
                           unnest(%s::smallint[]) as week) o
             where d.season = %s and d.team_id = o.team_id and d.week = o.week
            """,
            (teams, weeks, season),
        )
        if rows:
            columns = list(rows[0])
            cur.executemany(
                f"insert into {table} ({', '.join(columns)}) "
                f"values ({', '.join(f'%({c})s' for c in columns)})",
                rows,
            )
        conn.commit()
    return len(rows)
