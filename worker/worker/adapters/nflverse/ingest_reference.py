"""N2 -- conferences, teams, schedule and rosters for the NFL.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3). The mirror of
`worker/adapters/cfbd/ingest_reference.py`, and meant to be read beside it.

WHAT IS THE SAME. The table shapes and the write order: conferences before
teams, because a team names its conference; teams before games and rosters,
because both reference a team's surrogate id. Every write is an upsert keyed on
the external identifier, so a re-run is idempotent.

WHAT IS DIFFERENT, AND WHY.

  * **32 teams that never change, not 686 that do.** College realignment is why
    `team_seasons` is season-scoped and why conference membership is re-read
    every season. The NFL's divisions have been stable since 2002. The
    season-scoped table is still written -- the schema is shared and a division
    realignment is not impossible -- but it will say the same thing every year.

  * **No classification.** `team_classification` is the college enum
    (fbs/fcs/ii/iii/other) and no NFL row has an honest value in it. It is left
    NULL rather than set to 'other', which would read as "a division we do not
    display" instead of "this axis does not apply to this sport".

  * **Kickoff times are Eastern, and that is asserted rather than assumed.**
    The schedule carries `gameday` and `gametime` as local Eastern wall-clock,
    with no offset. Storing them as UTC without converting would put every
    Sunday 13:00 kickoff four or five hours early and, worse, would drift by an
    hour across the November DST boundary -- an error that is invisible in
    September and breaks the week-13 slate. `zoneinfo` does the conversion, and
    `test_the_opener_matches_the_odds_api` pins the result against the same
    kickoff read from a completely independent source.

  * **No logos, ever.** The team-metadata file carries six logo and wordmark
    URLs. CLAUDE.md §7: real marks are trademarked and this project ships
    colour chips until the client licenses otherwise. `TEAM_META_LOGO_COLUMNS`
    names them and a test asserts none reaches a payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import polars as pl

from worker.adapters.nflverse.assets import TEAM_META_LOGO_COLUMNS
from worker.adapters.nflverse.client import NflverseClient
from worker.adapters.nflverse.mapping import (
    IMMUTABLE,
    LIVE_MAX_AGE,
    SPORT,
    PositionMapper,
    canonical_team,
    int_or_none,
    text_or_none,
)
from worker.db import fetch_id_map, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

#: The schedule's `gametime` is local Eastern wall-clock with no offset.
SCHEDULE_TZ = ZoneInfo("America/New_York")

#: nfldata `game_type` -> our `season_type` enum. REG is the regular season;
#: everything else (WC, DIV, CON, SB) is one postseason round or another.
POSTSEASON_TYPES: frozenset[str] = frozenset({"WC", "DIV", "CON", "SB", "POST"})

#: The two conferences. Written explicitly rather than derived from the team
#: file, because there are two of them and they have not changed since 1970 --
#: deriving would add a failure mode (a season with a typo'd conference creating
#: a third) to save two lines.
CONFERENCES: tuple[tuple[str, str], ...] = (
    ("AFC", "American Football Conference"),
    ("NFC", "National Football Conference"),
)


@dataclass
class NflReferenceCounts:
    """Row counts per table, mirroring the college job's deliverable."""

    rows: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def add(self, table: str, n: int) -> None:
        self.rows[table] = self.rows.get(table, 0) + n

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def total(self) -> int:
        return sum(self.rows.values())


# -----------------------------------------------------------------------------
# conferences
# -----------------------------------------------------------------------------
def ingest_conferences(counts: NflReferenceCounts) -> None:
    """AFC and NFC.

    `is_displayed` is true for both, and that is not the same decision the
    college side made. There, it is a filter across 195 conferences of which
    five are shown. Here it is the whole league: hiding one would hide half the
    NFL, so the flag exists only to satisfy the shared schema.
    """
    payload = [
        {
            "name": name,
            "sport": SPORT,
            "abbreviation": abbr,
            "short_name": name,
            # classification stays NULL -- see the module docstring.
            "is_displayed": True,
        }
        for abbr, name in CONFERENCES
    ]
    # `unique (sport, name)` since migration 0035 -- the college adapter's
    # `on conflict (name)` broke on exactly this and took the Sunday chain with
    # it, so the sport belongs in the key here too.
    n = upsert("conferences", payload, conflict_columns=["sport", "name"])
    counts.add("conferences", n)
    log.info("conferences: %d rows", n)


