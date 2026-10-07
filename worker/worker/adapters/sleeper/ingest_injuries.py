"""NFL injury designations from Sleeper -> `player_injuries` (migration 0086).

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). Each run reads Sleeper's player dump
once, keeps every active player on a team who carries a designation, and
writes them as each team's injury report for its next game's week
(core/team_weeks.py), replacing that week until the game kicks off.

EVERY OPEN TEAM-WEEK IS REPLACED, INCLUDING WITH NOTHING. Unlike a depth
chart, an empty injury report is a real answer: a team whose last
Questionable player was cleared must lose that row. The guard against a
broken response is therefore on the response as a whole (`MIN_ACTIVE`,
`MIN_DESIGNATED`), not per team.

RESOLVING THE PLAYER: SLEEPER'S OWN GSIS ID IS ON ONE ROW IN FIVE. Measured
2026-10-07: 564 of 2,761 active players carry `gsis_id`. The rest go through
nflverse's roster, which records Sleeper's id and ESPN's beside the GSIS id,
then by name within the team as the last resort, and only when that name is
unique on the team. Resolved this way: 573 of 581 designated players, 188 of
190 at QB/RB/WR/TE, and no GSIS id claimed twice. An unresolved row is kept
with its name and a NULL player_id.

A COACH'S DECISION IS NOT AN INJURY, AND IT IS LAST GAME'S. Sleeper keeps a
game's healthy scratches as "Out, Coach's Decision" until the next week's
report replaces them: 116 of the 220 "Out" rows on 2026-10-07, two days after
those games. Written as this week's report they would read as half the
league's injuries, so they are skipped and counted (`NOT_INJURIES`).

SLEEPER SPELLS TWO TEAMS ITS OWN WAY. The Rams are `LAR` (nflverse `LA`), and
a few stale records still say `OAK`; the second is skipped and counted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl

from worker.adapters.nflverse.client import NflverseClient
from worker.adapters.nflverse.mapping import SPORT, text_or_none
from worker.adapters.sleeper.client import SleeperClient, SleeperError
from worker.core.name_match import normalize, strip_suffix
from worker.core.team_weeks import open_team_weeks, replace_team_weeks
from worker.db import fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

TABLE = "player_injuries"
SOURCE = "sleeper"

#: Sleeper team code -> nflverse's (teams.nfl_abbr).
TEAM_ALIASES: dict[str, str] = {"LAR": "LA"}

#: Designations that are not injuries. Matched on the body-part field, which is
#: where Sleeper puts them.
NOT_INJURIES: frozenset[str] = frozenset({"Coach's Decision"})

#: Sanity floors on the whole response. In season there are ~2,700 active
#: players on teams and ~580 designated (IR included); a response far under
#: either has lost a field or a page, and would clear every team's report.
MIN_ACTIVE = 1500
MIN_DESIGNATED = 100

#: In season, Sleeper updates someone's news every few hours. A dump whose
#: newest update is older than this has stopped moving.
MAX_NEWS_AGE = timedelta(days=4)


def _name_key(name: str | None) -> str:
    return " ".join(strip_suffix(normalize(name).split()))


@dataclass
class RosterBridge:
    """nflverse roster ids that lead from a Sleeper record to a GSIS id."""

    by_sleeper: dict[str, str]
    by_espn: dict[str, str]
    by_name: dict[tuple[str, str], str]  # (name key, nfl_abbr) -> gsis, unique only

    @classmethod
    def from_roster(cls, roster: pl.DataFrame) -> RosterBridge:
        def pairs(column: str) -> dict[str, str]:
            out: dict[str, str] = {}
            for r in roster.select(column, "gsis_id").drop_nulls().to_dicts():
                out[str(r[column]).split(".")[0]] = r["gsis_id"]
            return out

        names: dict[tuple[str, str], str | None] = {}
        for r in roster.select("full_name", "team", "gsis_id").drop_nulls().to_dicts():
            key = (_name_key(r["full_name"]), r["team"])
            # Two players with one name on one team: neither is guessed.
            names[key] = r["gsis_id"] if key not in names else None
        return cls(
            by_sleeper=pairs("sleeper_id"),
            by_espn=pairs("espn_id"),
            by_name={k: v for k, v in names.items() if v is not None},
        )

    def gsis(self, sleeper_id: str, record: Mapping[str, Any], team: str) -> str | None:
        own = text_or_none(record.get("gsis_id"))
        if own:
            return own
        if sleeper_id in self.by_sleeper:
            return self.by_sleeper[sleeper_id]
        espn = record.get("espn_id")
        if espn is not None and str(espn) in self.by_espn:
            return self.by_espn[str(espn)]
        return self.by_name.get((_name_key(record.get("full_name")), team))


def check_response(players: Mapping[str, Mapping[str, Any]], now: datetime) -> None:
    active = [p for p in players.values() if p.get("active") and p.get("team")]
    designated = [p for p in active if p.get("injury_status")]
    if len(active) < MIN_ACTIVE or len(designated) < MIN_DESIGNATED:
        raise SleeperError(
            f"sleeper: {len(active)} active player(s), {len(designated)} designated; "
            f"expected at least {MIN_ACTIVE} and {MIN_DESIGNATED}. Not writing a "
            "response that would clear every team's injury report."
        )
    newest = max((p.get("news_updated") or 0) for p in active)
    age = now - datetime.fromtimestamp(newest / 1000, UTC)
    if age > MAX_NEWS_AGE:
        raise SleeperError(f"sleeper: newest player update is {age} old; the dump is stale")


def build_rows(
    players: Mapping[str, Mapping[str, Any]],
    season: int,
    team_weeks: Mapping[int, int],
    team_ids: Mapping[str, int],
    player_ids: Mapping[str, int],
    bridge: RosterBridge,
    counts: Any,
) -> list[dict[str, Any]]:
    rows = []
    for sleeper_id, p in players.items():
        status = text_or_none(p.get("injury_status"))
        if not status or not p.get("active") or not p.get("team"):
            continue
        if p.get("position") == "DEF":  # Sleeper's team-defense pseudo-player
            continue
        if p.get("injury_body_part") in NOT_INJURIES:
            counts.skip("injuries: coach's decision (last game's scratch, not an injury)")
            continue
        team = TEAM_ALIASES.get(p["team"], p["team"])
        team_id = team_ids.get(team)
        if team_id is None:
            counts.skip("injuries: team not resolved")
            continue
        week = team_weeks.get(team_id)
        if week is None:
            counts.skip("injuries: team has no game left")
            continue
        gsis = bridge.gsis(sleeper_id, p, team)
        player_id = player_ids.get(gsis) if gsis else None
        if player_id is None:
            counts.skip("injuries: player not resolved (kept by name)")
        name = text_or_none(p.get("full_name")) or " ".join(
            x for x in (p.get("first_name"), p.get("last_name")) if x
        )
        news = p.get("news_updated")
        rows.append({
            "season": season,
            "week": week,
            "team_id": team_id,
            "source": SOURCE,
            "source_player_id": str(sleeper_id),
            "player_id": player_id,
            "player_name": name,
            "position": text_or_none(p.get("position")),
            "status": status,
            "body_part": text_or_none(p.get("injury_body_part")),
            "notes": text_or_none(p.get("injury_notes")),
            "source_updated_at": datetime.fromtimestamp(news / 1000, UTC) if news else None,
        })
    return rows


def run_nfl_injury_ingest(
    sleeper: SleeperClient,
    nflverse: NflverseClient,
    season: int,
    counts: Any,
    *,
    roster_max_age: float | None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> int:
    now = now or datetime.now(UTC)
    team_weeks = open_team_weeks(SPORT, season, now)
    if not team_weeks:
        log.info("injuries %d: no team has a game left; nothing to write", season)
        return 0

    players = sleeper.players()
    check_response(players, now)
    bridge = RosterBridge.from_roster(nflverse.fetch("roster", season, max_age=roster_max_age))
    team_ids = {
        r["nfl_abbr"]: int(r["id"])
        for r in fetch_all("select id, nfl_abbr from teams where sport = %s", (SPORT,))
    }
    player_ids = {
        r["gsis_id"]: int(r["id"])
        for r in fetch_all(
            "select id, gsis_id from players where sport = %s and gsis_id is not null",
            (SPORT,),
        )
    }
    rows = build_rows(players, season, team_weeks, team_ids, player_ids, bridge, counts)
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    log.info(
        "injuries %d: %d designated player(s) across %d open team-week(s): %s",
        season, len(rows), len(team_weeks),
        ", ".join(f"{k} {v}" for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])),
    )
    if dry_run:
        return 0
    written = replace_team_weeks(TABLE, season, team_weeks, rows)
    counts.add(TABLE, written)
    return written


__all__ = [
    "RosterBridge",
    "build_rows",
    "check_response",
    "run_nfl_injury_ingest",
]
