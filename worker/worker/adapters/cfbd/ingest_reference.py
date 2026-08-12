"""Phase 2b — reference and schedule ingest.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3).

Loads conferences, teams, season-scoped team attributes, venues, games, players
and roster membership. Everything downstream keys off these tables, so they are
loaded first and in dependency order.

Two decisions worth reading before the code:

**All teams are ingested, not just FBS.** 14% of a season's FBS schedule (121 of
874 games in 2024) is played against non-FBS opponents, mostly in week 1.
`games.home_team_id` / `away_team_id` are NOT NULL foreign keys, so ingesting
only the 134 FBS teams would fail on every one of those games — and skipping
them is worse than a constraint error, because an FBS player's production
against an FCS opponent is real and omitting those weeks would bias every
rolling average and every defensive split. `get_teams(year)` returns all 679
teams across classifications and covers every referenced id.

This does not contradict CLAUDE.md §4's "ingest all FBS teams": that rule sets a
floor against conference-based data cuts, and a superset satisfies it.

**Conference seeding is preserved.** The seed migration set `is_displayed` for
the five displayed conferences. Ingest reconciles names and metadata against the
API but must never overwrite that column — it is a display choice, not API data,
and clobbering it would empty the UI's conference filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cfbd

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.mapping import (
    PositionNormalizer,
    bigint_or_none,
    inches_or_none,
    normalize_classification,
    normalize_season_type,
    pounds_or_none,
    smallint_or_none,
    week_on_season_axis,
)
from worker.db import fetch_id_map, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

# The sport every row this adapter writes belongs to (migration 0035).
#
# Stated explicitly rather than left to the column default. The default exists so
# that adding the dimension did not require touching every insert in the project;
# THIS file is the one place where being explicit is the point, because it is the
# sport-specific adapter (CLAUDE.md §3) and the NFL one will be its mirror image.
# A reader comparing the two should be able to see the seam without diffing a
# schema.
SPORT = "cfb"

# Completed seasons are immutable, so their responses never expire. The caller
# overrides this for the current season.
IMMUTABLE = None


@dataclass
class IngestCounts:
    """Row counts per table, for the Phase 2 deliverable."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, table: str, n: int) -> None:
        self.counts[table] = self.counts.get(table, 0) + n

    def total(self) -> int:
        return sum(self.counts.values())

    def render(self) -> str:
        width = max((len(t) for t in self.counts), default=0)
        return "\n".join(
            f"  {table:<{width}}  {n:>7,}"
            for table, n in sorted(self.counts.items())
        )


# -----------------------------------------------------------------------------
# Conferences
# -----------------------------------------------------------------------------
def ingest_conferences(client: CfbdClient, counts: IngestCounts) -> None:
    rows = client.fetch("/conferences", cfbd.ConferencesApi, "get_conferences",
                        max_age=IMMUTABLE)

    # CFBD returns several names twice — a current conference and a defunct
    # record carrying the same name (e.g. "Ivy" as both today's FCS conference,
    # id 22, and a long-obsolete FBS classification, id 212). The unique key is
    # `(sport, name)` and every row here carries the same sport, so one has to
    # win — and a multi-row upsert hitting the same key twice in one statement is
    # rejected outright rather than resolved.
    #
    # Tie-break on the LOWEST CFBD id, which is the older registration and picks
    # the surviving conference in every case that matters here. Known
    # imperfection: "Missouri Valley" resolves to the defunct MVIAA rather than
    # the current MVC. All seven duplicated names are non-FBS and none are
    # displayed, and a team's own classification lives on team_seasons, so this
    # affects no ingest, feature or UI path — it is recorded rather than hidden.
    best_by_name: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = r.get("name")
        if not name:
            continue
        incumbent = best_by_name.get(name)
        if incumbent is None or (r.get("id") or 0) < (incumbent.get("id") or 0):
            best_by_name[name] = r

    if len(best_by_name) < len([r for r in rows if r.get("name")]):
        log.info(
            "conferences: %d duplicate name(s) in the API response resolved to "
            "the lowest CFBD id",
            len([r for r in rows if r.get("name")]) - len(best_by_name),
        )

    payload = [
        {
            "cfbd_id": r.get("id"),
            "name": name,
            "sport": SPORT,
            "abbreviation": r.get("abbreviation"),
            "short_name": r.get("shortName"),
            "classification": normalize_classification(r.get("classification")),
        }
        for name, r in best_by_name.items()
    ]

    # is_displayed is deliberately absent from update_columns: it is a UI choice
    # made in the seed migration, not something the API knows about. `sport` is
    # absent for a different reason — it is part of the conflict key, so an
    # existing row matched here is already this sport's row.
    #
    # THE CONFLICT TARGET IS (sport, name), NOT (name). Migration 0035 replaced
    # the global unique on the name, and `on conflict (name)` names an index
    # rather than a column list: it would raise "no unique or exclusion
    # constraint matching the ON CONFLICT specification" on the next Sunday run
    # and take the whole weekly chain down with it, since this is its first step.
    n = upsert(
        "conferences",
        payload,
        conflict_columns=["sport", "name"],
        update_columns=["cfbd_id", "abbreviation", "short_name", "classification"],
    )
    counts.add("conferences", n)
    log.info("conferences: %d rows", n)