# -----------------------------------------------------------------------------
# teams
# -----------------------------------------------------------------------------
def _team_rows(meta: pl.DataFrame, season_teams: pl.DataFrame) -> list[dict]:
    """Join the colour/division file to the per-season franchise list.

    Driven from the SEASON file rather than the metadata file. The metadata
    carries 36 rows -- the 32 current franchises plus legacy codes like OAK and
    STL -- and inserting those would create teams that no schedule references
    and that `canonical_team()` deliberately refuses to resolve.
    """
    by_abbr = {row["team_abbr"]: row for row in meta.to_dicts()}

    rows: list[dict] = []
    for entry in season_teams.to_dicts():
        abbr = canonical_team(entry.get("team"))
        if abbr is None:
            continue
        info = by_abbr.get(abbr, {})
        full = text_or_none(entry.get("full")) or text_or_none(info.get("team_name"))
        rows.append(
            {
                "abbr": abbr,
                # THE FULL NAME, NOT THE LOCATION, and the four shared-market
                # teams are why. `location` is "Buffalo" for 28 franchises and
                # "New York Jets" for the Jets, because the city alone does not
                # identify them -- so location is not a city column, it is a
                # city column with four exceptions baked in. Storing it would
                # put "Buffalo" beside "New York Jets" in the same field.
                #
                # `school` is a tooltip on the team chip in three of its four
                # uses (the chip itself shows the abbreviation), so being
                # unambiguous matters more than being short. The full name is
                # also UNIQUE across all 32, which keeps a future
                # `fetch_id_map("teams", "school")` from resolving "New York" to
                # whichever of the Giants and the Jets it met first -- the exact
                # trap that a non-unique natural key sets, recorded when the
                # sport column shipped.
                "school": full,
                "mascot": text_or_none(entry.get("nickname"))
                or text_or_none(info.get("team_nick")),
                "conference": text_or_none(info.get("team_conf")),
                "division": text_or_none(info.get("team_division")),
                "color": text_or_none(info.get("team_color")),
                "alt_color": text_or_none(info.get("team_color2")),
            }
        )
    return rows


def ingest_teams(
    client: NflverseClient, season: int, counts: NflReferenceCounts
) -> None:
    meta = client.fetch("team_meta", max_age=IMMUTABLE)
    season_teams = client.fetch("team_seasons", season, max_age=LIVE_MAX_AGE)
    season_teams = season_teams.filter(pl.col("season") == season)

    rows = _team_rows(meta, season_teams)
    if len(rows) != 32:
        # Not a warning. 32 is not a soft expectation about this league, and a
        # short list means a franchise silently failed to resolve -- which shows
        # up later as a game whose home team does not exist rather than as an
        # error here.
        raise ValueError(
            f"expected 32 NFL franchises for {season}, resolved {len(rows)}. "
            f"Check mapping.CURRENT_TEAMS and TEAM_ALIASES against the source."
        )

    team_payload = [
        {
            "nfl_abbr": r["abbr"],
            "school": r["school"],
            "sport": SPORT,
            "mascot": r["mascot"],
            "abbreviation": r["abbr"],
            "color": r["color"],
            "alt_color": r["alt_color"],
        }
        for r in rows
    ]
    n = upsert("teams", team_payload, conflict_columns=["nfl_abbr"])
    counts.add("teams", n)

    team_ids = fetch_id_map("teams", "nfl_abbr", filters={"sport": SPORT})
    conference_ids = fetch_id_map("conferences", "name", filters={"sport": SPORT})
    by_abbr = {abbr: full for abbr, full in CONFERENCES}

    season_payload = []
    for r in rows:
        team_id = team_ids.get(r["abbr"])
        if team_id is None:
            counts.skip("team_season: team not resolved")
            continue
        conference_name = by_abbr.get(r["conference"] or "")
        season_payload.append(
            {
                "team_id": team_id,
                "season": season,
                "conference_id": conference_ids.get(conference_name),
                "division": r["division"],
                # classification stays NULL -- see the module docstring.
            }
        )

    n = upsert(
        "team_seasons", season_payload, conflict_columns=["team_id", "season"]
    )
    counts.add("team_seasons", n)
    log.info("teams %d: %d teams, %d team_seasons", season, len(team_payload), n)


