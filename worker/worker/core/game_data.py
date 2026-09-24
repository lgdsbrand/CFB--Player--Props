"""The game model's dataset: one row per game, known-before-kickoff only (CLAUDE.md §11, G3).

SPORT-AGNOSTIC CORE. Reads `games`, `team_strength_ratings`,
`team_rating_snapshots` and the line tables, all scoped by `games.sport`.

THREE KINDS OF COLUMN, AND ONLY ONE MAY REACH A MODEL.

  * FEATURES (`h_*`, `a_*`, `neutral`, `week`): what was known before the game.
    Team strength is read at `as_of_week = games.week`, which by construction
    (migration 0077) was fitted on earlier weeks only. Elo is the latest
    point-in-time rating entering the game's week. The prior is LAST season's
    final SP+, known before this season began. Season-final ratings of the
    SAME season are never read — the CHECK on `team_rating_snapshots` makes a
    point-in-time join unable to return one, and this module only asks for
    point_in_time rows of this season and season_final rows of the previous.
  * OUTCOMES (`margin`, `total`, `q1_*`, `h1_*`): what the model predicts.
  * MARKET (`mkt_*`): the closing lines, for GRADING ONLY. `FEATURE_COLUMNS`
    below is the single list a model may train on, and it holds no `mkt_`
    column; `test_game_model` asserts that. A model that reads the line it is
    graded against measures the book against itself (CLAUDE.md §11).

Current-season strength is stored relative to that season's league mean
(`*_rel`), because the league means drift between seasons (league PPA was
0.10 in 2022 and 0.17 in 2024) and a model trained on raw values would learn
the drift.
"""

from __future__ import annotations

import polars as pl

from worker.db import fetch_all

_GAMES_SQL = """
with elo as (
  -- Latest point-in-time Elo entering each week, per team and season.
  select team_id, season, as_of_week, rating
    from team_rating_snapshots
   where source = 'elo' and snapshot_kind = 'point_in_time'
),
prior as (
  -- LAST season's final SP+: known before this season's first game.
  select team_id, season + 1 as season, rating, offense_rating, defense_rating
    from team_rating_snapshots
   where source = 'sp_plus' and snapshot_kind = 'season_final'
),
lines as (
  select game_id,
         percentile_cont(0.5) within group (order by spread)          as spread,
         percentile_cont(0.5) within group (order by over_under)      as total,
         percentile_cont(0.5) within group (order by home_moneyline)  as home_ml,
         percentile_cont(0.5) within group (order by away_moneyline)  as away_ml
    from game_lines
   group by game_id
)
select
  g.id as game_id, g.season, g.week, g.start_date, g.neutral_site, g.completed,
  g.home_team_id, g.away_team_id,
  g.home_points, g.away_points,
  g.home_line_scores, g.away_line_scores,
  (select e.rating from elo e
    where e.team_id = g.home_team_id and e.season = g.season and e.as_of_week <= g.week
    order by e.as_of_week desc limit 1)                         as h_elo,
  (select e.rating from elo e
    where e.team_id = g.away_team_id and e.season = g.season and e.as_of_week <= g.week
    order by e.as_of_week desc limit 1)                         as a_elo,
  hp.rating as h_prior, hp.offense_rating as h_prior_off, hp.defense_rating as h_prior_def,
  ap.rating as a_prior, ap.offense_rating as a_prior_off, ap.defense_rating as a_prior_def,
  l.spread as mkt_spread, l.total as mkt_total, l.home_ml as mkt_home_ml, l.away_ml as mkt_away_ml
from games g
left join prior hp on hp.team_id = g.home_team_id and hp.season = g.season
left join prior ap on ap.team_id = g.away_team_id and ap.season = g.season
left join lines l on l.game_id = g.id
where g.sport = %(sport)s and g.season = any(%(seasons)s)
order by g.season, g.week, g.id
"""

