"""Team strength, point in time — the game model's features (CLAUDE.md §11, G2).

SPORT-AGNOSTIC CORE, NOT SPORT-BLIND. Reads `plays` and `games`; `plays` has no
sport column and holds NFL rows from 2025, so every read joins `games.sport`.
The play-type vocabulary below is CFBD's, which makes the scrimmage filter the
one college-specific piece; the NFL adapter would pass its own.

For each week N of a season, fits an additive model over every completed
game-side with week < N:

    value = league mean + offense effect + defense effect

and stores `league mean + effect` for each team's offense and defense. That is
the splits engine's fit (core/splits.py `_fit_additive`), reused rather than
re-derived, on four metrics: points, plays (pace), PPA per play and success
rate. See migration 0077 for what each column means and why nothing here is
shrunk.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from worker.core.splits import Observation, _fit_additive
from worker.db import execute, fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

# CFBD play types that are a snap from scrimmage by the offense. Everything
# else — kickoffs, punts, field goals, penalties, timeouts, period markers — is
# not an offensive play and would dilute per-play efficiency.
CFBD_SCRIMMAGE_PLAY_TYPES = (
    "Rush",
    "Rushing Touchdown",
    "Pass Reception",
    "Pass Completion",
    "Pass Incompletion",
    "Passing Touchdown",
    "Sack",
    "Pass Interception Return",
    "Interception",
    "Interception Return Touchdown",
    "Fumble Recovery (Own)",
    "Fumble Recovery (Opponent)",
    "Fumble Return Touchdown",
    "Fumble",
    "Safety",
)

# Garbage time: the margin above which a quarter's snaps say little about a
# team's real strength (the thresholds commonly used in college analytics).
# The first quarter is never garbage time.
GARBAGE_MARGIN_BY_PERIOD = {2: 38, 3: 28, 4: 22}

# A side with fewer scrimmage snaps than this in a game carries no efficiency
# observation: the play feed is incomplete for that game, and a 12-play sample
# would be fitted as if it were a whole game.
MIN_SCRIMMAGE_PLAYS = 30

METRICS = ("points", "plays", "ppa", "success")


def is_success(down: int | None, distance: float | None, gained: float | None) -> bool | None:
    """Standard success: 50% of the distance on 1st down, 70% on 2nd, all of it on 3rd/4th."""
    if down is None or distance is None or gained is None or distance <= 0:
        return None
    need = {1: 0.5, 2: 0.7}.get(down, 1.0) * distance
    return gained >= need


def is_garbage_time(period: int | None, margin: int | None) -> bool:
    if period is None or margin is None:
        return False
    threshold = GARBAGE_MARGIN_BY_PERIOD.get(period)
    # Overtime (period 5+) is never garbage time: the game is tied by definition.
    return threshold is not None and abs(margin) > threshold


# The success rule and garbage filter, in SQL, over one sport's completed games.
# THE SAME RULES AS THE TWO PYTHON FUNCTIONS ABOVE, restated because this runs
# over ~160k plays a season. The Python pair is what the tests pin; if either
# side changes, change the other. The build's sanity check is the league
# success rate, which lands near 0.42 for college when the two agree.
_SIDE_SQL = """
with scrimmage as (
  select
    p.game_id,
    p.offense_team_id,
    p.defense_team_id,
    p.ppa,
    case
      when p.down is null or p.distance is null or p.distance <= 0
        or p.yards_gained is null then null
      when p.yards_gained >= (case p.down when 1 then 0.5 when 2 then 0.7 else 1.0 end)
                             * p.distance then 1.0
      else 0.0
    end as success,
    case
      when p.period = 2 then abs(p.offense_score - p.defense_score) > 38
      when p.period = 3 then abs(p.offense_score - p.defense_score) > 28
      when p.period = 4 then abs(p.offense_score - p.defense_score) > 22
      else false
    end as garbage
  from plays p
  join games g on g.id = p.game_id
  where g.sport = %(sport)s
    and g.season = %(season)s
    and g.completed
    and p.play_type = any(%(types)s)
)
select
  game_id,
  offense_team_id,
  defense_team_id,
  count(*)                                         as plays,
  avg(ppa)     filter (where not coalesce(garbage, false)) as ppa,
  avg(success) filter (where not coalesce(garbage, false)) as success
