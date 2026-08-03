"""The position-split engine — what each defense allows to each position.

SPORT-AGNOSTIC CORE (CLAUDE.md §3). This reads `play_player_stats` and `plays`,
both of which the NFL adapter will populate with the same shape, so nothing here
is college-specific. The CFBD ingest is upstream of it.

This is the primary defensive signal (CLAUDE.md §5). No provider serves "what
this defense allows to RBs", so it is built here in two stages that the schema
deliberately keeps apart:

  1. `defense_position_game_splits` — an OBSERVATION. What one defense allowed
     to one position in one game. True forever, needs no knowledge cutoff.
  2. `defense_position_ratings` — an INFERENCE. The opponent-ADJUSTED figure,
     fitted across games and therefore only meaningful relative to a cutoff, so
     every row carries `as_of_week` and is fitted on `week < as_of_week` only.

Stage 1 runs in SQL because it aggregates ~345k attribution rows; stage 2 runs
in Python because it is an iterative fit.

WHY ADJUSTMENT IS NOT OPTIONAL
------------------------------
College schedules are wildly unbalanced. A defense that played three
run-averse offenses looks elite on raw numbers, and a defense that faced Boise
State with Ashton Jeanty looks porous. Feeding raw splits into a projection
would systematically misprice exactly the matchups the product exists to call.

WHAT THE ADJUSTMENT CANNOT DO
-----------------------------
The fit separates defense from offence only where schedules OVERLAP. If two
groups of teams never meet, no additive model can tell "these defenses are bad"
from "those offences are good" — the information is not in the data, and the fit
will report a confident, spurious gap.

That is not hypothetical here. At `as_of_week = 2` each defense has ~1.5 games
and the schedule graph is barely connected, so early-season adjusted ratings are
the least trustworthy ones the table contains. Shrinkage toward the league mean
(`shrinkage_weight`, surfaced on every row) is the mitigation, and it is also
why CLAUDE.md §6 asks for visibly wider uncertainty early in the season. By
mid-season, cross-conference play has connected the graph well enough for the
adjustment to carry real information.

RUSHING EXCLUDES SACKS
----------------------
Rushing here comes from attribution ('Rush' rows), which excludes sacks, rather
than from box-score rush_yards, which includes them because NCAA charges sacks
as rushing losses. A sack is a pass-rush outcome and says little about defending
the run, so counting it as "rush yards allowed" would distort the run-defense
signal — most severely for defenses that face pass-heavy offenses. See
adapters/cfbd/ingest_stats.py for the measurement behind this.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from worker.db import execute, fetch_all, upsert
from worker.logging_setup import get_logger

log = get_logger(__name__)

# Positions we model markets for (CLAUDE.md §6). Splits for OL/DL/LB/DB would be
# storage without a consumer.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Conventional explosive-play thresholds. Not in CLAUDE.md §9's open list, so
# they live here as named constants rather than runtime config — but they are
# conventions, not physics, and are worth revisiting against the data in Phase 3.
EXPLOSIVE_RUSH_YARDS = 10
EXPLOSIVE_REC_YARDS = 15

# Identifies the adjustment method. Part of the unique key on
# defense_position_ratings, so a revised method can be computed alongside this
# one and compared rather than silently overwriting history.
ADJUSTMENT_VERSION = "v1_iterative_additive"

# Iterations of the alternating fit. The estimates move very little after ~5;
# 10 is cheap insurance.
FIT_ITERATIONS = 10

# Shrinkage constant: a defense with k games gets half its raw deviation. Early
# season, one blowout should not make a defense look historically bad.
SHRINKAGE_GAMES = 4.0

# The metric each position's rank_vs_position orders by — the stat that position
# is actually measured on.
#
# STATED AS A TABLE RATHER THAN A RULE. This was previously the ternary "rushing
# if RB, else receiving", which silently ranked QB defenses by receiving yards
# allowed to quarterbacks: a stat averaging 0.56 yards a game, negative for 22 of
# 136 defenses after adjustment, and uncorrelated (r = -0.06) with the rushing
# figure that is the only real QB position split. The resulting "softest vs QB"
# list was noise — its top team allowed BELOW-average QB rushing. A rule with one
# exception invites a second position to inherit the wrong branch by default; a
# table has to be answered for every position that is added.
#
# QB IS RUSHING BY NECESSITY, NOT PREFERENCE. The position split disaggregates a
# defense by who it conceded to, and since the quarterback is the only passer,
# "pass yards allowed to QBs" is just team pass defense. Rushing is the only
# genuine QB split there is, so a QB rank means "softest vs QB RUSHING" and every
# surface showing it must say so.
RANK_METRICS = {
    "QB": "adj_rush_yards_allowed_pg",
    "RB": "adj_rush_yards_allowed_pg",
    "WR": "adj_rec_yards_allowed_pg",
    "TE": "adj_rec_yards_allowed_pg",
}

# Metrics that get opponent-adjusted, mapped to their split-table source column.
ADJUSTED_METRICS = {
    "rush_yards_allowed": "rush_yards_allowed",
    "rec_yards_allowed": "rec_yards_allowed",
    "receptions_allowed": "receptions_allowed",
    "rush_tds_allowed": "rush_tds_allowed",
    "rec_tds_allowed": "rec_tds_allowed",
    "ppa_allowed": "ppa_allowed",
}


# -----------------------------------------------------------------------------
# Stage 1 — raw per-game splits
# -----------------------------------------------------------------------------
SPLIT_SQL = """
insert into defense_position_game_splits (
    game_id, defense_team_id, offense_team_id, season, week, position_group,
    plays, rush_attempts, rush_yards_allowed, rush_tds_allowed,
    targets, receptions_allowed, rec_yards_allowed, rec_tds_allowed,
    first_downs_allowed, explosive_plays_allowed,
    goal_line_carries_allowed, goal_line_targets_allowed, ppa_allowed
)
with per_play as (
    -- One row per (play, defense, position). Collapsing to the play first is
    -- what keeps a QB's Completion+Touchdown rows from counting as two plays,
    -- and lets ppa be summed once per play rather than once per stat row.
    select
        pps.play_id,
        pps.game_id,
        pps.opponent_team_id                                as defense_team_id,
        pps.team_id                                         as offense_team_id,
        pps.season,
        pps.week,
        pps.position_group,
        pl.yards_to_goal,
        pl.yards_gained,
        pl.distance,
        pl.ppa,
        count(*) filter (where pps.stat_type = 'Rush')      as rush_att,
        coalesce(sum(pps.stat_value)
                 filter (where pps.stat_type = 'Rush'), 0)  as rush_yds,
        count(*) filter (where pps.stat_type = 'Reception') as receptions,
        coalesce(sum(pps.stat_value)
                 filter (where pps.stat_type = 'Reception'), 0) as rec_yds,
        -- 'Target' is emitted only on incompletions, so targets are
        -- receptions + these. See ingest_stats.backfill_targets.
        count(*) filter (where pps.stat_type = 'Target')    as inc_targets,
        bool_or(pps.stat_type = 'Touchdown')                as has_td,
        bool_or(pps.stat_type = 'Rush')                     as has_rush,
        bool_or(pps.stat_type = 'Reception')                as has_reception
      from play_player_stats pps
      join plays pl on pl.id = pps.play_id
     where pps.season = %(season)s
       and pps.position_group = any(%(positions)s)
     group by pps.play_id, pps.game_id, pps.opponent_team_id, pps.team_id,
              pps.season, pps.week, pps.position_group,
              pl.yards_to_goal, pl.yards_gained, pl.distance, pl.ppa
)
select
    game_id, defense_team_id, offense_team_id, season, week, position_group,
    count(*)                                                as plays,
    sum(rush_att)                                           as rush_attempts,
    sum(rush_yds)                                           as rush_yards_allowed,
    -- A touchdown belongs to rushing or receiving according to what the SAME
    -- player did on the SAME play. Both the passer and the receiver get a
    -- 'Touchdown' row, so this is what keeps a passing TD from also counting
    -- as a receiving TD.
    count(*) filter (where has_td and has_rush)             as rush_tds_allowed,
    sum(receptions) + sum(inc_targets)                      as targets,
    sum(receptions)                                         as receptions_allowed,
    sum(rec_yds)                                            as rec_yards_allowed,
    count(*) filter (where has_td and has_reception)        as rec_tds_allowed,
    count(*) filter (
        where (has_rush or has_reception)
          and yards_gained is not null and distance is not null
          and yards_gained >= distance
    )                                                       as first_downs_allowed,
    count(*) filter (
        where (has_rush and rush_yds >= %(explosive_rush)s)
           or (has_reception and rec_yds >= %(explosive_rec)s)
    )                                                       as explosive_plays_allowed,
    coalesce(sum(rush_att) filter (
        where yards_to_goal <= %(goal_line)s), 0)           as goal_line_carries_allowed,
    coalesce(sum(receptions) filter (
        where yards_to_goal <= %(goal_line)s), 0)
      + coalesce(sum(inc_targets) filter (
        where yards_to_goal <= %(goal_line)s), 0)           as goal_line_targets_allowed,
    sum(ppa)                                                as ppa_allowed
  from per_play
 group by game_id, defense_team_id, offense_team_id, season, week, position_group