_STRENGTH_SQL = """
select r.team_id, r.season, r.as_of_week, r.games_included,
       r.off_ppa - r.league_ppa             as off_ppa_rel,
       r.def_ppa - r.league_ppa             as def_ppa_rel,
       r.off_success - r.league_success     as off_success_rel,
       r.def_success - r.league_success     as def_success_rel,
       r.off_points_pg - r.league_points    as off_points_rel,
       r.def_points_pg - r.league_points    as def_points_rel,
       r.off_plays_pg - r.league_plays      as off_plays_rel,
       r.def_plays_pg - r.league_plays      as def_plays_rel,
       r.league_points, r.league_plays
  from team_strength_ratings r
  join teams t on t.id = r.team_id
 where t.sport = %(sport)s and r.season = any(%(seasons)s)
"""

STRENGTH_METRICS = (
    "off_ppa_rel", "def_ppa_rel", "off_success_rel", "def_success_rel",
    "off_points_rel", "def_points_rel", "off_plays_rel", "def_plays_rel",
)

# The ONLY columns a model may train on. No outcome, no `mkt_` column.
FEATURE_COLUMNS: tuple[str, ...] = (
    "neutral",
    "week",
    "h_games", "a_games",
    *(f"h_{m}" for m in STRENGTH_METRICS),
    *(f"a_{m}" for m in STRENGTH_METRICS),
    "h_elo", "a_elo",
    "h_prior", "h_prior_off", "h_prior_def",
    "a_prior", "a_prior_off", "a_prior_def",
    "league_points", "league_plays",
)


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


def load_games(seasons: list[int], sport: str = "cfb") -> pl.DataFrame:
    """Every game in `seasons` with its pre-game features, outcomes and closing lines."""
    params = {"sport": sport, "seasons": list(seasons)}
    games = _frame(fetch_all(_GAMES_SQL, params))
    strength = _frame(fetch_all(_STRENGTH_SQL, params))
    if games.is_empty():
        return games

    numeric = [c for c in games.columns if c.startswith(("h_", "a_", "mkt_"))]
    games = games.with_columns(
        [pl.col(c).cast(pl.Float64) for c in numeric]
    )

    strength = strength.with_columns(
        [pl.col(c).cast(pl.Float64) for c in (*STRENGTH_METRICS, "league_points", "league_plays")]
    )
    league = strength.select("season", "as_of_week", "league_points", "league_plays").unique(
        ["season", "as_of_week"]
    )
    side_cols = ["team_id", "season", "as_of_week", "games_included", *STRENGTH_METRICS]
    for side, team_col in (("h", "home_team_id"), ("a", "away_team_id")):
        renamed = strength.select(side_cols).rename(
            {
                "team_id": team_col,
                "as_of_week": "week",
                "games_included": f"{side}_games",
                **{m: f"{side}_{m}" for m in STRENGTH_METRICS},
            }
        )
        games = games.join(renamed, on=[team_col, "season", "week"], how="left")
    games = games.join(
        league.rename({"as_of_week": "week"}), on=["season", "week"], how="left"
    )

    return games.with_columns(
        pl.col("neutral_site").cast(pl.Float64).alias("neutral"),
        pl.col("week").cast(pl.Float64),
        pl.col("h_games").fill_null(0).cast(pl.Float64),
        pl.col("a_games").fill_null(0).cast(pl.Float64),
        (pl.col("home_points") - pl.col("away_points")).cast(pl.Float64).alias("margin"),
        (pl.col("home_points") + pl.col("away_points")).cast(pl.Float64).alias("total"),
        pl.col("home_line_scores").list.get(0, null_on_oob=True).alias("h_q1"),
        pl.col("away_line_scores").list.get(0, null_on_oob=True).alias("a_q1"),
        (
            pl.col("home_line_scores").list.get(0, null_on_oob=True)
            + pl.col("home_line_scores").list.get(1, null_on_oob=True)
        ).alias("h_h1"),
        (
            pl.col("away_line_scores").list.get(0, null_on_oob=True)
            + pl.col("away_line_scores").list.get(1, null_on_oob=True)
        ).alias("a_h1"),
    ).with_columns(
        (pl.col("h_q1") - pl.col("a_q1")).cast(pl.Float64).alias("q1_margin"),
        (pl.col("h_q1") + pl.col("a_q1")).cast(pl.Float64).alias("q1_total"),
        (pl.col("h_h1") - pl.col("a_h1")).cast(pl.Float64).alias("h1_margin"),
        (pl.col("h_h1") + pl.col("a_h1")).cast(pl.Float64).alias("h1_total"),
    )