from scrimmage
group by game_id, offense_team_id, defense_team_id
"""


@dataclass(frozen=True)
class GameSide:
    game_id: int
    week: int
    offense_id: int
    defense_id: int
    values: dict[str, float]


def load_game_sides(season: int, sport: str = "cfb") -> list[GameSide]:
    """One observation per team per completed game: its offense against the opponent's defense.

    Points come from the final score, so a game with a thin play feed still
    counts for points; plays, PPA and success only where the feed has at least
    MIN_SCRIMMAGE_PLAYS snaps for that side.
    """
    games = fetch_all(
        """
        select id, week, home_team_id, away_team_id, home_points, away_points
          from games
         where sport = %s and season = %s and completed
           and home_points is not null and away_points is not null
        """,
        (sport, season),
    )
    efficiency = {
        (r["game_id"], r["offense_team_id"]): r
        for r in fetch_all(
            _SIDE_SQL,
            {"sport": sport, "season": season, "types": list(CFBD_SCRIMMAGE_PLAY_TYPES)},
        )
    }

    sides: list[GameSide] = []
    for g in games:
        for offense, defense, points in (
            (g["home_team_id"], g["away_team_id"], g["home_points"]),
            (g["away_team_id"], g["home_team_id"], g["away_points"]),
        ):
            values: dict[str, float] = {"points": float(points)}
            eff = efficiency.get((g["id"], offense))
            if eff is not None and eff["plays"] >= MIN_SCRIMMAGE_PLAYS:
                values["plays"] = float(eff["plays"])
                if eff["ppa"] is not None:
                    values["ppa"] = float(eff["ppa"])
                if eff["success"] is not None:
                    values["success"] = float(eff["success"])
            sides.append(
                GameSide(
                    game_id=g["id"],
                    week=g["week"],
                    offense_id=offense,
                    defense_id=defense,
                    values=values,
                )
            )
    return sides


def ratings_as_of(sides: list[GameSide], season: int, as_of_week: int) -> list[dict]:
    """Every team's adjusted offense and defense, from games with week < as_of_week ONLY.

    The cutoff is applied HERE, on the observations, before any fitting: a
    fit that saw a later game cannot be un-seen by filtering its output.
    """
    before = [s for s in sides if s.week < as_of_week]
    if not before:
        return []

    observations = [
        Observation(
            defense_id=s.defense_id, offense_id=s.offense_id, week=s.week, values=s.values
        )
        for s in before
    ]
    games_by_team: dict[int, set[int]] = defaultdict(set)
    for s in before:
        games_by_team[s.offense_id].add(s.game_id)
        games_by_team[s.defense_id].add(s.game_id)

    fits = {metric: _fit_additive(observations, metric) for metric in METRICS}

    rows = []
    for team_id, games in games_by_team.items():
        row: dict = {
            "team_id": team_id,
            "season": season,
            "as_of_week": as_of_week,
            "games_included": len(games),
        }
        for metric, (mean, defense_effect, offense_effect) in fits.items():
            name = {"points": "points_pg", "plays": "plays_pg"}.get(metric, metric)
            has_off = team_id in offense_effect
            has_def = team_id in defense_effect
            row[f"off_{name}"] = mean + offense_effect[team_id] if has_off else None
            row[f"def_{name}"] = mean + defense_effect[team_id] if has_def else None
            row[f"league_{metric}"] = mean
        rows.append(row)
    return rows


def build_season(season: int, sport: str = "cfb") -> int:
    """Rebuild every week of one season. Returns rows written.

    Deletes the season's rows first so a re-run never leaves a stale week
    behind (a week that no longer has games after a schedule correction).
    """
    sides = load_game_sides(season, sport)
    if not sides:
        log.warning("team strength %s %d: no completed games", sport, season)
        return 0
    last_week = max(s.week for s in sides)

    rows: list[dict] = []
    for as_of_week in range(2, last_week + 2):
        rows.extend(ratings_as_of(sides, season, as_of_week))

    execute(
        """
        delete from team_strength_ratings r
         using teams t
         where t.id = r.team_id and t.sport = %s and r.season = %s
        """,
        (sport, season),
    )
    n = upsert(
        "team_strength_ratings",
        rows,
        conflict_columns=["team_id", "season", "as_of_week"],
    )
    log.info(
        "team strength %s %d: %d game-sides, weeks 2-%d, %d rows",
        sport, season, len(sides), last_week + 1, n,
    )
    return n