on conflict (game_id, defense_team_id, position_group) do update set
    plays = excluded.plays,
    rush_attempts = excluded.rush_attempts,
    rush_yards_allowed = excluded.rush_yards_allowed,
    rush_tds_allowed = excluded.rush_tds_allowed,
    targets = excluded.targets,
    receptions_allowed = excluded.receptions_allowed,
    rec_yards_allowed = excluded.rec_yards_allowed,
    rec_tds_allowed = excluded.rec_tds_allowed,
    first_downs_allowed = excluded.first_downs_allowed,
    explosive_plays_allowed = excluded.explosive_plays_allowed,
    goal_line_carries_allowed = excluded.goal_line_carries_allowed,
    goal_line_targets_allowed = excluded.goal_line_targets_allowed,
    ppa_allowed = excluded.ppa_allowed,
    computed_at = now()
"""


def compute_game_splits(season: int, goal_line_yards: int = 10) -> int:
    """Build raw per-game defensive splits for a season. Returns rows written."""
    n = execute(
        SPLIT_SQL,
        {
            "season": season,
            "positions": list(SKILL_POSITIONS),
            "explosive_rush": EXPLOSIVE_RUSH_YARDS,
            "explosive_rec": EXPLOSIVE_REC_YARDS,
            "goal_line": goal_line_yards,
        },
    )
    log.info("defense_position_game_splits %d: %d rows", season, n)
    return n


# -----------------------------------------------------------------------------
# Stage 2 — opponent adjustment, point-in-time
# -----------------------------------------------------------------------------
@dataclass
class Observation:
    defense_id: int
    offense_id: int
    week: int
    values: dict[str, float]


def _fit_additive(
    observations: list[Observation], metric: str
) -> tuple[float, dict[int, float], dict[int, float]]:
    """Fit allowed = league_mean + defense_effect + offense_effect.

    Alternating means rather than least squares: it needs no new dependency, is
    inspectable, and converges quickly on a two-way additive model this small.
    The defense effect is what "opponent-adjusted" means here — how much a
    defense allows ABOVE what the offences it faced generate against everyone.
    """
    values = [o.values[metric] for o in observations if metric in o.values]
    if not values:
        return 0.0, {}, {}

    league_mean = sum(values) / len(values)

    defense_effect: dict[int, float] = defaultdict(float)
    offense_effect: dict[int, float] = defaultdict(float)

    for _ in range(FIT_ITERATIONS):
        # Defense effects, holding offence effects fixed.
        sums: dict[int, float] = defaultdict(float)
        counts: dict[int, int] = defaultdict(int)
        for o in observations:
            if metric not in o.values:
                continue
            residual = o.values[metric] - league_mean - offense_effect[o.offense_id]
            sums[o.defense_id] += residual
            counts[o.defense_id] += 1
        defense_effect = defaultdict(
            float, {k: sums[k] / counts[k] for k in sums if counts[k]}
        )

        # Offence effects, holding defence effects fixed.
        sums.clear()
        counts.clear()
        for o in observations:
            if metric not in o.values:
                continue
            residual = o.values[metric] - league_mean - defense_effect[o.defense_id]
            sums[o.offense_id] += residual
            counts[o.offense_id] += 1
        offense_effect = defaultdict(
            float, {k: sums[k] / counts[k] for k in sums if counts[k]}
        )

    return league_mean, dict(defense_effect), dict(offense_effect)


def _load_observations(season: int) -> dict[str, list[Observation]]:
    """Read raw splits, grouped by position."""
    rows = fetch_all(
        """
        select defense_team_id, offense_team_id, week, position_group,
               rush_yards_allowed, rec_yards_allowed, receptions_allowed,
               rush_tds_allowed, rec_tds_allowed, ppa_allowed
          from defense_position_game_splits
         where season = %s
        """,
        (season,),
    )

    by_position: dict[str, list[Observation]] = defaultdict(list)
    for r in rows:
        values = {
            metric: float(r[column])
            for metric, column in ADJUSTED_METRICS.items()
            if r.get(column) is not None
        }
        by_position[r["position_group"]].append(
            Observation(
                defense_id=r["defense_team_id"],
                offense_id=r["offense_team_id"],
                week=r["week"],
                values=values,
            )
        )
    return by_position


def _fbs_team_ids(season: int) -> set[int]:
    return {
        r["team_id"]
        for r in fetch_all(
            """
            select team_id from team_seasons
             where season = %s and classification = 'fbs'
            """,
            (season,),
        )
    }


def compute_ratings(season: int, max_week: int | None = None) -> int:
    """Fit opponent-adjusted ratings for every (position, as_of_week).

    One fit per cutoff. A rating at as_of_week = N is fitted ONLY on games with
    week < N, which is what makes it usable as a week-N feature — the strict
    inequality is the entire point and is asserted in the tests.
    """
    by_position = _load_observations(season)
    if not by_position:
        log.warning("No splits found for %d; run compute_game_splits first.", season)
        return 0

    fbs = _fbs_team_ids(season)
    weeks = sorted({o.week for obs in by_position.values() for o in obs})
    if not weeks:
        return 0
    cutoffs = range(2, (max_week or max(weeks)) + 2)

    payload: list[dict[str, Any]] = []

    for position, observations in by_position.items():
        for as_of_week in cutoffs:
            # THE CUTOFF. Strictly earlier weeks only.
            prior = [o for o in observations if o.week < as_of_week]
            if not prior:
                continue

            games_by_defense: dict[int, int] = defaultdict(int)
            for o in prior:
                games_by_defense[o.defense_id] += 1

            fits = {
                metric: _fit_additive(prior, metric) for metric in ADJUSTED_METRICS
            }

            raw_totals: dict[int, dict[str, float]] = defaultdict(
                lambda: defaultdict(float)
            )
            for o in prior:
                for metric, value in o.values.items():
                    raw_totals[o.defense_id][metric] += value

            rows_this_cut: list[dict[str, Any]] = []
            for defense_id, n_games in games_by_defense.items():
                if defense_id not in fbs:
                    # Splits are computed for every defense so FBS-vs-FCS games
                    # are not lost, but ranking an FCS defense against FBS ones
                    # would be meaningless.
                    continue

                shrink = n_games / (n_games + SHRINKAGE_GAMES)

                row: dict[str, Any] = {
                    "defense_team_id": defense_id,
                    "season": season,
                    "as_of_week": as_of_week,
                    "position_group": position,
                    "adjustment_version": ADJUSTMENT_VERSION,
                    "games_included": n_games,
                    "shrinkage_weight": round(shrink, 4),
                }

                for metric, (league_mean, defense_effect, _) in fits.items():
                    raw_pg = raw_totals[defense_id].get(metric, 0.0) / n_games
                    effect = defense_effect.get(defense_id, 0.0) * shrink
                    adjusted = league_mean + effect

                    if metric == "ppa_allowed":
                        row["adj_ppa_allowed"] = round(adjusted, 4)
                    else:
                        row[f"{metric}_pg"] = round(raw_pg, 3)
                        row[f"adj_{metric}_pg"] = round(adjusted, 3)

                rows_this_cut.append(row)

            # rank_vs_position: 1 = allows the LEAST, i.e. the BEST defense.
            #
            # This is the conventional reading, and it is conventional for a
            # reason: everyone who looks at a defensive ranking already knows
            # what "ranked 3rd" means. The earlier orientation (1 = allows the
            # most) was chosen because the product's headline use is a
            # "who to target" list, but it cost more than it saved — a reader
            # has to be told the convention before any rank means anything, and
            # Gemini, given the convention in capitals immediately above the
            # number, still described rank 118 of 136 as "a favorable matchup".
            # A convention that has to be explained every time it appears is
            # the wrong convention.
            #
            # Consumers that want the SOFTEST defenses now sort DESCENDING:
            # the weekly targets list and the board's matchup sort both do.
            #
            # Indexing RANK_METRICS raises KeyError for an unmapped position,
            # deliberately: a new position group must state what it ranks on
            # rather than silently inherit somebody else's metric.
            rank_metric = RANK_METRICS[position]
            rows_this_cut.sort(key=lambda r, m=rank_metric: r.get(m) or 0.0)
            for rank, row in enumerate(rows_this_cut, start=1):
                row["rank_vs_position"] = rank

            payload.extend(rows_this_cut)

    # REPLACE, NOT MERGE. An upsert alone leaves orphans: a cell that used to be
    # rated and no longer is keeps its old row forever, because nothing writes
    # over it. That is not hypothetical — correcting the postseason week axis
    # (migration 0020) removed a team's only prior "game" at as_of_week = 2, the
    # engine correctly declined to rate it, and the contaminated row from the
    # previous run survived the rebuild with games_included = 1 against zero
    # games. Ratings are derived, so a rebuild for a season has to be the whole
    # truth for that season and version.
    stale = execute(
        "delete from defense_position_ratings "
        " where season = %s and adjustment_version = %s",
        (season, ADJUSTMENT_VERSION),
    )
    if stale:
        log.info("defense_position_ratings %d: cleared %d prior rows", season, stale)

    n = upsert(
        "defense_position_ratings",
        payload,
        conflict_columns=[
            "defense_team_id", "season", "as_of_week",
            "position_group", "adjustment_version",
        ],
    )
    log.info(
        "defense_position_ratings %d: %d rows across %d cutoffs",
        season, n, len(list(cutoffs)),
    )
    return n


def run_split_engine(season: int, goal_line_yards: int = 10) -> dict[str, int]:
    counts = {
        "defense_position_game_splits": compute_game_splits(season, goal_line_yards),
    }
    counts["defense_position_ratings"] = compute_ratings(season)
    return counts


__all__ = [
    "ADJUSTMENT_VERSION",
    "RANK_METRICS",
    "SKILL_POSITIONS",
    "compute_game_splits",
    "compute_ratings",
    "run_split_engine",
]
