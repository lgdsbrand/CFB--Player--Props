"""AP, Coaches and CFP committee poll rankings from CFBD `/rankings`.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). `team_poll_rankings` itself is sport
agnostic; the NFL build has no equivalent poll and simply leaves it empty.

COSTS NO ODDS API CREDITS, and barely costs CFBD ones either: one call returns a
whole season.

POINT-IN-TIME WITHOUT AN OFFSET. CFBD's week N poll is the poll published
ENTERING week N, reflecting games through week N-1 — verified against 2025,
where every ranked team that lost in week 1 keeps its ranking in the week 1 poll
and drops in the week 2 poll (Texas #1 -> #7, Alabama #8 -> #21, Kansas State
#17 -> unranked). So the week is stored as given, and a join on it is already
free of lookahead. Migration 0032 records the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cfbd

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.mapping import (
    bigint_or_none,
    normalize_season_type,
    smallint_or_none,
    week_on_season_axis,
)
from worker.db import fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

IMMUTABLE = None

# The FBS polls. /rankings returns these alongside "FCS Coaches Poll", "AFCA
# Division II Coaches Poll" and "AFCA Division III Coaches Poll" — storing those
# would put a Division III school's #1 beside an FBS team's, and nothing
# downstream distinguishes them.
#
# A SET RATHER THAN A "NOT FCS" TEST, deliberately. An allow-list fails closed:
# a poll CFBD adds later is skipped and logged rather than silently ingested
# into a filter that claims to mean "Top 25 of college football".
FBS_POLLS: frozenset[str] = frozenset(
    {"AP Top 25", "Coaches Poll", "Playoff Committee Rankings"}
)


@dataclass
class RankingCounts:
    rows: int = 0
    poll_weeks: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    polls_seen: set[str] = field(default_factory=set)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _team_id_by_cfbd_id() -> dict[int, int]:
    return {
        r["cfbd_id"]: r["id"]
        for r in fetch_all("select id, cfbd_id from teams where cfbd_id is not null")
    }


def _team_id_by_school() -> dict[str, int]:
    return {r["school"]: r["id"] for r in fetch_all("select id, school from teams")}


def ingest_poll_rankings(
    client: CfbdClient,
    season: int,
    *,
    max_age: float | None = IMMUTABLE,
) -> RankingCounts:
    """Upsert every FBS poll ranking for a season. One API call per season type."""
    counts = RankingCounts()

    by_cfbd_id = _team_id_by_cfbd_id()
    by_school = _team_id_by_school()
    if not by_school:
        log.warning("rankings %d: no teams ingested, nothing to match", season)
        return counts

    payload: dict[tuple[int, int, str, int], dict[str, Any]] = {}

    for season_type in ("regular", "postseason"):
        rows = client.fetch(
            "/rankings",
            cfbd.RankingsApi,
            "get_rankings",
            year=season,
            season_type=season_type,
            max_age=max_age,
        )

        for entry in rows:
            raw_week = smallint_or_none(entry.get("week"))
            if raw_week is None:
                counts.skip("poll week with no week number")
                continue

            # Offset postseason weeks onto the monotone season axis, the same
            # transform every other per-week table uses. Without it a bowl-week
            # poll would collide with a September one and a backtest would read
            # January's rankings into week 1 — the exact shape of the postseason
            # week collision found in Phase 4.
            week = week_on_season_axis(
                raw_week, normalize_season_type(entry.get("seasonType") or season_type)
            )

            for poll in entry.get("polls") or []:
                name = poll.get("poll")
                if not name:
                    counts.skip("poll with no name")
                    continue
                counts.polls_seen.add(name)
                if name not in FBS_POLLS:
                    counts.skip(f"non-FBS poll: {name}")
                    continue

                counts.poll_weeks += 1
                for rank in poll.get("ranks") or []:
                    position = smallint_or_none(rank.get("rank"))
                    if position is None or position < 1:
                        counts.skip("rank with no position")
                        continue

                    # Prefer the id; fall back to the school name. CFBD has
                    # served string ids where ints were documented (Phase 2), so
                    # the name is the safety net rather than the primary key.
                    team_id = by_cfbd_id.get(bigint_or_none(rank.get("teamId")) or -1)
                    if team_id is None:
                        team_id = by_school.get(rank.get("school") or "")
                    if team_id is None:
                        counts.skip("unknown team")
                        continue

                    payload[(season, week, name, team_id)] = {
                        "team_id": team_id,
                        "season": season,
                        "week": week,
                        "poll": name,
                        "rank": position,
                        "first_place_votes": smallint_or_none(
                            rank.get("firstPlaceVotes")
                        ),
                        "points": rank.get("points"),
                    }

    n = upsert(
        "team_poll_rankings",
        list(payload.values()),
        conflict_columns=["season", "week", "poll", "team_id"],
    )
    counts.rows = n

    log.info(
        "rankings %d: %d rows across %d poll-weeks; polls seen %s; skipped %s",
        season,
        n,
        counts.poll_weeks,
        sorted(counts.polls_seen) or "none",
        counts.skipped or "nothing",
    )
    return counts


def estimate_calls(season: int) -> int:
    """Two: one per season type."""
    return 2
