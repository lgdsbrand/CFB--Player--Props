"""Phase 2d — team rating snapshots and weather.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3).

THE OFF-BY-ONE THAT WOULD HAVE POISONED EVERY BACKTEST
------------------------------------------------------
`get_elo(year, week=N)` returns the rating **AFTER week N has been played**, not
entering it. Measured against 2024 week 8: the values matched `postgameElo` for
118 of 118 team-games and `pregameElo` for 0. Storing that as `as_of_week = N`
would hand a week-N prediction the results of week N — the "silent,
disqualifying bug" CLAUDE.md §4 names explicitly, and one that produces
flattering backtest numbers rather than an error.

So Elo is NOT sourced from `/ratings/elo` at all. It comes from
`games.pregameElo`, which is definitionally the rating a team carried INTO a
specific game. That choice has three advantages beyond safety:

  * No off-by-one to reason about, now or when someone revisits this later.
  * Week 1 is covered, which a week-shifted `/ratings/elo` cannot be.
  * It costs zero extra API calls — the /games responses are already cached.

Coverage is every FBS team-game in both seasons; the ~7% without a value are FCS
opponents, which have no Elo to report.

SP+, SRS AND FPI ARE SEASON-FINAL, NOT FEATURES
-----------------------------------------------
None of those endpoints accept a `week`, so for historical seasons they can only
be end-of-season values. They are stored with `snapshot_kind = 'season_final'`,
which the schema's CHECK forces to carry a NULL `as_of_week` — making them
physically unreachable from any feature query that joins on `as_of_week`. They
remain valid for retrospective sanity checks, which is all they can honestly be.

POSTSEASON
----------
Postseason games are all `week = 1`, which collides with regular-season week 1
under the unique index on (team, season, source, as_of_week). Point-in-time Elo
is therefore ingested for the REGULAR SEASON only. Weekly props are a
regular-season product; bowl-game support would need a distinct week axis and is
out of scope here rather than silently mis-keyed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cfbd
from psycopg.types.json import Json

from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.mapping import (
    bigint_or_none,
    smallint_or_none,
    week_for_api,
)
from worker.db import fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

IMMUTABLE = None

# CFBD endpoint -> rating_source enum value.
SEASON_FINAL_SOURCES = {
    "sp_plus": ("/ratings/sp", "get_sp"),
    "srs": ("/ratings/srs", "get_srs"),
    "fpi": ("/ratings/fpi", "get_fpi"),
}


@dataclass
class RatingCounts:
    counts: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def skip(self, reason: str, n: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + n

    def total(self) -> int:
        return sum(self.counts.values())


def _team_id_by_school() -> dict[str, int]:
    return {r["school"]: r["id"] for r in fetch_all("select id, school from teams")}


def _nested_rating(value: Any) -> float | None:
    """Pull the scalar out of a rating that may arrive nested.

    SP+ returns `offense`/`defense`/`specialTeams` as OBJECTS carrying a
    `rating` plus breakdowns, while SRS returns plain numbers. Passing the raw
    object to psycopg fails with "cannot adapt type 'dict'", so the shape has to
    be handled here rather than assumed.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        rating = value.get("rating")
        return float(rating) if isinstance(rating, (int, float)) else None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _structured_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Nested payload with no dedicated column, preserved in `raw`."""
    return {k: v for k, v in row.items() if isinstance(v, (dict, list))}


# -----------------------------------------------------------------------------
# Elo — point-in-time, from pregame ratings
# -----------------------------------------------------------------------------
def ingest_elo_snapshots(
    client: CfbdClient, season: int, counts: RatingCounts
) -> None:
    """Store the Elo each team carried INTO each regular-season week.

    See the module docstring: `/ratings/elo` is deliberately not used.
    """
    team_ids = _team_id_by_school()

    games = client.fetch(
        "/games", cfbd.GamesApi, "get_games",
        year=season, season_type="regular", classification="fbs",
        max_age=IMMUTABLE,
    )

    # One row per (team, week) — but a team CAN play twice in the same labelled
    # week. CFBD folds "Week 0" games into week 1, so the 2024 Dublin opener
    # (Georgia Tech vs Florida State) gives both teams two week-1 games.
    #
    # Keeping the last one seen stored the SECOND game's pregame Elo, which is
    # the FIRST game's postgame Elo — i.e. a result from inside week 1 labelled
    # as known entering week 1. That is exactly the lookahead this module exists
    # to prevent, and it was caught by verification rather than by review.
    #
    # Sorting by kickoff and keeping the EARLIEST game makes as_of_week = N mean
    # "before any week-N game was played", which is the only defensible reading.
    games = sorted(games, key=lambda g: (g.get("startDate") or "", g.get("id") or 0))

    snapshots: dict[tuple[int, int], dict[str, Any]] = {}

    for g in games:
        week = smallint_or_none(g.get("week"))
        if week is None or week < 1:
            counts.skip("elo: bad week")
            continue

        for side in ("home", "away"):
            team_name = g.get(f"{side}Team")
            elo = g.get(f"{side}PregameElo")
            team_id = team_ids.get(team_name) if team_name else None

            if elo is None:
                # Overwhelmingly FCS opponents, which carry no Elo.
                counts.skip("elo: no pregame rating")
                continue
            if team_id is None:
                counts.skip("elo: unknown team")
                continue

            # setdefault, not assignment: first (earliest) game of the week wins.
            snapshots.setdefault(
                (team_id, week),
                {
                    "team_id": team_id,
                    "season": season,
                    "source": "elo",
                    "snapshot_kind": "point_in_time",
                    "as_of_week": week,
                    "rating": elo,
                },
            )

    n = upsert(
        "team_rating_snapshots",
        list(snapshots.values()),
        conflict_columns=["team_id", "season", "source", "as_of_week"],
        update_columns=["rating"],
        conflict_where="snapshot_kind = 'point_in_time'",
    )
    counts.add("elo (point_in_time)", n)
    log.info("elo snapshots %d: %d rows", season, n)


# -----------------------------------------------------------------------------
# SP+, SRS, FPI — season-final only
# -----------------------------------------------------------------------------
def ingest_season_final_ratings(
    client: CfbdClient, season: int, counts: RatingCounts
) -> None:
    team_ids = _team_id_by_school()

    for source, (endpoint, method) in SEASON_FINAL_SOURCES.items():
        rows = client.fetch(
            endpoint, cfbd.RatingsApi, method, year=season, max_age=IMMUTABLE
        )

        payload: dict[int, dict[str, Any]] = {}
        for r in rows:
            team_id = team_ids.get(r.get("team"))
            if team_id is None:
                # SP+ includes a national-averages pseudo-row, and SRS covers
                # divisions below what we ingest.
                counts.skip(f"{source}: unknown team")
                continue

            # FPI names its headline value `fpi`; SP+ and SRS use `rating`.
            rating = r.get("fpi") if source == "fpi" else r.get("rating")

            payload[team_id] = {
                "team_id": team_id,
                "season": season,
                "source": source,
                "snapshot_kind": "season_final",
                # NULL by the schema CHECK: a season-final value has no week at
                # which it was known, so it can never be read as a feature.
                "as_of_week": None,
                "rating": rating,
                "ranking": smallint_or_none(r.get("ranking")),
                "offense_rating": _nested_rating(r.get("offense")),
                "defense_rating": _nested_rating(r.get("defense")),
                "special_teams_rating": _nested_rating(r.get("specialTeams")),
                # Everything structured that has no column — SP+'s success and
                # explosiveness splits, FPI's efficiencies and resume ranks —
                # is kept verbatim so a later phase can use it without
                # re-ingesting.
                "raw": Json(_structured_fields(r) or None),
            }

        n = upsert(
            "team_rating_snapshots",
            list(payload.values()),
            conflict_columns=["team_id", "season", "source"],
            update_columns=[
                "rating", "ranking", "offense_rating", "defense_rating",
                "special_teams_rating", "raw",
            ],
            conflict_where="snapshot_kind = 'season_final'",
        )
        counts.add(f"{source} (season_final)", n)
        log.info("%s %d: %d rows", source, season, n)


# -----------------------------------------------------------------------------
# Weather
# -----------------------------------------------------------------------------
def ingest_weather(
    client: CfbdClient, season: int, counts: RatingCounts
) -> None:
    game_ids = {
        r["cfbd_id"]: r["id"]
        for r in fetch_all(
            "select id, cfbd_id from games where season = %s", (season,)
        )
    }

    slices = fetch_all(
        """
        select distinct season_type::text as season_type, week
          from games where season = %s order by season_type, week
        """,
        (season,),
    )

    payload: dict[int, dict[str, Any]] = {}
    for s in slices:
        # `games.week` carries postseason weeks offset onto a monotone season
        # axis; CFBD's endpoint wants its own numbering back.
        rows = client.fetch(
            "/games/weather", cfbd.GamesApi, "get_weather",
            year=season,
            week=week_for_api(s["week"], s["season_type"]),
            season_type=s["season_type"],
            classification="fbs", max_age=IMMUTABLE,
        )

        for r in rows:
            game_id = game_ids.get(bigint_or_none(r.get("id")))
            if game_id is None:
                counts.skip("weather: unknown game")
                continue

            payload[game_id] = {
                "game_id": game_id,
                "source": "cfbd",
                "temperature_f": r.get("temperature"),
                "dew_point_f": r.get("dewPoint"),
                "humidity": r.get("humidity"),
                "precipitation_in": r.get("precipitation"),
                "snowfall_in": r.get("snowfall"),
                "wind_speed_mph": r.get("windSpeed"),
                "wind_direction_deg": r.get("windDirection"),
                "pressure_mb": r.get("pressure"),
                "condition": r.get("weatherCondition"),
                "is_indoor": r.get("gameIndoors"),
                "observed_at": r.get("startTime"),
            }

    n = upsert(
        "game_weather",
        list(payload.values()),
        conflict_columns=["game_id", "source"],
    )
    counts.add("weather", n)
    log.info("weather %d: %d rows", season, n)


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------
def estimate_calls(season: int) -> int:
    """3 season-final ratings + 1 weather call per week slice.

    Elo costs nothing: it is read from the already-cached /games responses.
    """
    slices = fetch_all(
        """
        select count(distinct (season_type, week)) as n
          from games where season = %s
        """,
        (season,),
    )
    return 3 + int(slices[0]["n"] if slices else 0)


def run_ratings_ingest(client: CfbdClient, season: int) -> RatingCounts:
    counts = RatingCounts()
    log.info("--- season %d ---", season)

    ingest_elo_snapshots(client, season, counts)
    ingest_season_final_ratings(client, season, counts)
    ingest_weather(client, season, counts)

    if counts.skipped:
        log.info(
            "skipped by reason: %s",
            ", ".join(f"{k}={v:,}" for k, v in sorted(counts.skipped.items())),
        )
    return counts