# -----------------------------------------------------------------------------
# Venues
# -----------------------------------------------------------------------------
def ingest_venues(client: CfbdClient, counts: IngestCounts) -> None:
    rows = client.fetch("/venues", cfbd.VenuesApi, "get_venues", max_age=IMMUTABLE)

    payload = [
        {
            "cfbd_id": r.get("id"),
            "name": r.get("name"),
            "city": r.get("city"),
            "state": r.get("state"),
            "zip": r.get("zip"),
            "country_code": r.get("countryCode"),
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
            "elevation_m": r.get("elevation"),
            "capacity": r.get("capacity"),
            "is_dome": bool(r.get("dome")),
            "has_grass": r.get("grass"),
            "timezone": r.get("timezone"),
        }
        for r in rows
        if r.get("id") is not None and r.get("name")
    ]

    n = upsert("venues", payload, conflict_columns=["cfbd_id"])
    counts.add("venues", n)
    log.info("venues: %d rows", n)


# -----------------------------------------------------------------------------
# Teams and team_seasons
# -----------------------------------------------------------------------------
def ingest_teams(client: CfbdClient, season: int, counts: IngestCounts) -> None:
    """Ingest every team for a season, across all classifications.

    See the module docstring: FBS-only would break foreign keys on 14% of games.
    """
    rows = client.fetch("/teams", cfbd.TeamsApi, "get_teams",
                        year=season, max_age=IMMUTABLE)

    team_payload = []
    for r in rows:
        if r.get("id") is None or not r.get("school"):
            continue
        alt_names = r.get("alternateNames") or []
        team_payload.append(
            {
                "cfbd_id": r["id"],
                "school": r["school"],
                "sport": SPORT,
                "mascot": r.get("mascot"),
                "abbreviation": r.get("abbreviation"),
                # alternateNames is a list; the schema holds a single alt_name.
                "alt_name": alt_names[0] if alt_names else None,
                "color": r.get("color"),
                "alt_color": r.get("alternateColor"),
            }
        )

    n = upsert("teams", team_payload, conflict_columns=["cfbd_id"])
    counts.add("teams", n)

    # team_seasons needs surrogate ids, so resolve after teams are written.
    team_ids = fetch_id_map("teams", "cfbd_id")
    # Scoped by sport: `cfbd_id` above is globally unique, a conference NAME is
    # only unique within one (migration 0035). Unscoped, this would eventually
    # map a college conference name onto another sport's row.
    conference_ids = fetch_id_map("conferences", "name", filters={"sport": SPORT})

    season_payload = []
    unknown_conferences: set[str] = set()
    for r in rows:
        team_id = team_ids.get(r.get("id"))
        if team_id is None:
            continue

        conference_name = r.get("conference")
        conference_id = conference_ids.get(conference_name) if conference_name else None
        if conference_name and conference_id is None:
            unknown_conferences.add(conference_name)

        season_payload.append(
            {
                "team_id": team_id,
                "season": season,
                "conference_id": conference_id,
                "division": r.get("division"),
                "classification": normalize_classification(r.get("classification")),
            }
        )

    n = upsert(
        "team_seasons",
        season_payload,
        conflict_columns=["team_id", "season"],
    )
    counts.add("team_seasons", n)

    if unknown_conferences:
        # Not fatal — conference_id is nullable and these are overwhelmingly
        # non-FBS conferences we do not display. Logged so a genuinely new FBS
        # conference cannot slip in unnoticed after realignment.
        log.info(
            "%d conference name(s) not in the conferences table (left null): %s",
            len(unknown_conferences),
            ", ".join(sorted(unknown_conferences)[:8]),
        )

    log.info("teams %d: %d teams, %d team_seasons", season, len(team_payload), n)