# -----------------------------------------------------------------------------
# schedule
# -----------------------------------------------------------------------------
def kickoff_utc(gameday: str | None, gametime: str | None) -> datetime | None:
    """Combine the schedule's local date and time into an aware UTC instant.

    Returns None when either half is missing, which the schedule does for games
    whose window is not yet fixed -- the same case `games.start_time_tbd`
    exists for on the college side. Guessing a kickoff is how the odds backfill
    ended up asking about games that had already started.
    """
    day = text_or_none(gameday)
    clock = text_or_none(gametime)
    if day is None or clock is None:
        return None
    try:
        naive = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=SCHEDULE_TZ).astimezone(ZoneInfo("UTC"))


def ingest_games(
    client: NflverseClient, season: int, counts: NflReferenceCounts
) -> None:
    schedule = client.fetch("schedule", season, max_age=LIVE_MAX_AGE)
    schedule = schedule.filter(pl.col("season") == season)

    team_ids = fetch_id_map("teams", "nfl_abbr", filters={"sport": SPORT})

    payload = []
    for row in schedule.to_dicts():
        home = canonical_team(row.get("home_team"))
        away = canonical_team(row.get("away_team"))
        if home is None or away is None:
            counts.skip("game: unresolved team")
            continue
        home_id, away_id = team_ids.get(home), team_ids.get(away)
        if home_id is None or away_id is None:
            counts.skip("game: team not in database")
            continue

        game_type = (text_or_none(row.get("game_type")) or "REG").upper()
        kickoff = kickoff_utc(row.get("gameday"), row.get("gametime"))
        home_score = int_or_none(row.get("home_score"))
        away_score = int_or_none(row.get("away_score"))

        payload.append(
            {
                "nflverse_id": text_or_none(row.get("game_id")),
                "season": season,
                "week": int_or_none(row.get("week")),
                "season_type": (
                    "postseason" if game_type in POSTSEASON_TYPES else "regular"
                ),
                "sport": SPORT,
                "start_date": kickoff,
                "start_time_tbd": kickoff is None,
                # The schedule's `location` is 'Home' or 'Neutral'.
                "neutral_site": (
                    text_or_none(row.get("location")) or "Home"
                ).lower() == "neutral",
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_points": home_score,
                "away_points": away_score,
                # A game is complete when it has a score, which is how the
                # source expresses it -- there is no `completed` column. This
                # is what the daily incremental stats load keys off, so getting
                # it from the scores rather than from the clock keeps the two
                # in step.
                "completed": home_score is not None and away_score is not None,
            }
        )

    n = upsert("games", payload, conflict_columns=["nflverse_id"])
    counts.add("games", n)
    log.info(
        "games %d: %d rows (%d complete)",
        season, n, sum(1 for p in payload if p["completed"]),
    )


