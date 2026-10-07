"""Head coaches and their season-by-season records from CFBD.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The three tables (migration 0084) are
sport-agnostic; the NFL build has no equivalent feed and leaves them empty.

Two calls shapes, both CFBD-only (no Odds API credits):

  /coaches?year=S         every head coach of season S — ONE call, ~138 rows
  /coaches/seasons?id=C   one coach's every season, with record splits

so a season costs one call plus one per head coach: ~139 a week.

SEASONS ARE STORED AS CFBD RECORDS THEM and summed on the site. CFBD's own
career and tenure totals may or may not include the season in progress
depending on when it last updated (on 2026-10-07 they did not), and the page
states records BEFORE this season. Summing completed seasons ourselves makes
that answer independent of CFBD's timing. See migration 0084.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cfbd

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.mapping import bigint_or_none, smallint_or_none
from worker.db import fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

# The splits CFBD carries per season, and the column prefix each lands in.
SPLITS: dict[str, str] = {
    "conference": "conf",
    "home": "home",
    "away": "away",
    "neutral": "neutral",
    "postseason": "post",
}


@dataclass
class CoachCounts:
    coaches: int = 0
    seasons: int = 0
    team_coaches: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def estimate_calls(coaches: int = 140) -> int:
    """One for the season's coach list, one per coach for their seasons."""
    return 1 + coaches


def _team_id_by_cfbd_id() -> dict[int, int]:
    return {
        r["cfbd_id"]: r["id"]
        for r in fetch_all(
            "select id, cfbd_id from teams where cfbd_id is not null and sport = 'cfb'"
        )
    }


def season_row(entry: dict[str, Any], team_ids: dict[int, int]) -> dict[str, Any] | None:
    """One `coach_seasons` row from a `/coaches/seasons` entry, or None if unusable."""
    coach = entry.get("coach") or {}
    team = entry.get("team") or {}
    coach_id = bigint_or_none(coach.get("id"))
    season = smallint_or_none(entry.get("year"))
    school = team.get("school")
    if coach_id is None or season is None or not school:
        return None
    cfbd_team = bigint_or_none(team.get("id"))
    row: dict[str, Any] = {
        "coach_id": coach_id,
        "season": season,
        "school": school,
        "team_id": team_ids.get(cfbd_team) if cfbd_team is not None else None,
        "games": smallint_or_none(entry.get("games")) or 0,
        "wins": smallint_or_none(entry.get("wins")) or 0,
        "losses": smallint_or_none(entry.get("losses")) or 0,
        "ties": smallint_or_none(entry.get("ties")) or 0,
    }
    # NULL, not 0, where CFBD has not attributed the season: "not recorded"
    # must not read as "went 0-0 at home".
    splits = entry.get("recordSplits") or {}
    for key, prefix in SPLITS.items():
        split = splits.get(key)
        row[f"{prefix}_wins"] = smallint_or_none(split.get("wins")) if split else None
        row[f"{prefix}_losses"] = smallint_or_none(split.get("losses")) if split else None
    return row


def ingest_coaches(
    client: CfbdClient, season: int, *, max_age: float | None = None
) -> CoachCounts:
    """Upsert every head coach of `season`, their seasons, and who coaches whom."""
    counts = CoachCounts()
    team_ids = _team_id_by_cfbd_id()
    if not team_ids:
        log.warning("coaches %d: no teams ingested, nothing to match", season)
        return counts

    listing = client.fetch(
        "/coaches", cfbd.CoachesApi, "get_coaches", year=season, max_age=max_age
    )

    coaches: dict[int, dict[str, Any]] = {}
    team_coaches: dict[tuple[int, int, int], dict[str, Any]] = {}
    for entry in listing:
        coach_id = bigint_or_none(entry.get("id"))
        if coach_id is None:
            counts.skip("coach with no id")
            continue
        coaches[coach_id] = {
            "id": coach_id,
            "first_name": entry.get("firstName") or "",
            "last_name": entry.get("lastName") or "",
        }
        hire = entry.get("hireDate")
        for stint in entry.get("seasons") or []:
            team_id = team_ids.get(bigint_or_none(stint.get("teamId")) or -1)
            if team_id is None:
                counts.skip("coach of a team we do not hold")
                continue
            team_coaches[(team_id, season, coach_id)] = {
                "team_id": team_id,
                "season": season,
                "coach_id": coach_id,
                "hire_date": str(hire)[:10] if hire else None,
            }

    seasons: dict[tuple[int, int, str], dict[str, Any]] = {}
    for coach_id in coaches:
        for entry in client.fetch(
            "/coaches/seasons",
            cfbd.CoachesApi,
            "get_coach_seasons",
            coach_id=coach_id,
            max_age=max_age,
        ):
            row = season_row(entry, team_ids)
            if row is None:
                counts.skip("season with no coach, year or school")
                continue
            seasons[(row["coach_id"], row["season"], row["school"])] = row

    # Parents first: coach_seasons and team_coaches both reference coaches.
    counts.coaches = upsert("coaches", list(coaches.values()), conflict_columns=["id"])
    counts.seasons = upsert(
        "coach_seasons",
        list(seasons.values()),
        conflict_columns=["coach_id", "season", "school"],
    )
    counts.team_coaches = upsert(
        "team_coaches",
        list(team_coaches.values()),
        conflict_columns=["team_id", "season", "coach_id"],
    )
    log.info(
        "coaches %d: %d coaches, %d coach-seasons, %d team-coach rows; skipped %s",
        season,
        counts.coaches,
        counts.seasons,
        counts.team_coaches,
        counts.skipped or "nothing",
    )
    return counts