# -----------------------------------------------------------------------------
# Games
# -----------------------------------------------------------------------------
def ingest_games(client: CfbdClient, season: int, counts: IngestCounts) -> None:
    team_ids = fetch_id_map("teams", "cfbd_id")
    venue_ids = fetch_id_map("venues", "cfbd_id")

    total = 0
    for season_type in ("regular", "postseason"):
        rows = client.fetch(
            "/games", cfbd.GamesApi, "get_games",
            year=season, season_type=season_type, classification="fbs",
            max_age=IMMUTABLE,
        )

        payload = []
        skipped_unknown_team = 0
        for r in rows:
            home_id = team_ids.get(r.get("homeId"))
            away_id = team_ids.get(r.get("awayId"))
            if home_id is None or away_id is None or home_id == away_id:
                # games_teams_differ CHECK plus unresolvable ids. Counted rather
                # than silently dropped so the number is visible in the log.
                skipped_unknown_team += 1
                continue

            week = smallint_or_none(r.get("week"))
            if week is None or week < 1:
                skipped_unknown_team += 1
                continue

            # CFBD restarts week numbering at 1 for the postseason, so a bowl
            # arrives labelled the same as the season opener. `games.week` is
            # the time axis every cutoff in this schema is defined against, so
            # it has to be monotone in time — see `week_on_season_axis`.
            row_season_type = normalize_season_type(r.get("seasonType"))
            week = week_on_season_axis(week, row_season_type)

            payload.append(
                {
                    "cfbd_id": r.get("id"),
                    "season": season,
                    "week": week,
                    "sport": SPORT,
                    "season_type": row_season_type,
                    "start_date": r.get("startDate"),
                    "start_time_tbd": bool(r.get("startTimeTBD")),
                    "neutral_site": bool(r.get("neutralSite")),
                    "conference_game": r.get("conferenceGame"),
                    "venue_id": venue_ids.get(r.get("venueId")),
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "home_points": smallint_or_none(r.get("homePoints")),
                    "away_points": smallint_or_none(r.get("awayPoints")),
                    "completed": bool(r.get("completed")),
                    "attendance": r.get("attendance"),
                }
            )

        n = upsert("games", payload, conflict_columns=["cfbd_id"])
        total += n
        counts.add("games", n)

        if skipped_unknown_team:
            log.warning(
                "games %d %s: skipped %d row(s) with unresolvable teams or weeks",
                season, season_type, skipped_unknown_team,
            )
        log.info("games %d %s: %d rows", season, season_type, n)

    log.info("games %d total: %d rows", season, total)