# -----------------------------------------------------------------------------
# rosters
# -----------------------------------------------------------------------------
def ingest_rosters(
    client: NflverseClient, season: int, counts: NflReferenceCounts
) -> None:
    roster = client.fetch("roster", season, max_age=LIVE_MAX_AGE)
    mapper = PositionMapper()

    team_ids = fetch_id_map("teams", "nfl_abbr", filters={"sport": SPORT})

    player_payload = []
    memberships: list[dict] = []
    for row in roster.to_dicts():
        gsis = text_or_none(row.get("gsis_id"))
        if gsis is None:
            # Measured: 1 of 2,946 in the 2026 file. Counted rather than
            # dropped silently, and not fatal -- gsis_id is the key, so a
            # player without one cannot be joined to a stat line anyway.
            counts.skip("player: no gsis_id")
            continue

        name = text_or_none(row.get("full_name"))
        if name is None:
            counts.skip("player: no name")
            continue

        # `depth_chart_position` is the position the player is listed AT;
        # `position` is what they are. For a prop model the first is the more
        # useful of the two, and it falls back to the second.
        raw_position = text_or_none(
            row.get("depth_chart_position")
        ) or text_or_none(row.get("position"))
        group = mapper.group_for(raw_position)

        player_payload.append(
            {
                "gsis_id": gsis,
                "name": name,
                "sport": SPORT,
                "first_name": text_or_none(row.get("first_name")),
                "last_name": text_or_none(row.get("last_name")),
                "position_group": group,
                "position_raw": raw_position,
                "height_inches": int_or_none(row.get("height")),
                "weight_lbs": int_or_none(row.get("weight")),
            }
        )

        abbr = canonical_team(row.get("team"))
        memberships.append(
            {
                "gsis_id": gsis,
                "team_abbr": abbr,
                "position_group": group,
                "position_raw": raw_position,
                "jersey": int_or_none(row.get("jersey_number")),
                "height_inches": int_or_none(row.get("height")),
                "weight_lbs": int_or_none(row.get("weight")),
            }
        )

    n = upsert("players", player_payload, conflict_columns=["gsis_id"])
    counts.add("players", n)

    player_ids = fetch_id_map("players", "gsis_id")

    membership_payload = []
    for m in memberships:
        player_id = player_ids.get(m["gsis_id"])
        team_id = team_ids.get(m["team_abbr"]) if m["team_abbr"] else None
        if player_id is None or team_id is None:
            counts.skip("membership: unresolved player or team")
            continue
        membership_payload.append(
            {
                "player_id": player_id,
                "team_id": team_id,
                "season": season,
                "position_group": m["position_group"],
                "position_raw": m["position_raw"],
                "jersey": m["jersey"],
                "height_inches": m["height_inches"],
                "weight_lbs": m["weight_lbs"],
            }
        )

    n = upsert(
        "player_team_seasons",
        membership_payload,
        conflict_columns=["player_id", "team_id", "season"],
    )
    counts.add("player_team_seasons", n)
    mapper.report()
    log.info(
        "rosters %d: %d players, %d memberships",
        season, len(player_payload), n,
    )


# -----------------------------------------------------------------------------
# orchestration
# -----------------------------------------------------------------------------
def run_nfl_reference_ingest(
    client: NflverseClient, seasons: list[int]
) -> NflReferenceCounts:
    """Conferences, then teams, then schedule and rosters, per season."""
    counts = NflReferenceCounts()
    ingest_conferences(counts)

    for season in seasons:
        log.info("--- NFL season %d ---", season)
        ingest_teams(client, season, counts)
        ingest_games(client, season, counts)
        ingest_rosters(client, season, counts)

    if counts.skipped:
        log.info(
            "skipped rows by reason: %s",
            ", ".join(f"{k}={v:,}" for k, v in sorted(counts.skipped.items())),
        )
    return counts


__all__ = [
    "TEAM_META_LOGO_COLUMNS",
    "NflReferenceCounts",
    "ingest_conferences",
    "ingest_games",
    "ingest_rosters",
    "ingest_teams",
    "kickoff_utc",
    "run_nfl_reference_ingest",
]
