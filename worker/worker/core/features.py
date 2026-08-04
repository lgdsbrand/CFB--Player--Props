"""Point-in-time feature assembly.

SPORT-AGNOSTIC CORE (CLAUDE.md §3). Reads only tables the NFL adapter will
populate with the same shape.

THIS MODULE'S ENTIRE JOB IS THE KNOWLEDGE CUTOFF
------------------------------------------------
Every other part of Phase 3 fails loudly when it is wrong: a bad distribution
family shows up in the fit diagnostics, a bad projection shows up in the
calibration curve. Lookahead does the opposite — it produces a model that looks
*excellent* and is worthless. CLAUDE.md §4 calls it silent and disqualifying,
and it would invalidate the calibration report that is the client review gate.

So the cutoff is enforced three ways, deliberately overlapping:

  1. **One type carries it.** Every query takes an `AsOf`, never a bare week.
     There is no function here that reads a feature without stating its cutoff.
  2. **Every aggregate reports its own high-water mark.** Each query selects
     `max(week) as max_source_week`, and `_assert_no_lookahead` checks it before
     the rows are returned. This runs in production, not only under test — an
     off-by-one raises `LookaheadError` rather than quietly producing a
     flattering number.
  3. **Tests re-derive the bound independently**, in tests/test_features.py.

The database provides a fourth layer: `backtest_predictions` has a CHECK that
`as_of_week <= week`.

WHAT "AS OF WEEK N" MEANS
------------------------
`AsOf(season, week=N)` describes a prediction made ENTERING week N. It may read:

  * same-season games with `week < N` — strictly less, never `<=`,
  * the whole of any PRIOR season, which is complete and known,
  * `defense_position_ratings` and `team_rating_snapshots` rows whose
    `as_of_week` equals N, since those tables define that column as "fitted on
    week < as_of_week" and are append-only,
  * the week-N schedule itself — knowing who plays whom is not lookahead.

It may NOT read week-N results. `upcoming_slate` therefore does not select
`home_points`, `away_points` or `completed`, so a week-N score cannot reach a
feature even by accident.

THE ONE HONEST CAVEAT: WEATHER
------------------------------
`game_weather` holds OBSERVED conditions. Entering week N you would have had a
forecast, not an observation. Using observations in a backtest therefore grants
the model slightly more than was knowable, and the bias runs in the flattering
direction. Temperature and wind forecast well a day or two out so the effect is
small, but it is not zero and it is not symmetric.

`include_weather` defaults to True because CLAUDE.md §4 and §7 both call for
weather, and it is exposed as a switch so the calibration report can MEASURE the
difference rather than assert it is negligible. Whatever that measurement shows
belongs in the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import polars as pl

from worker.db import fetch_all
from worker.logging_setup import get_logger

log = get_logger(__name__)

# Positions we model markets for (CLAUDE.md §6).
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Box-score columns rolled into per-player usage features. Whitelisted rather
# than discovered, because these names are interpolated into SQL.
USAGE_STAT_COLUMNS = (
    "pass_attempts",
    "pass_completions",
    "pass_yards",
    "pass_tds",
    "interceptions",
    "rush_attempts",
    "rush_yards",
    "rush_tds",
    "targets",
    "receptions",
    "rec_yards",
    "rec_tds",
    "offensive_tds",
    "snaps",
)

# Metrics pivoted into the opponent's full defensive profile, so a row can read
# what its opponent allows to ANY position rather than only to its own. A QB's
# passing markets depend on what the defense concedes to WRs and TEs; without
# this those markets would have no opponent adjustment at all.
DEFENSE_PROFILE_METRICS = (
    "adj_rush_yards_allowed_pg",
    "adj_rec_yards_allowed_pg",
    "adj_receptions_allowed_pg",
    "adj_rush_tds_allowed_pg",
    "adj_rec_tds_allowed_pg",
)

# Recency window for the "hot lately" features. Five matches the L5 hit-rate
# window the board already offers (app_config.hit_rate_windows), so the number a
# user sees and the number the model uses describe the same span.
RECENT_GAMES_WINDOW = 5

# Prior-season weight decays as k/(k+n) with n games played this season. k is a
# "games equivalent" for how much the prior season is worth: at k=4 the prior
# carries its full ceiling in week 1, half of it after 4 games, a third after 8.
#
# Transfer portal and NIL churn are why the ceiling exists at all (CLAUDE.md §6)
# — prior-year production frequently happened at another school, against another
# conference, in another scheme.
PRIOR_GAMES_EQUIVALENT = 4.0

# Extra haircut when the player was on a DIFFERENT team last season. Their prior
# production is still informative about the player, but far less about their
# role: usage is a property of the depth chart they left behind.
CHANGED_TEAM_PRIOR_MULTIPLIER = 0.5


class LookaheadError(RuntimeError):
    """Raised when a feature query returned data from at or after its cutoff.

    This is never a recoverable condition. It means a predicate is wrong and
    every number computed downstream is contaminated.
    """


@dataclass(frozen=True)
class AsOf:
    """A knowledge cutoff: what was known entering `week` of `season`."""

    season: int
    week: int

    def __post_init__(self) -> None:
        if self.week < 1:
            raise ValueError(f"as-of week must be >= 1, got {self.week}")
        if self.season < 1900:
            raise ValueError(f"implausible season {self.season}")

    @property
    def prior_season(self) -> int:
        return self.season - 1

    def __str__(self) -> str:
        return f"{self.season}w{self.week}"


def _assert_no_lookahead(
    rows: list[dict[str, Any]],
    as_of: AsOf,
    label: str,
    column: str = "max_source_week",
) -> list[dict[str, Any]]:
    """Verify no row drew on data from `as_of.week` or later.

    Runs on every query, in production. The cost is one comparison per row
    against a value the aggregate already computed; the alternative is trusting
    that a `<` was never typed as a `<=`.
    """
    for row in rows:
        high_water = row.get(column)
        if high_water is None:
            continue
        if high_water >= as_of.week:
            raise LookaheadError(
                f"{label}: row drew on week {high_water} while predicting "
                f"{as_of} — features may only use week < {as_of.week}. "
                f"Offending row: { {k: v for k, v in list(row.items())[:6]} }"
            )
    return rows


def _assert_snapshot_cutoff(
    rows: list[dict[str, Any]], as_of: AsOf, label: str
) -> list[dict[str, Any]]:
    """Verify as-of-keyed snapshot rows carry exactly this cutoff.

    `defense_position_ratings` and `team_rating_snapshots` are append-only and
    already define `as_of_week` as "fitted on week < as_of_week". Reading a row
    with a LARGER as_of_week would import later knowledge; a smaller one is
    merely stale. Both are bugs, so this pins equality.
    """
    for row in rows:
        week = row.get("as_of_week")
        if week is not None and int(week) != as_of.week:
            raise LookaheadError(
                f"{label}: snapshot as_of_week={week} does not match the "
                f"requested cutoff {as_of.week}."
            )
    return rows


# -----------------------------------------------------------------------------
# The slate being predicted
# -----------------------------------------------------------------------------
def upcoming_slate(as_of: AsOf) -> list[dict[str, Any]]:
    """Games in the target week, with scheduling and venue context only.

    Knowing the fixture list is not lookahead. Knowing the SCORE is, which is
    why `home_points`, `away_points` and `completed` are not selected here — a
    column that is never read cannot leak.
    """
    return fetch_all(
        """
        select g.id            as game_id,
               g.season,
               g.week,
               g.start_date,
               g.neutral_site,
               g.conference_game,
               g.home_team_id,
               g.away_team_id,
               v.is_dome,
               v.elevation_m
          from games g
          left join venues v on v.id = g.venue_id
         where g.season = %(season)s
           and g.week   = %(week)s
         order by g.start_date nulls last, g.id
        """,
        {"season": as_of.season, "week": as_of.week},
    )


# -----------------------------------------------------------------------------
# Player usage — current season to date
# -----------------------------------------------------------------------------
def _usage_select_list() -> str:
    parts: list[str] = []
    for column in USAGE_STAT_COLUMNS:
        parts.append(f"avg({column}::numeric) as {column}_pg")
        parts.append(f"stddev_samp({column}::numeric) as {column}_sd")
        parts.append(
            f"avg({column}::numeric) filter (where recency_rank <= "
            f"{RECENT_GAMES_WINDOW}) as {column}_recent_pg"
        )
    return ",\n               ".join(parts)


def player_usage(as_of: AsOf) -> list[dict[str, Any]]:
    """Per-player rolling production from completed games this season.

    Returns season-to-date mean and standard deviation per stat, plus a
    recency-weighted mean over the last `RECENT_GAMES_WINDOW` games. The
    standard deviation matters as much as the mean here: the product's output is
    a distribution, and a player's game-to-game variance is a large part of it.
    """
    rows = fetch_all(
        f"""
        with played as (
          select s.*,
                 row_number() over (
                   partition by s.player_id order by s.week desc
                 ) as recency_rank
            from player_game_stats s
           where s.season = %(season)s
             and s.week   < %(week)s
             and s.position_group = any(%(positions)s::position_group[])
        )
        select player_id,
               (array_agg(team_id order by week desc))[1]       as team_id,
               (array_agg(position_group order by week desc))[1] as position_group,
               count(*)                                          as games_played,
               max(week)                                         as max_source_week,
               {_usage_select_list()}
          from played
         group by player_id
        """,
        {
            "season": as_of.season,
            "week": as_of.week,
            "positions": list(SKILL_POSITIONS),
        },
    )
    return _assert_no_lookahead(rows, as_of, "player_usage")


def roster_universe(as_of: AsOf) -> list[dict[str, Any]]:
    """Skill-position players on a roster this season, with the team they play for.

    WHY THE ROSTER AND NOT `player_game_stats`. `player_usage` can only see
    players who have already appeared, so entering week 1 it returns nothing and
    `build_feature_frame` has no player to project. The roster is the only source
    that answers "who is on this team" before a snap is played.

    NOT LOOKAHEAD, and checked rather than assumed. `/roster?year=N` describes
    squad membership for season N, which was knowable before it started — the
    same standing as `upcoming_slate` knowing the fixture list. The failure mode
    would be a player joining mid-season and appearing retroactively, so that was
    measured: across 2024 and 2025 exactly 3 and 2 players respectively hold rows
    for two different teams in one season. Mid-season movement is nil in college
    football, where the portal windows are winter and spring.

    THE ROSTER ALONE IS FAR TOO BROAD. 2025 has 15,601 roster rows and only 2,963
    of those players ever recorded a snap — it includes every walk-on and
    redshirt. This function is the universe's outer bound, not the universe; the
    caller narrows it by requiring prior-season production.
    """
    rows = fetch_all(
        """
        select pts.player_id,
               pts.team_id,
               coalesce(pts.position_group, p.position_group)::text as position_group
          from player_team_seasons pts
          join players p on p.id = pts.player_id
         where pts.season = %(season)s
           and coalesce(pts.position_group, p.position_group)
                 = any(%(positions)s::position_group[])
        """,
        {"season": as_of.season, "positions": list(SKILL_POSITIONS)},
    )
    log.info("%s: %d skill-position players on a roster", as_of, len(rows))
    return rows


# -----------------------------------------------------------------------------
# Player usage — prior season
# -----------------------------------------------------------------------------
def prior_season_usage(as_of: AsOf) -> list[dict[str, Any]]:
    """Per-player production across the whole PRIOR season.

    A completed season carries no cutoff of its own — all of it was knowable
    before this season started — so there is no week predicate here. The guard
    that matters is the season one, asserted below.
    """
    rows = fetch_all(
        f"""
        with played as (
          select s.*,
                 row_number() over (
                   partition by s.player_id order by s.week desc
                 ) as recency_rank
            from player_game_stats s
           where s.season = %(prior_season)s
             and s.position_group = any(%(positions)s::position_group[])
        )
        select player_id,
               (array_agg(team_id order by week desc))[1]        as prior_team_id,
               (array_agg(position_group order by week desc))[1] as prior_position_group,
               count(*)                                          as prior_games_played,
               max(season)                                       as max_source_season,
               {_usage_select_list()}
          from played
         group by player_id
        """,
        {
            "prior_season": as_of.prior_season,
            "positions": list(SKILL_POSITIONS),
        },
    )
    for row in rows:
        season = row.get("max_source_season")
        if season is not None and int(season) >= as_of.season:
            raise LookaheadError(
                f"prior_season_usage: row drew on season {season} while "
                f"predicting {as_of}."
            )
    # Namespace so prior-season columns cannot collide with current-season ones,
    # and drop the guard column so it does not reach the frame.
    return [
        {
            ("prior_" + k if k.endswith(("_pg", "_sd")) else k): v
            for k, v in row.items()
            if k != "max_source_season"
        }
        for row in rows
    ]


def prior_column_names() -> list[str]:
    """Canonical prior-season columns, whether or not a prior season exists.

    The feature frame's schema must not depend on how much history happens to be
    ingested. Without this, a 2024 frame (no 2023 data) and a 2025 frame (2024
    present) would have different column sets, and any model or backtest walking
    across seasons would break on the boundary — or worse, silently train on a
    different feature set per season.
    """
    names = ["prior_team_id", "prior_position_group", "prior_games_played"]
    for column in USAGE_STAT_COLUMNS:
        names.append(f"prior_{column}_pg")
        names.append(f"prior_{column}_sd")
        names.append(f"prior_{column}_recent_pg")
    return names


def prior_weight(
    games_played: int, *, changed_team: bool, ceiling: float
) -> float:
    """How much of a projection the prior season may carry.

    Starts at `ceiling` before any current-season evidence exists and decays as
    games accumulate — the "lean on priors early, sharpen as the season
    accumulates" rule in CLAUDE.md §6. Written to `projections.prior_weight` so
    the UI can widen the displayed range honestly instead of implying a
    precision the model does not have this early.
    """
    if ceiling <= 0:
        return 0.0
    decay = PRIOR_GAMES_EQUIVALENT / (PRIOR_GAMES_EQUIVALENT + max(games_played, 0))
    weight = ceiling * decay
    if changed_team:
        weight *= CHANGED_TEAM_PRIOR_MULTIPLIER
    return max(0.0, min(weight, ceiling))


# -----------------------------------------------------------------------------
# Team context
# -----------------------------------------------------------------------------
def player_goal_line_usage(
    as_of: AsOf, goal_line_yards: int = 10
) -> list[dict[str, Any]]:
    """Scoring opportunities and conversions, split at the goal-line threshold.

    Feeds the anytime-TD model (CLAUDE.md §6), which is opportunity x finish
    rate rather than a per-game average.

    TWO measurements that the box score cannot give us:

    1. **Where the chances came from.** Split at `goal_line_yards` because the
       two ranges convert at wildly different rates — measured over 2024-25, an
       RB scores on 28% of goal-line opportunities and 1.6% of the rest.

    2. **The split is NOT optional for receivers.** Only 28.5% of WR touchdowns
       originate inside the ten; they score on long plays. A pure goal-line
       model would systematically underrate exactly the players books price as
       longshots. RB 60%, TE 51%, QB 42% — so the mix differs by position too.

    EXCLUDING PASSING TOUCHDOWNS. `play_player_stats` records `stat_type =
    'Touchdown'` against the passer as well as the scorer, so counting those
    rows directly credits a quarterback with every touchdown he throws. Measured
    on 2024 weeks 1-7 that put QB goal-line conversion at 1.064 per opportunity
    — above 1.0, which is impossible and was the tell. Requiring the same player
    to also have a Rush or Reception on that play drops it to 0.381, and matches
    the rushing-plus-receiving definition `player_game_stats.offensive_tds` uses.

    KNOWN DISTORTION for receivers, harmless to the output but not to the story
    told about it. `Target` rows exist only on INCOMPLETE passes, and not even on
    all of them: inside the ten in 2024, wide receivers show 386 receptions
    against just 72 targets, implying an 84% catch rate where the true red-zone
    figure is nearer 50%. So the receiving OPPORTUNITY count is understated and
    the conversion rate correspondingly overstated — WR 0.595 rather than
    something near 0.3.

    This does not bias the projection, because the two errors cancel exactly:
    opportunities x (touchdowns / opportunities) = touchdowns, and touchdowns are
    counted correctly. Expected touchdowns per game is right either way. What it
    does mean is that the decomposition is only INTERPRETABLE for rush-based
    scoring, where Rush rows are complete. The weekly AI read must not tell a
    user a receiver "scores on 59% of goal-line targets" — that number is an
    artifact of the provider's attribution, not a fact about the player.
    """
    rows = fetch_all(
        """
        with per_play as (
          select pps.play_id,
                 pps.player_id,
                 p.game_id,
                 p.week,
                 p.yards_to_goal,
                 count(*) filter (
                   where pps.stat_type in ('Rush', 'Reception', 'Target')
                 ) > 0 as is_opportunity,
                 count(*) filter (
                   where pps.stat_type in ('Rush', 'Reception')
                 ) > 0 as is_touch,
                 count(*) filter (where pps.stat_type = 'Touchdown') > 0 as has_td
            from play_player_stats pps
            join plays p on p.id = pps.play_id
           where pps.season = %(season)s
             and pps.week   < %(week)s
             and pps.position_group = any(%(positions)s::position_group[])
             and p.yards_to_goal is not null
           group by pps.play_id, pps.player_id, p.game_id, p.week, p.yards_to_goal
        )
        select player_id,
               max(week)                as max_source_week,
               count(distinct game_id)  as scoring_games,
               count(*) filter (
                 where is_opportunity and yards_to_goal <= %(goal_line)s
               ) as goal_line_opportunities,
               count(*) filter (
                 where is_touch and has_td and yards_to_goal <= %(goal_line)s
               ) as goal_line_tds,
               count(*) filter (
                 where is_opportunity and yards_to_goal > %(goal_line)s
               ) as open_field_opportunities,
               count(*) filter (
                 where is_touch and has_td and yards_to_goal > %(goal_line)s
               ) as open_field_tds
          from per_play
         group by player_id
        """,
        {
            "season": as_of.season,
            "week": as_of.week,
            "goal_line": goal_line_yards,
            "positions": list(SKILL_POSITIONS),
        },
    )
    return _assert_no_lookahead(rows, as_of, "player_goal_line_usage")


def team_context(as_of: AsOf) -> list[dict[str, Any]]:
    """Offensive volume and pass/run balance per team, season to date.

    Derived from box scores rather than from `plays.play_type`, whose string
    vocabulary is provider-specific and would put a CFBD-shaped assumption into
    sport-agnostic core.
    """
    rows = fetch_all(
        """
        select team_id,
               count(distinct game_id)                as team_games_played,
               max(week)                              as max_source_week,
               sum(pass_attempts)::numeric
                 / nullif(count(distinct game_id), 0) as team_pass_attempts_pg,
               sum(rush_attempts)::numeric
                 / nullif(count(distinct game_id), 0) as team_rush_attempts_pg,
               sum(pass_yards)::numeric
                 / nullif(count(distinct game_id), 0) as team_pass_yards_pg,
               sum(rush_yards)::numeric
                 / nullif(count(distinct game_id), 0) as team_rush_yards_pg,
               sum(offensive_tds)::numeric
                 / nullif(count(distinct game_id), 0) as team_offensive_tds_pg,
               sum(pass_attempts)::numeric
                 / nullif(sum(pass_attempts) + sum(rush_attempts), 0)
                                                      as team_pass_rate
          from player_game_stats
         where season = %(season)s
           and week   < %(week)s
         group by team_id
        """,
        {"season": as_of.season, "week": as_of.week},
    )
    return _assert_no_lookahead(rows, as_of, "team_context")


# -----------------------------------------------------------------------------
# Opponent: the position-split signal
# -----------------------------------------------------------------------------
def opponent_defense(as_of: AsOf) -> list[dict[str, Any]]:
    """Opponent-adjusted allowances by position, as of this cutoff.

    The core defensive signal (CLAUDE.md §5). Joined on `as_of_week = week`
    because that column already means "fitted on games before this week" — the
    anti-lookahead work happened when the row was written, and reading the wrong
    snapshot is the only way to break it here.

    NOTE ON `rank_vs_position`: rank 1 = allows the MOST, i.e. the softest
    matchup. Deliberate, confirmed 2026-07-31 — it drives the "who to target"
    list. It inverts the conventional defensive ranking, so anything surfacing
    it must label it.
    """
    rows = fetch_all(
        """
        select defense_team_id,
               position_group,
               as_of_week,
               games_included,
               adj_rush_yards_allowed_pg,
               adj_rec_yards_allowed_pg,
               adj_receptions_allowed_pg,
               adj_rush_tds_allowed_pg,
               adj_rec_tds_allowed_pg,
               adj_ppa_allowed,
               rush_yards_allowed_pg,
               rec_yards_allowed_pg,
               receptions_allowed_pg,
               shrinkage_weight,
               rank_vs_position
          from defense_position_ratings
         where season     = %(season)s
           and as_of_week = %(week)s
           and position_group = any(%(positions)s::position_group[])
        """,
        {
            "season": as_of.season,
            "week": as_of.week,
            "positions": list(SKILL_POSITIONS),
        },
    )
    return _assert_snapshot_cutoff(rows, as_of, "opponent_defense")


def team_elo(as_of: AsOf) -> list[dict[str, Any]]:
    """Point-in-time Elo entering this week.

    Elo is the ONLY external team rating usable as a feature for the 2024-25
    backtest era: SP+, SRS and FPI are served season-scoped only, so they are
    stored as `season_final` with a NULL `as_of_week` and are physically
    unreachable from this query. That is by design, not omission — see the
    CHECK constraint on `team_rating_snapshots`.

    The values originate from `games.pregameElo`, never from `/ratings/elo`,
    which returns the rating AFTER a week has been played.
    """
    rows = fetch_all(
        """
        select team_id,
               as_of_week,
               rating as elo
          from team_rating_snapshots
         where season        = %(season)s
           and source        = 'elo'
           and snapshot_kind = 'point_in_time'
           and as_of_week    = %(week)s
        """,
        {"season": as_of.season, "week": as_of.week},
    )
    return _assert_snapshot_cutoff(rows, as_of, "team_elo")


def game_weather(as_of: AsOf) -> list[dict[str, Any]]:
    """Conditions for the target week's games.

    OBSERVED, not forecast — see the caveat in the module docstring. Prefers the
    CFBD reading and falls back to Open-Meteo, matching the note on
    `game_weather`.
    """
    return fetch_all(
        """
        select distinct on (w.game_id)
               w.game_id,
               w.temperature_f,
               w.wind_speed_mph,
               w.precipitation_in,
               w.humidity,
               w.is_indoor,
               w.source
          from game_weather w
          join games g on g.id = w.game_id
         where g.season = %(season)s
           and g.week   = %(week)s
         order by w.game_id, (w.source = 'cfbd') desc
        """,
        {"season": as_of.season, "week": as_of.week},
    )


# -----------------------------------------------------------------------------
# Assembly
# -----------------------------------------------------------------------------
def _coerce(value: Any) -> Any:
    """Normalize a database value into something polars can hold.

    psycopg returns Postgres `numeric` as `decimal.Decimal`. Polars can panic
    building a frame from mixed-precision Decimals (an out-of-bounds in its
    decimal kernel), and every consumer downstream — scipy, scikit-learn,
    statsmodels — wants float64 regardless. Converting once at the boundary is
    both the fix and the right representation.

    The precision loss is irrelevant here: these are per-game averages and team
    ratings, not currency.
    """
    if isinstance(value, Decimal):
        return float(value)
    return value


def _frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    cleaned = [{k: _coerce(v) for k, v in row.items()} for row in rows]
    return pl.DataFrame(cleaned, infer_schema_length=None)


def build_feature_frame(
    as_of: AsOf,
    *,
    prior_season_weight_max: float = 0.5,
    include_weather: bool = True,
    goal_line_yards: int = 10,
) -> pl.DataFrame:
    """Assemble one row per (player, upcoming game) for the target week.

    Every input has already been cutoff-checked by the function that fetched it;
    this only joins them. Players with no current-season and no prior-season
    history are dropped — there is nothing to project from, and inventing a
    league-average line for them would put noise on the board.
    """
    slate = upcoming_slate(as_of)
    if not slate:
        log.warning("No games scheduled for %s — empty feature frame.", as_of)
        return pl.DataFrame()

    usage = player_usage(as_of)
    prior = prior_season_usage(as_of)
    context = team_context(as_of)
    defense = opponent_defense(as_of)
    elo = team_elo(as_of)

    log.info(
        "%s: %d games, %d players with current-season usage, %d with prior, "
        "%d team-context rows, %d defense ratings, %d elo rows",
        as_of,
        len(slate),
        len(usage),
        len(prior),
        len(context),
        len(defense),
        len(elo),
    )

    # One row per team per game, carrying its opponent. This is what turns a
    # game-shaped slate into the player-shaped frame the models consume.
    matchups: list[dict[str, Any]] = []
    for game in slate:
        for team_key, opponent_key, is_home in (
            ("home_team_id", "away_team_id", True),
            ("away_team_id", "home_team_id", False),
        ):
            matchups.append(
                {
                    "game_id": game["game_id"],
                    "season": game["season"],
                    "week": game["week"],
                    "team_id": game[team_key],
                    "opponent_team_id": game[opponent_key],
                    "is_home": is_home and not game["neutral_site"],
                    "neutral_site": game["neutral_site"],
                    "conference_game": game["conference_game"],
                    "start_date": game["start_date"],
                    "is_dome": game["is_dome"],
                    "elevation_m": game["elevation_m"],
                }
            )

    matchup_frame = _frame(matchups)
    usage_frame = _frame(usage)

    # Players with no current-season snap yet, taken from the roster. Entering
    # week 1 this is the ENTIRE universe — `player_usage` returns nothing, and
    # before Phase 6b that emptiness returned an empty frame and ended the run,
    # which is why the board was blank on opening weekend.
    #
    # Carrying them at every cutoff rather than only in weeks 1-2 keeps one code
    # path. It does not widen the published board: `MIN_GAMES_TO_PROJECT` still
    # decides who is projected, and a row with no current-season games fails it
    # exactly as it did before. Phase 6b.3 is where that rule changes.
    # A roster row on its own is not evidence — 15,601 of them in 2025 against
    # 2,963 players who ever took a snap. Require prior-season production, which
    # is the only thing a week-1 projection can be built from anyway. Players
    # with neither a current season nor a prior one are dropped here rather than
    # projected off a league-average line that would be pure noise on the board.
    roster_frame = _frame(roster_universe(as_of))
    if not roster_frame.is_empty() and prior:
        roster_frame = roster_frame.join(
            _frame(prior).select("player_id"), on="player_id", how="semi"
        )
    elif not prior:
        roster_frame = pl.DataFrame()

    if not roster_frame.is_empty():
        if usage_frame.is_empty():
            frame = roster_frame.with_columns(
                pl.lit(0, dtype=pl.Int64).alias("games_played")
            )
        else:
            newcomers = roster_frame.join(
                usage_frame.select("player_id"), on="player_id", how="anti"
            ).with_columns(pl.lit(0, dtype=pl.Int64).alias("games_played"))
            frame = pl.concat([usage_frame, newcomers], how="diagonal")
    elif usage_frame.is_empty():
        log.warning("No current-season usage and no roster for %s.", as_of)
        return pl.DataFrame()
    else:
        frame = usage_frame

    frame = frame.join(matchup_frame, on="team_id", how="inner")

    prior_frame = _frame(prior)
    if not prior_frame.is_empty():
        frame = frame.join(prior_frame, on="player_id", how="left")

    # Materialize the full prior-season schema regardless. A frame's columns
    # must depend on the cutoff, never on how much history happens to be
    # ingested — otherwise a backtest walking from 2024 into 2025 changes
    # feature set mid-run.
    missing = [c for c in prior_column_names() if c not in frame.columns]
    if missing:
        frame = frame.with_columns(
            [
                pl.lit(
                    0 if c == "prior_games_played" else None,
                    dtype=pl.Int64 if c.endswith(("_id", "_played")) else pl.Float64,
                ).alias(c)
                for c in missing
                if c != "prior_position_group"
            ]
        )
        if "prior_position_group" not in frame.columns:
            frame = frame.with_columns(
                pl.lit(None, dtype=pl.Utf8).alias("prior_position_group")
            )

    goal_line_frame = _frame(player_goal_line_usage(as_of, goal_line_yards))
    if not goal_line_frame.is_empty():
        frame = frame.join(
            goal_line_frame.drop("max_source_week"), on="player_id", how="left"
        )
    for column in (
        "scoring_games",
        "goal_line_opportunities",
        "goal_line_tds",
        "open_field_opportunities",
        "open_field_tds",
    ):
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(0, dtype=pl.Int64).alias(column))
    frame = frame.with_columns(
        [pl.col(c).fill_null(0) for c in (
            "scoring_games",
            "goal_line_opportunities",
            "goal_line_tds",
            "open_field_opportunities",
            "open_field_tds",
        )]
    )

    context_frame = _frame(context)
    if not context_frame.is_empty():
        frame = frame.join(
            context_frame.drop("max_source_week"), on="team_id", how="left"
        )

    elo_frame = _frame(elo)
    if not elo_frame.is_empty():
        elo_frame = elo_frame.drop("as_of_week")
        frame = frame.join(
            elo_frame.rename({"elo": "team_elo"}), on="team_id", how="left"
        ).join(
            elo_frame.rename({"team_id": "opponent_team_id", "elo": "opponent_elo"}),
            on="opponent_team_id",
            how="left",
        )
        frame = frame.with_columns(
            (pl.col("team_elo") - pl.col("opponent_elo")).alias("elo_diff")
        )

    defense_frame = _frame(defense)
    if not defense_frame.is_empty():
        defense_frame = defense_frame.drop("as_of_week").rename(
            {"defense_team_id": "opponent_team_id"}
        )
        frame = frame.join(
            defense_frame, on=["opponent_team_id", "position_group"], how="left"
        )

        # The join above matches the PLAYER's position, which is right for a
        # receiver facing "rec yards allowed to WRs" — and useless for a QB,
        # whose passing markets care about what the defense allows to WRs and
        # TEs, not to quarterbacks. So attach the opponent's full defensive
        # profile as well, one column per position, available to every row.
        profile = defense_frame.pivot(
            index="opponent_team_id",
            on="position_group",
            values=list(DEFENSE_PROFILE_METRICS),
            aggregate_function="first",
        )
        renames = {
            c: f"opp_{c}"
            for c in profile.columns
            if c != "opponent_team_id"
        }
        frame = frame.join(
            profile.rename(renames), on="opponent_team_id", how="left"
        )

    if include_weather:
        weather_frame = _frame(game_weather(as_of))
        if not weather_frame.is_empty():
            frame = frame.join(weather_frame.drop("source"), on="game_id", how="left")

    # Prior weight, per row: how much of the projection the prior season carries.
    frame = frame.with_columns(
        pl.col("prior_games_played").fill_null(0),
        (
            pl.col("prior_team_id").is_not_null()
            & (pl.col("prior_team_id") != pl.col("team_id"))
        ).alias("changed_team"),
    )

    frame = frame.with_columns(
        pl.struct(["games_played", "changed_team"])
        .map_elements(
            lambda row: prior_weight(
                int(row["games_played"] or 0),
                changed_team=bool(row["changed_team"]),
                ceiling=prior_season_weight_max,
            ),
            return_dtype=pl.Float64,
        )
        .alias("prior_weight")
    )

    # Effective sample: current-season games plus the prior season's games
    # discounted by the weight actually applied to them. Written to
    # projections.effective_sample so the UI can show honest uncertainty.
    frame = frame.with_columns(
        (
            pl.col("games_played")
            + pl.col("prior_games_played") * pl.col("prior_weight")
        ).alias("effective_sample"),
        pl.lit(as_of.week, dtype=pl.Int16).alias("as_of_week"),
    )

    log.info("%s: feature frame is %d rows x %d columns", as_of, *frame.shape)
    return frame