# -----------------------------------------------------------------------------
# Players and roster membership
# -----------------------------------------------------------------------------
def ingest_rosters(
    client: CfbdClient,
    season: int,
    counts: IngestCounts,
    normalizer: PositionNormalizer,
) -> None:
    """Ingest all FBS rosters for a season.

    `classification='fbs'` returns every FBS roster in ONE call (~16k players)
    rather than one call per team, which is the difference between 2 calls and
    268 for the backfill.
    """
    rows = client.fetch(
        "/roster", cfbd.TeamsApi, "get_roster",
        year=season, classification="fbs", max_age=IMMUTABLE,
    )

    # players first: identity is independent of team, and a mid-season transfer
    # is two player_team_seasons rows for one player.
    player_payload: dict[int, dict[str, Any]] = {}
    for r in rows:
        # /roster returns athlete ids as strings; every lookup below must use
        # the same int type the column holds. See mapping.bigint_or_none.
        athlete_id = bigint_or_none(r.get("id"))
        if athlete_id is None:
            continue

        first = (r.get("firstName") or "").strip()
        last = (r.get("lastName") or "").strip()
        name = f"{first} {last}".strip()
        if not name:
            continue

        # Dedupe within the payload: a player appearing twice in one response
        # would make the multi-row upsert hit the same key twice in one
        # statement, which Postgres rejects outright.
        player_payload[athlete_id] = {
            "cfbd_athlete_id": athlete_id,
            "name": name,
            "sport": SPORT,
            "first_name": first or None,
            "last_name": last or None,
            "position_group": normalizer.normalize(r.get("position")),
            "position_raw": r.get("position"),
            "height_inches": inches_or_none(r.get("height")),
            "weight_lbs": pounds_or_none(r.get("weight")),
        }

    n = upsert(
        "players",
        list(player_payload.values()),
        conflict_columns=["cfbd_athlete_id"],
    )
    counts.add("players", n)

    player_ids = fetch_id_map("players", "cfbd_athlete_id")
    # Scoped for the same reason as the conference map above, and here the risk
    # is more concrete: `teams` has no unique constraint on `school` at all, so
    # two sports sharing a city name would resolve to whichever row came back.
    team_ids_by_school = fetch_id_map("teams", "school", filters={"sport": SPORT})

    membership: dict[tuple[int, int, int], dict[str, Any]] = {}
    unresolved_teams: set[str] = set()
    unresolved_players = 0
    for r in rows:
        player_id = player_ids.get(bigint_or_none(r.get("id")))
        school = r.get("team")
        team_id = team_ids_by_school.get(school) if school else None

        if player_id is None:
            unresolved_players += 1
            continue
        if team_id is None:
            if school:
                unresolved_teams.add(school)
            continue

        membership[(player_id, team_id, season)] = {
            "player_id": player_id,
            "team_id": team_id,
            "season": season,
            "position_group": normalizer.normalize(r.get("position")),
            "position_raw": r.get("position"),
            "jersey": smallint_or_none(r.get("jersey")),
            # CFBD sends class year as an int (1-4); the column is text.
            "class_year": str(r["year"]) if r.get("year") is not None else None,
            "height_inches": inches_or_none(r.get("height")),
            "weight_lbs": pounds_or_none(r.get("weight")),
        }

    n = upsert(
        "player_team_seasons",
        list(membership.values()),
        conflict_columns=["player_id", "team_id", "season"],
    )
    counts.add("player_team_seasons", n)

    if unresolved_teams:
        log.warning(
            "%d roster school name(s) did not match teams.school: %s",
            len(unresolved_teams),
            ", ".join(sorted(unresolved_teams)[:10]),
        )

    # A join that resolves almost nothing is a defect, not a data quirk, and it
    # produces zero rows rather than an exception — so it has to be asserted.
    # This exact failure already happened once: /roster returns athlete ids as
    # strings, the lookup compared them against ints, and every one of 16,221
    # rows silently failed to match while the job reported success.
    if rows and unresolved_players > len(rows) * 0.05:
        raise RuntimeError(
            f"rosters {season}: {unresolved_players:,} of {len(rows):,} roster rows "
            "did not resolve to a player id. That is an id-mapping bug, not "
            "missing data — check for a type mismatch between the API payload "
            "and players.cfbd_athlete_id."
        )

    log.info(
        "rosters %d: %d players, %d memberships (%d unresolved)",
        season, len(player_payload), n, unresolved_players,
    )


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------
def estimate_calls(seasons: list[int]) -> int:
    """Calls this ingest needs, for the quota preflight.

    2 season-independent (conferences, venues) + 4 per season
    (teams, roster, regular games, postseason games).
    """
    return 2 + 4 * len(seasons)


def run_reference_ingest(client: CfbdClient, seasons: list[int]) -> IngestCounts:
    """Load reference and schedule data for the given seasons, in FK order."""
    counts = IngestCounts()
    normalizer = PositionNormalizer()

    ingest_conferences(client, counts)
    ingest_venues(client, counts)

    for season in seasons:
        log.info("--- season %d ---", season)
        ingest_teams(client, season, counts)
        ingest_games(client, season, counts)
        ingest_rosters(client, season, counts, normalizer)

    normalizer.report()
    return counts
