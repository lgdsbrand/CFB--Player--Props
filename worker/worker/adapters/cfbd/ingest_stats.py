"""Phase 2c — box scores, play-by-play, and per-play attribution.

SPORT-SPECIFIC ADAPTER (CLAUDE.md §3).

Loads the three highest-volume tables:

  * `player_game_stats` — the ACTUALS. Every hit rate, backtest grade and
    calibration number resolves here.
  * `plays` — trimmed play-by-play.
  * `play_player_stats` — per-play attribution, the raw material the
    position-split engine (§5) aggregates.

**`/plays/stats` is fetched per GAME, not per week, and this is not an
optimisation choice — it is a correctness one.** The endpoint caps a response at
2,000 rows and truncates silently. A week-level call for 2024 week 5 returned
2,000 rows covering 11 of 56 games, while SEC and ACC alone account for 2,475.
Building the split engine on that would produce confident defensive numbers from
under a fifth of the plays, with nothing in any log to suggest a problem.
Per-game responses run ~215 rows, comfortably clear of the cap.

`/plays` and `/games/players` were checked for the same failure and are not
capped: both return every completed game in the week.

Loading uses COPY into a slice that is deleted first, rather than upsert. These
tables are append-only and large; a delete-then-COPY of one season is both
faster and easier to reason about than several hundred thousand upserts, and it
makes a re-run exactly idempotent.

SACKS: the two sources disagree BY DEFINITION, and the difference is expected
--------------------------------------------------------------------------
NCAA charges a sack as a RUSHING LOSS against the quarterback, so box-score
`rush_yards` includes it. CFBD's play attribution keeps sacks out of 'Rush' and
records them under 'Sack Taken', whose stat_value is a positive magnitude of
yards lost. Verified across 2024:

    box rush_yards = sum('Rush') - sum('Sack Taken')

Reconciliation of QB rushing rises from 32.7% to 69.9% exact once sacks are
subtracted (80.9% exact / 85.4% within two yards across all positions).
Shedeur Sanders: 30 attributed rushing yards, 61 sack yards, box score -31.

This is why the split between the two tables matters rather than being
redundant storage:

  * `player_game_stats` (box score) is the ACTUALS table. Markets grade against
    it, so a QB rushing prop inherits the book's own convention.
  * `play_player_stats` (attribution) feeds the position-split engine, where
    excluding sacks is arguably the better run-defense signal — a sack is a
    pass-rush outcome, not evidence about defending the run.

The residual ~15% is attribution gaps (laterals, fumble-recovery yardage,
unattributed plays), not a systematic bias: mean signed difference for RBs is
+0.1 yards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cfbd

from worker.adapters.cfbd.boxscore import BoxScoreParser
from worker.adapters.cfbd.client import CfbdClient
from worker.adapters.cfbd.mapping import bigint_or_none, smallint_or_none
from worker.db import copy_into, execute, fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

IMMUTABLE = None

# Columns COPYed into plays, in order.
PLAY_COLUMNS = (
    "cfbd_id", "game_id", "season", "week",
    "offense_team_id", "defense_team_id",
    "period", "clock_seconds", "down", "distance",
    "yards_to_goal", "yards_gained", "play_type", "play_text",
    "scoring", "ppa", "offense_score", "defense_score",
)

PLAY_PLAYER_COLUMNS = (
    "play_id", "game_id", "player_id", "team_id", "opponent_team_id",
    "season", "week", "position_group", "stat_type", "stat_value",
)


@dataclass
class StatsCounts:
    counts: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def add(self, table: str, n: int) -> None:
        self.counts[table] = self.counts.get(table, 0) + n

    def skip(self, reason: str, n: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + n

    def total(self) -> int:
        return sum(self.counts.values())


# -----------------------------------------------------------------------------
# Shared lookups
# -----------------------------------------------------------------------------
@dataclass
class SeasonContext:
    """Id maps for one season, built once and reused across all three loaders."""

    season: int
    games_by_cfbd: dict[int, dict[str, Any]]
    game_id_by_cfbd: dict[int, int]
    team_id_by_school: dict[str, int]
    player_id_by_athlete: dict[int, int]
    position_by_player: dict[int, str]

    @classmethod
    def build(cls, season: int) -> SeasonContext:
        games = fetch_all(
            """
            select id, cfbd_id, season, week, home_team_id, away_team_id
              from games where season = %s
            """,
            (season,),
        )
        games_by_cfbd = {g["cfbd_id"]: g for g in games}

        teams = fetch_all("select id, school from teams")
        players = fetch_all(
            "select id, cfbd_athlete_id from players where cfbd_athlete_id is not null"
        )

        # Position as of THIS season. play_player_stats denormalizes it so a
        # player who changes position later cannot retroactively rewrite
        # historical defensive splits.
        positions = fetch_all(
            """
            select distinct on (player_id) player_id, position_group
              from player_team_seasons where season = %s
             order by player_id, id
            """,
            (season,),
        )

        return cls(
            season=season,
            games_by_cfbd=games_by_cfbd,
            game_id_by_cfbd={k: v["id"] for k, v in games_by_cfbd.items()},
            team_id_by_school={t["school"]: t["id"] for t in teams},
            player_id_by_athlete={
                p["cfbd_athlete_id"]: p["id"] for p in players
            },
            position_by_player={
                p["player_id"]: p["position_group"] for p in positions
            },
        )

    def position_for(self, player_id: int) -> str:
        return self.position_by_player.get(player_id, "OTHER")


def week_slices(season: int) -> list[tuple[str, int]]:
    """(season_type, week) pairs that actually have games, from our own games."""
    rows = fetch_all(
        """
        select distinct season_type::text as season_type, week
          from games where season = %s order by season_type, week
        """,
        (season,),
    )
    return [(r["season_type"], r["week"]) for r in rows]


# -----------------------------------------------------------------------------
# player_game_stats
# -----------------------------------------------------------------------------
def ingest_player_game_stats(
    client: CfbdClient, ctx: SeasonContext, counts: StatsCounts
) -> None:
    parser = BoxScoreParser()
    payload: list[dict[str, Any]] = []

    for season_type, week in week_slices(ctx.season):
        games = client.fetch(
            "/games/players", cfbd.GamesApi, "get_game_player_stats",
            year=ctx.season, week=week, season_type=season_type,
            classification="fbs", max_age=IMMUTABLE,
        )

        for game_payload in games:
            game = ctx.games_by_cfbd.get(bigint_or_none(game_payload.get("id")))
            if game is None:
                counts.skip("box score: unknown game")
                continue

            for row in parser.parse_game(game_payload):
                player_id = ctx.player_id_by_athlete.get(row["cfbd_athlete_id"])
                if player_id is None:
                    # Rosters cover FBS only, so an FCS opponent's players have
                    # no row. Their production is not modelled, but it is
                    # counted here rather than dropped invisibly.
                    counts.skip("box score: player not in roster")
                    continue

                is_home = row.get("home_away") == "home"
                team_id = game["home_team_id"] if is_home else game["away_team_id"]
                opponent_id = game["away_team_id"] if is_home else game["home_team_id"]

                payload.append(
                    {
                        "player_id": player_id,
                        "game_id": game["id"],
                        "team_id": team_id,
                        "opponent_team_id": opponent_id,
                        "season": ctx.season,
                        "week": game["week"],
                        "position_group": ctx.position_for(player_id),
                        "is_home": is_home,
                        "pass_attempts": smallint_or_none(row.get("pass_attempts")),
                        "pass_completions": smallint_or_none(row.get("pass_completions")),
                        "pass_yards": smallint_or_none(row.get("pass_yards")),
                        "pass_tds": smallint_or_none(row.get("pass_tds")),
                        "interceptions": smallint_or_none(row.get("interceptions")),
                        "rush_attempts": smallint_or_none(row.get("rush_attempts")),
                        "rush_yards": smallint_or_none(row.get("rush_yards")),
                        "rush_tds": smallint_or_none(row.get("rush_tds")),
                        "receptions": smallint_or_none(row.get("receptions")),
                        "rec_yards": smallint_or_none(row.get("rec_yards")),
                        "rec_tds": smallint_or_none(row.get("rec_tds")),
                    }
                )

    n = upsert(
        "player_game_stats", payload, conflict_columns=["player_id", "game_id"]
    )
    counts.add("player_game_stats", n)
    parser.report()
    log.info("player_game_stats %d: %d rows", ctx.season, n)


# -----------------------------------------------------------------------------
# plays
# -----------------------------------------------------------------------------
def _clock_seconds(clock: Any) -> int | None:
    """CFBD sends clock as {'minutes': m, 'seconds': s}."""
    if not isinstance(clock, dict):
        return None
    minutes = clock.get("minutes")
    seconds = clock.get("seconds")
    if minutes is None and seconds is None:
        return None
    return int(minutes or 0) * 60 + int(seconds or 0)


def ingest_plays(
    client: CfbdClient, ctx: SeasonContext, counts: StatsCounts
) -> None:
    rows_out: list[tuple[Any, ...]] = []

    for season_type, week in week_slices(ctx.season):
        plays = client.fetch(
            "/plays", cfbd.PlaysApi, "get_plays",
            year=ctx.season, week=week, season_type=season_type,
            classification="fbs", max_age=IMMUTABLE,
        )

        for p in plays:
            game = ctx.games_by_cfbd.get(bigint_or_none(p.get("gameId")))
            if game is None:
                counts.skip("play: unknown game")
                continue

            rows_out.append(
                (
                    bigint_or_none(p.get("id")),
                    game["id"],
                    ctx.season,
                    game["week"],
                    ctx.team_id_by_school.get(p.get("offense")),
                    ctx.team_id_by_school.get(p.get("defense")),
                    smallint_or_none(p.get("period")),
                    _clock_seconds(p.get("clock")),
                    smallint_or_none(p.get("down")),
                    smallint_or_none(p.get("distance")),
                    smallint_or_none(p.get("yardsToGoal")),
                    smallint_or_none(p.get("yardsGained")),
                    p.get("playType"),
                    p.get("playText"),
                    bool(p.get("scoring")),
                    p.get("ppa"),
                    smallint_or_none(p.get("offenseScore")),
                    smallint_or_none(p.get("defenseScore")),
                )
            )

    # Delete-then-COPY makes the re-run exactly idempotent without upsert cost.
    deleted = execute("delete from plays where season = %s", (ctx.season,))
    if deleted:
        log.info("plays %d: cleared %d existing rows", ctx.season, deleted)

    n = copy_into("plays", PLAY_COLUMNS, rows_out)
    counts.add("plays", n)
    log.info("plays %d: %d rows", ctx.season, n)


# -----------------------------------------------------------------------------
# play_player_stats
# -----------------------------------------------------------------------------
def ingest_play_player_stats(
    client: CfbdClient, ctx: SeasonContext, counts: StatsCounts
) -> None:
    """Per-GAME fetch. See the module docstring: week-level responses truncate."""
    play_id_by_cfbd = {
        r["cfbd_id"]: r["id"]
        for r in fetch_all(
            "select id, cfbd_id from plays where season = %s", (ctx.season,)
        )
    }
    log.info(
        "play_player_stats %d: resolving against %d plays",
        ctx.season, len(play_id_by_cfbd),
    )

    rows_out: list[tuple[Any, ...]] = []
    seen: set[tuple[int, int, str]] = set()
    games = sorted(
        ctx.games_by_cfbd.values(), key=lambda g: (g["week"], g["cfbd_id"])
    )

    for i, game in enumerate(games, start=1):
        stats = client.fetch(
            "/plays/stats", cfbd.PlaysApi, "get_play_stats",
            year=ctx.season, game_id=game["cfbd_id"], max_age=IMMUTABLE,
        )

        if len(stats) >= 2000:
            # Should be unreachable per-game, but if a game ever hits the cap we
            # must know rather than quietly lose the tail.
            log.error(
                "play_player_stats: game %s returned %d rows — at or above the "
                "2000-row cap, data is likely truncated",
                game["cfbd_id"], len(stats),
            )

        for s in stats:
            play_id = play_id_by_cfbd.get(bigint_or_none(s.get("playId")))
            player_id = ctx.player_id_by_athlete.get(
                bigint_or_none(s.get("athleteId"))
            )
            stat_type = s.get("statType")

            if play_id is None:
                counts.skip("attribution: unknown play")
                continue
            if player_id is None:
                counts.skip("attribution: player not in roster")
                continue
            if not stat_type:
                counts.skip("attribution: missing statType")
                continue

            team_id = ctx.team_id_by_school.get(s.get("team"))
            opponent_id = ctx.team_id_by_school.get(s.get("opponent"))
            if team_id is None or opponent_id is None:
                counts.skip("attribution: unknown team")
                continue

            # unique (play_id, player_id, stat_type); COPY has no ON CONFLICT,
            # so duplicates must be filtered before they reach Postgres.
            key = (play_id, player_id, stat_type)
            if key in seen:
                counts.skip("attribution: duplicate key")
                continue
            seen.add(key)

            rows_out.append(
                (
                    play_id,
                    game["id"],
                    player_id,
                    team_id,
                    opponent_id,
                    ctx.season,
                    game["week"],
                    ctx.position_for(player_id),
                    stat_type,
                    s.get("stat"),
                )
            )

        if i % 200 == 0:
            log.info(
                "play_player_stats %d: %d/%d games, %d rows staged",
                ctx.season, i, len(games), len(rows_out),
            )

    deleted = execute(
        "delete from play_player_stats where season = %s", (ctx.season,)
    )
    if deleted:
        log.info(
            "play_player_stats %d: cleared %d existing rows", ctx.season, deleted
        )

    n = copy_into("play_player_stats", PLAY_PLAYER_COLUMNS, rows_out)
    counts.add("play_player_stats", n)
    log.info("play_player_stats %d: %d rows", ctx.season, n)


# -----------------------------------------------------------------------------
# targets backfill
# -----------------------------------------------------------------------------
def backfill_targets(season: int) -> int:
    """Populate player_game_stats.targets from attribution data.

    CFBD box scores do not serve targets, so they are reconstructed here.

    **A raw count of the 'Target' statType is NOT the target count.** CFBD emits
    'Target' only on INCOMPLETE passes: across all of 2024 every one of the 3,616
    Target rows sits on a play_type of 'Pass Incompletion', and none on a
    completion. A caught pass is recorded as 'Reception' instead.

    So a target is a reception *or* an incompletion thrown at the player:

        targets = receptions + count('Target')

    Verified against a receiver-game: Dante Wright had 14 receptions and 5
    'Target' rows, i.e. 19 targets. Using the raw count alone would have
    reported 5 and understated receiving volume by roughly 80% — silently, and
    in the denominator the receptions market depends on.

    `receptions` comes from the official box score rather than from attribution
    rows, because the box score is authoritative and attribution occasionally
    runs a catch or two short.

    Rows with no receiving involvement at all are left NULL rather than zeroed,
    so "no targets" stays distinguishable from "not a receiver".
    """
    return execute(
        """
        update player_game_stats pgs
           set targets = coalesce(pgs.receptions, 0) + coalesce(tg.n, 0)
          from (
                select p.id as pgs_id, t.n
                  from player_game_stats p
                  left join (
                        select player_id, game_id, count(*) as n
                          from play_player_stats
                         where season = %(season)s and stat_type = 'Target'
                         group by player_id, game_id
                       ) t
                    on t.player_id = p.player_id and t.game_id = p.game_id
                 where p.season = %(season)s
                   and (p.receptions is not null or t.n is not null)
               ) tg
         where pgs.id = tg.pgs_id
        """,
        {"season": season},  # type: ignore[arg-type]
    )


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------
def estimate_calls(season: int) -> int:
    """Per season: 1 call per week slice for plays and box scores, 1 per game."""
    slices = len(week_slices(season))
    games = len(fetch_all("select id from games where season = %s", (season,)))
    return slices * 2 + games


def run_stats_ingest(client: CfbdClient, season: int) -> StatsCounts:
    counts = StatsCounts()
    log.info("--- season %d: building id maps ---", season)
    ctx = SeasonContext.build(season)
    log.info(
        "  %d games, %d teams, %d players, %d season positions",
        len(ctx.games_by_cfbd), len(ctx.team_id_by_school),
        len(ctx.player_id_by_athlete), len(ctx.position_by_player),
    )

    ingest_player_game_stats(client, ctx, counts)
    ingest_plays(client, ctx, counts)
    ingest_play_player_stats(client, ctx, counts)

    n = backfill_targets(season)
    log.info("targets backfilled onto %d player_game_stats rows", n)

    if counts.skipped:
        log.info(
            "skipped rows by reason: %s",
            ", ".join(f"{k}={v:,}" for k, v in sorted(counts.skipped.items())),
        )
    return counts
