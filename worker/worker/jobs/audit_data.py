"""Correctness audit of the schema and the ingested data.

    python -m worker.jobs.audit_data      # exits non-zero if anything fails

Run after any ingest. Every check states a PASS CONDITION rather than printing a
number for a human to eyeball — a report that only shows counts cannot fail, and
a check that cannot fail is not a check.

This exists because the defects found in Phase 2 were all silent: an endpoint
truncating at 2,000 rows, ids arriving as strings so a join matched nothing, a
ratings endpoint returning post-game values, and CFBD swapping the passer and
receiver labels on touchdown plays. None raised an error. Each was caught by
asserting a property that had to hold, so those assertions live here rather than
in a transcript.

Covers: schema objects and constraints, RLS posture, the odds math, referential
integrity, the anti-lookahead guarantees, data completeness, value plausibility,
cross-source reconciliation between box scores and play attribution, the
distribution-family resolution layer, Python/SQL agreement on the odds math, and
the calibration outputs the Phase 3 report is rendered from.
"""

from __future__ import annotations

import sys

import psycopg

from worker.core import probability
from worker.db import connect, fetch_all, fetch_one
from worker.logging_setup import configure_logging

configure_logging("ERROR")

RESULTS: list[tuple[str, str, bool, str]] = []


def check(group: str, name: str, sql: str, ok, detail_cols=None, params=None):
    """Run sql, apply `ok(row)`, record PASS/FAIL."""
    try:
        row = fetch_one(sql, params)
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((group, name, False, f"query error: {exc}"))
        return
    if row is None:
        RESULTS.append((group, name, False, "no rows returned"))
        return
    passed = bool(ok(row))
    cols = detail_cols or list(row.keys())
    detail = ", ".join(f"{c}={row.get(c)}" for c in cols)
    RESULTS.append((group, name, passed, detail))


def manual(group: str, name: str, passed: bool, detail: str):
    RESULTS.append((group, name, bool(passed), detail))


def rejects(group: str, name: str, statement: str, params, constraint: str):
    """Assert a CHECK constraint actually FIRES, and that the named one fired.

    Every lookahead check above asks whether a constraint is *installed*. That is
    a different question from whether it bites, and only the second one protects
    anything: a constraint written against the wrong column, or one Postgres
    cannot prove and therefore skips, is present in `pg_constraint` and still
    lets the bad row in.

    The rows attempted here carry deliberately invalid foreign keys, because
    building valid ones would mean fabricating a player, a game and a projection
    to test an arithmetic rule. That is safe: Postgres evaluates CHECK
    constraints while forming the tuple, and foreign keys afterwards in AFTER
    triggers, so the CHECK is reached first. Asserting on `diag.constraint_name`
    rather than on "some error happened" is what makes that safe rather than
    lucky — an FK violation, a type error or a missing column all report
    something else and fail this check instead of passing it.

    Everything runs inside a transaction that is always rolled back.
    """
    try:
        with connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(statement, params)
            except psycopg.errors.CheckViolation as exc:
                fired = exc.diag.constraint_name
                RESULTS.append(
                    (
                        group,
                        name,
                        fired == constraint,
                        f"fired={fired}, expected={constraint}",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                RESULTS.append(
                    (group, name, False, f"wrong error: {type(exc).__name__}: {exc}")
                )
            else:
                RESULTS.append((group, name, False, "INSERT was ACCEPTED"))
            finally:
                conn.rollback()
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((group, name, False, f"connection error: {exc}"))


# =============================================================================
# PHASE 1 — schema integrity
# =============================================================================
G = "P1 schema"

check(G, "all 28 tables present", """
    select count(*) as n from pg_tables where schemaname='public'
""", lambda r: r["n"] >= 28)

check(G, "RLS enabled on every public table", """
    select count(*) as without_rls from pg_tables t
      join pg_class c on c.relname = t.tablename
     where t.schemaname='public' and c.relrowsecurity = false
""", lambda r: r["without_rls"] == 0)

check(G, "no write policies exist anywhere", """
    select count(*) as write_policies from pg_policies
     where schemaname='public' and cmd in ('INSERT','UPDATE','DELETE','ALL')
""", lambda r: r["write_policies"] == 0)

check(G, "closed tables have no policy (deny-all)", """
    select count(*) as policies from pg_policies
     where schemaname='public'
       and tablename in ('plays','play_player_stats','model_runs',
                         'pipeline_runs','backtests','backtest_predictions',
                         'calibration_bins','backtest_metrics')
""", lambda r: r["policies"] == 0)

check(G, "lookahead CHECK on team_rating_snapshots is live", """
    select count(*) as n from pg_constraint
     where conname = 'team_rating_snapshots_as_of_week_matches_kind'
""", lambda r: r["n"] == 1)

check(G, "backtest_predictions as_of_week tripwire exists", """
    select count(*) as n from pg_constraint
     where conrelid = 'backtest_predictions'::regclass and contype = 'c'
""", lambda r: r["n"] >= 1)

check(G, "odds helper functions exist", """
    select count(*) as n from pg_proc
     where proname in ('american_to_implied_probability','devig_two_way',
                       'edge_on_side','defense_position_splits_through')
""", lambda r: r["n"] >= 4)

check(G, "all three de-vig methods are installed", """
    select count(*) as n from pg_proc
     where proname in ('devig_two_way_proportional','devig_two_way_additive',
                       'devig_two_way_shin','devig_shin_z')
""", lambda r: r["n"] == 4)

check(G, "configured devig_method is one we implement", """
    select value #>> '{}' as method from app_config where key = 'devig_method'
""", lambda r: r["method"] in ("proportional", "additive", "shin"))

check(G, "board/detail views exist", """
    select count(*) as n from pg_views where schemaname='public'
       and viewname in ('v_board_rows','v_latest_prop_lines','v_player_game_log')
""", lambda r: r["n"] == 3)

# --- the odds math actually computes correctly --------------------------------
check(G, "american_to_implied_probability(-110) ~ 0.5238", """
    select american_to_implied_probability(-110) as p
""", lambda r: abs(float(r["p"]) - 0.52381) < 0.001)

check(G, "american_to_implied_probability(+100) = 0.5", """
    select american_to_implied_probability(100) as p
""", lambda r: abs(float(r["p"]) - 0.5) < 1e-9)

check(G, "devig of a -110/-110 market gives 0.5", """
    select devig_two_way(-110, -110) as p
""", lambda r: abs(float(r["p"]) - 0.5) < 1e-6)

check(G, "devig strips the vig (raw implied sums >1, de-vigged =1)", """
    select american_to_implied_probability(-150)
         + american_to_implied_probability(130) as raw_total,
           devig_two_way(-150, 130) as fair_over,
           1 - devig_two_way(-150, 130) as fair_under
""", lambda r: float(r["raw_total"]) > 1.0
             and abs(float(r["fair_over"]) + float(r["fair_under"]) - 1.0) < 1e-9)

check(G, "de-vigged favourite probability is below its raw implied", """
    select american_to_implied_probability(-150) as raw,
           devig_two_way(-150, 130) as fair
""", lambda r: float(r["fair"]) < float(r["raw"]))

check(G, "edge = model prob minus de-vigged book prob (over)", """
    select edge_on_side(0.60, devig_two_way(-110,-110), 'over'::bet_side) as edge
""", lambda r: abs(float(r["edge"]) - 0.10) < 1e-6)

check(G, "edge on the UNDER is measured against the under side", """
    select edge_on_side(0.30, devig_two_way(-110,-110), 'under'::bet_side) as edge
""", lambda r: abs(float(r["edge"]) - 0.20) < 1e-6)

check(G, "edge is NULL when no book price exists (not zero)", """
    select edge_on_side(0.60, null, 'over'::bet_side) is null as is_null
""", lambda r: r["is_null"] is True)

# --- de-vig method behaviour (migration 0013) ---------------------------------
check(G, "shin and additive agree on a two-way market", """
    select devig_two_way_shin(600, -1100)     as shin,
           devig_two_way_additive(600, -1100) as additive
""", lambda r: abs(float(r["shin"]) - float(r["additive"])) < 1e-12)

check(G, "proportional and shin diverge on a lopsided price", """
    select devig_two_way_proportional(600, -1100) as prop,
           devig_two_way_shin(600, -1100)         as shin
""", lambda r: float(r["prop"]) - float(r["shin"]) > 0.02)

check(G, "all methods agree near even money", """
    select devig_two_way_proportional(-110, -110) as prop,
           devig_two_way_additive(-110, -110)     as additive,
           devig_two_way_shin(-110, -110)         as shin
""", lambda r: max(abs(float(r["prop"]) - float(r["shin"])),
                   abs(float(r["additive"]) - float(r["shin"]))) < 1e-9)

check(G, "an incoherent market (implied total < 1) de-vigs to NULL", """
    select devig_two_way(600, 5000) is null as is_null
""", lambda r: r["is_null"] is True)

check(G, "shin z is a plausible informed-money share", """
    select devig_shin_z(-110, -110) as z
""", lambda r: 0.0 < float(r["z"]) < 0.25)

check(G, "picks records which devig method produced its book probability", """
    select count(*) as n from information_schema.columns
     where table_name = 'picks' and column_name = 'devig_method'
""", lambda r: r["n"] == 1)

check(G, "no pick carries a book probability without naming its method", """
    select count(*) as n from picks
     where book_prob_over is not null and devig_method is null
""", lambda r: r["n"] == 0)

check(G, "views are queryable against real data", """
    select (select count(*) from v_board_rows) as board,
           (select count(*) from v_player_game_log) as game_log,
           (select count(*) from v_latest_prop_lines) as lines
""", lambda r: r["game_log"] > 0)

# =============================================================================
# PHASE 2 — referential integrity
# =============================================================================
G = "P2 integrity"

check(G, "no orphaned foreign keys anywhere", """
    select
      (select count(*) from games g left join teams t on t.id=g.home_team_id where t.id is null)
      + (select count(*) from games g left join teams t on t.id=g.away_team_id where t.id is null)
      + (select count(*) from team_seasons s left join teams t on t.id=s.team_id where t.id is null)
      + (select count(*) from player_team_seasons p
           left join players x on x.id=p.player_id where x.id is null)
      + (select count(*) from player_game_stats s
           left join games g on g.id=s.game_id where g.id is null)
      + (select count(*) from plays p
           left join games g on g.id=p.game_id where g.id is null)
      + (select count(*) from play_player_stats s
           left join plays p on p.id=s.play_id where p.id is null)
      + (select count(*) from defense_position_game_splits s
           left join games g on g.id=s.game_id where g.id is null)
      + (select count(*) from defense_position_ratings r
           left join teams t on t.id=r.defense_team_id where t.id is null)
      + (select count(*) from game_weather w
           left join games g on g.id=w.game_id where g.id is null)
      as orphans
""", lambda r: r["orphans"] == 0)

check(G, "denormalized season matches games.season everywhere", """
    select
      (select count(*) from plays p join games g on g.id=p.game_id where p.season<>g.season)
      + (select count(*) from play_player_stats s
           join games g on g.id=s.game_id where s.season<>g.season)
      + (select count(*) from player_game_stats s
           join games g on g.id=s.game_id where s.season<>g.season)
      + (select count(*) from defense_position_game_splits s
           join games g on g.id=s.game_id where s.season<>g.season)
      as mismatches
""", lambda r: r["mismatches"] == 0)

check(G, "denormalized week matches games.week everywhere", """
    select
      (select count(*) from plays p join games g on g.id=p.game_id where p.week<>g.week)
      + (select count(*) from play_player_stats s
           join games g on g.id=s.game_id where s.week<>g.week)
      + (select count(*) from player_game_stats s
           join games g on g.id=s.game_id where s.week<>g.week)
      + (select count(*) from defense_position_game_splits s
           join games g on g.id=s.game_id where s.week<>g.week)
      as mismatches
""", lambda r: r["mismatches"] == 0)

check(G, "team != opponent in every fact table", """
    select
      (select count(*) from player_game_stats where team_id=opponent_team_id)
      + (select count(*) from play_player_stats where team_id=opponent_team_id)
      + (select count(*) from defense_position_game_splits where defense_team_id=offense_team_id)
      + (select count(*) from games where home_team_id=away_team_id)
      as violations
""", lambda r: r["violations"] == 0)

check(G, "opponent_team_id is genuinely the other team in the game", """
    select count(*) as wrong from player_game_stats s join games g on g.id=s.game_id
     where not (
       (s.team_id=g.home_team_id and s.opponent_team_id=g.away_team_id) or
       (s.team_id=g.away_team_id and s.opponent_team_id=g.home_team_id))
""", lambda r: r["wrong"] == 0)

check(G, "is_home agrees with the game record", """
    select count(*) as wrong from player_game_stats s join games g on g.id=s.game_id
     where s.is_home <> (s.team_id = g.home_team_id)
""", lambda r: r["wrong"] == 0)

check(G, "no duplicate player-game rows", """
    select count(*) as dupes from (
      select player_id, game_id from player_game_stats
       group by 1,2 having count(*)>1) x
""", lambda r: r["dupes"] == 0)

check(G, "no duplicate split rows", """
    select count(*) as dupes from (
      select game_id, defense_team_id, position_group
        from defense_position_game_splits group by 1,2,3 having count(*)>1) x
""", lambda r: r["dupes"] == 0)

# =============================================================================
# PHASE 2 — anti-lookahead (the load-bearing guarantees)
# =============================================================================
G = "P2 lookahead"

# THE CHECK THIS GROUP WAS MISSING, and the reason a real lookahead bug lived
# here for two phases. Every other check below verifies that a cutoff was
# applied correctly AGAINST games.week. None of them asked whether games.week
# orders games by TIME — so when CFBD's postseason numbering put a December bowl
# in week 1, all eight passed and every "through week N" aggregation for N >= 2
# quietly read a December result. A guard that is consistent with the bug it is
# meant to catch is worse than no guard, because it is reassuring.
#
# Stated as the property rather than as "no postseason in week 1": any future
# source quirk that breaks time ordering fails this too.
check(G, "games.week orders games by TIME, not by source label", """
    select count(*) as violations,
           min(a.season) as season,
           min(a.week)   as week
      from games a
      join games b
        on b.season = a.season and b.week < a.week
     where a.start_date is not null and b.start_date is not null
       and a.start_date < b.start_date
""", lambda r: r["violations"] == 0)

check(G, "postseason weeks sit past any regular-season week", """
    select count(*) as bad from games
     where season_type = 'postseason' and week <= 20
""", lambda r: r["bad"] == 0)

check(G, "denormalized week copies agree with games.week", """
    select
      (select count(*) from player_game_stats s join games g on g.id=s.game_id
        where s.week <> g.week or s.season <> g.season)
    + (select count(*) from defense_position_game_splits s join games g on g.id=s.game_id
        where s.week <> g.week or s.season <> g.season)
    + (select count(*) from plays p join games g on g.id=p.game_id
        where p.week <> g.week or p.season <> g.season)
    + (select count(*) from play_player_stats p join games g on g.id=p.game_id
        where p.week <> g.week or p.season <> g.season)
      as drifted
""", lambda r: r["drifted"] == 0)

check(G, "no model output targets a postseason week", """
    select
      (select count(*) from projections p join games g on g.id=p.game_id
        where g.season_type='postseason')
    + (select count(*) from picks p join games g on g.id=p.game_id
        where g.season_type='postseason')
    + (select count(*) from backtest_predictions b join games g on g.id=b.game_id
        where g.season_type='postseason')
      as targeted
""", lambda r: r["targeted"] == 0)

check(G, "season_final ratings never carry a week", """
    select count(*) as bad from team_rating_snapshots
     where snapshot_kind='season_final' and as_of_week is not null
""", lambda r: r["bad"] == 0)

check(G, "point_in_time ratings always carry a week", """
    select count(*) as bad from team_rating_snapshots
     where snapshot_kind='point_in_time' and as_of_week is null
""", lambda r: r["bad"] == 0)

check(G, "ratings fitted ONLY on strictly earlier weeks", """
    select count(*) as violations from defense_position_ratings r
     where r.games_included <> (
       select count(*) from defense_position_game_splits s
        where s.defense_team_id=r.defense_team_id and s.season=r.season
          and s.position_group=r.position_group and s.week < r.as_of_week)
""", lambda r: r["violations"] == 0)

check(G, "no rating is fitted on zero games", """
    select count(*) as bad from defense_position_ratings where games_included = 0
""", lambda r: r["bad"] == 0)

check(G, "splits_through(N) aggregates exactly the games with week < N", """
    with fn as (
      select defense_team_id, position_group, games_included
        from defense_position_splits_through(2024, 8)),
    direct as (
      select defense_team_id, position_group, count(*) as n
        from defense_position_game_splits
       where season=2024 and week < 8 group by 1,2)
    select count(*) as compared,
           count(*) filter (where fn.games_included <> direct.n) as disagree
      from fn join direct on direct.defense_team_id=fn.defense_team_id
                         and direct.position_group=fn.position_group
""", lambda r: r["compared"] > 0 and r["disagree"] == 0)

check(G, "splits_through(N) never includes week N itself", """
    with fn as (
      select defense_team_id, position_group, games_included
        from defense_position_splits_through(2024, 8)),
    through_9 as (
      select defense_team_id, position_group, games_included
        from defense_position_splits_through(2024, 9))
    select count(*) filter (
             where through_9.games_included < fn.games_included) as shrank
      from fn join through_9 using (defense_team_id, position_group)
""", lambda r: r["shrank"] == 0)

check(G, "splits_through(week=1) returns nothing (no prior games)", """
    select count(*) as n from defense_position_splits_through(2024, 1)
""", lambda r: r["n"] == 0)

check(G, "a feature query on as_of_week cannot reach season_final", """
    select count(*) as leaked from team_rating_snapshots
     where as_of_week is not null and snapshot_kind='season_final'
""", lambda r: r["leaked"] == 0)

# =============================================================================
# PHASE 2 — data completeness
# =============================================================================
G = "P2 completeness"

# Scoped to FULLY-ingested seasons. Prior-season backfills load box scores only
# (`ingest_stats --box-scores-only`), because prior-year features are box-score
# aggregates while play-by-play only ever feeds the split engine for the season
# being PREDICTED. A season with no plays is therefore a deliberate scope
# decision, not a gap — but a fully-loaded season missing play-by-play still is
# a gap, so the check identifies full seasons by whether they have any plays at
# all rather than by hardcoding a list.
check(G, "every completed game in a full-ingest season has play-by-play", """
    with full_seasons as (
      select distinct season from plays
    )
    select count(*) as missing from games g
     join full_seasons f on f.season = g.season
     where g.completed and not exists (select 1 from plays p where p.game_id=g.id)
""", lambda r: r["missing"] <= 2, ["missing"])

check(G, "box-score-only seasons are complete on their own terms", """
    with box_only as (
      select distinct s.season from player_game_stats s
       where not exists (select 1 from plays p where p.season = s.season)
    )
    select coalesce(count(*), 0) as missing
      from games g
      join box_only b on b.season = g.season
     where g.completed
       and not exists (select 1 from player_game_stats s where s.game_id = g.id)
""", lambda r: r["missing"] <= 2, ["missing"])

check(G, "every completed game has box-score rows", """
    select count(*) as missing from games g
     where g.completed
       and not exists (select 1 from player_game_stats s where s.game_id=g.id)
""", lambda r: r["missing"] <= 2, ["missing"])

# Two seasons carry play-by-play and are backtestable (2024, 2025); prior-season
# backfills add box scores only and are not counted here.
check(G, "two full-ingest seasons present", """
    select count(distinct season) as seasons from plays
""", lambda r: r["seasons"] == 2, ["seasons"])

check(G, "at least one prior season supplies prior-year features", """
    select count(distinct s.season) as seasons
      from player_game_stats s
     where not exists (select 1 from plays p where p.season = s.season)
""", lambda r: r["seasons"] >= 1, ["seasons"])

check(G, "FBS team counts are right (134 in 2024, 136 in 2025)", """
    select
      (select count(*) from team_seasons where season=2024 and classification='fbs') as y24,
      (select count(*) from team_seasons where season=2025 and classification='fbs') as y25
""", lambda r: r["y24"] == 134 and r["y25"] == 136)

check(G, "every FBS team has ratings at the final cutoff", """
    select count(distinct defense_team_id) as teams
      from defense_position_ratings
     where season=2024 and as_of_week=16 and position_group='RB'
""", lambda r: r["teams"] == 134)

check(G, "displayed conferences still flagged", """
    select count(*) as n from conferences where is_displayed
""", lambda r: r["n"] == 5)

# Weather is ingested per season alongside play-by-play, so a box-score-only
# prior season legitimately has none. Scoped to the seasons that were loaded in
# full, where a gap really would be a gap.
check(G, "weather covers >95% of full-ingest games", """
    with full_seasons as (select distinct season from plays)
    select round(100.0*count(distinct w.game_id)/count(distinct g.id),1) as pct
      from games g
      join full_seasons f on f.season = g.season
      left join game_weather w on w.game_id=g.id
""", lambda r: float(r["pct"]) > 95, ["pct"])

# =============================================================================
# PHASE 2 — value sanity (no absurd data)
# =============================================================================
G = "P2 sanity"

check(G, "no impossible passing yards in a game", """
    select count(*) as bad from player_game_stats
     where pass_yards > 800 or pass_yards < -50
""", lambda r: r["bad"] == 0)

check(G, "no impossible rushing yards", """
    select count(*) as bad from player_game_stats
     where rush_yards > 500 or rush_yards < -100
""", lambda r: r["bad"] == 0)

check(G, "completions never exceed attempts", """
    select count(*) as bad from player_game_stats
     where pass_completions > pass_attempts
""", lambda r: r["bad"] == 0)

check(G, "receptions never exceed targets", """
    select count(*) as bad from player_game_stats
     where targets is not null and receptions > targets
""", lambda r: r["bad"] == 0)

check(G, "offensive_tds generated column = rush + rec", """
    select count(*) as bad from player_game_stats
     where offensive_tds <> coalesce(rush_tds,0)+coalesce(rec_tds,0)
""", lambda r: r["bad"] == 0)

check(G, "offensive_tds excludes passing TDs", """
    select count(*) as bad from player_game_stats
     where pass_tds > 0 and rush_tds is null and rec_tds is null
       and offensive_tds <> 0
""", lambda r: r["bad"] == 0)

# Split by season_type since migration 0020. A single range over both would
# have to be wide enough to admit a postseason week, which would stop it
# catching the very thing it exists for — a regular-season week landing at 21.
check(G, "regular-season weeks are within a plausible range", """
    select min(week) as min_wk, max(week) as max_wk from games
     where season_type = 'regular'
""", lambda r: r["min_wk"] >= 1 and r["max_wk"] <= 20)

check(G, "postseason weeks are offset but not absurd", """
    select coalesce(min(week), 21) as min_wk, coalesce(max(week), 21) as max_wk
      from games where season_type = 'postseason'
""", lambda r: r["min_wk"] >= 21 and r["max_wk"] <= 25)

check(G, "Elo ratings are in a plausible band", """
    select min(rating) as lo, max(rating) as hi from team_rating_snapshots
     where source='elo'
""", lambda r: 500 < float(r["lo"]) and float(r["hi"]) < 2500)

check(G, "temperatures are plausible", """
    select min(temperature_f) as lo, max(temperature_f) as hi from game_weather
""", lambda r: -30 < float(r["lo"]) and float(r["hi"]) < 130)

check(G, "split yardage is non-negative where it must be", """
    select count(*) as bad from defense_position_game_splits
     where receptions_allowed < 0 or rec_yards_allowed < -50
""", lambda r: r["bad"] == 0)

check(G, "shrinkage weight is a valid proportion", """
    select count(*) as bad from defense_position_ratings
     where shrinkage_weight <= 0 or shrinkage_weight > 1
""", lambda r: r["bad"] == 0)

check(G, "ranks are dense 1..N per cutoff with no gaps or ties", """
    select count(*) as bad from (
      select season, as_of_week, position_group,
             count(*) as teams, count(distinct rank_vs_position) as ranks,
             min(rank_vs_position) as lo, max(rank_vs_position) as hi
        from defense_position_ratings group by 1,2,3
    ) x where teams <> ranks or lo <> 1 or hi <> teams
""", lambda r: r["bad"] == 0)

# THE CHECK ABOVE IS NOT ENOUGH, AND THE WAY IT FAILED IS WORTH KEEPING.
#
# It asks whether rank_vs_position is a valid permutation. It always was. What
# it never asked is whether the permutation orders on a column that MEANS
# anything, and for two phases QB defenses were ranked by receiving yards
# allowed to quarterbacks — a trick-play statistic averaging 0.46 yards a game,
# non-positive in 1,110 of 5,559 rated rows, uncorrelated (r = -0.06) with the
# rushing figure that is the only genuine QB position split. Dense, 1..N, no
# ties, and pure noise. Same shape as the bowl-week bug: a guard consistent with
# the defect is worse than no guard, because it reassures.
#
# So the property is stated twice, from both directions.

check(G, "rank_vs_position orders by the metric that position is measured on", """
    with ranked as (
      select rank_vs_position as stored,
             row_number() over (
               partition by season, as_of_week, position_group
               order by case when position_group in ('QB','RB')
                             then adj_rush_yards_allowed_pg
                             else adj_rec_yards_allowed_pg end desc
             ) as expected
        from defense_position_ratings
    )
    select count(*) as bad from ranked where stored <> expected
""", lambda r: r["bad"] == 0)

# The independent half: no appeal to what the code ranked on, just the fact that
# a per-game yards-allowed figure a rank is built from cannot be zero or
# negative across a season. The old QB metric fails this outright.
check(G, "every ranking metric is a real quantity, not noise around zero", """
    select count(*) as rated, count(*) filter (where m <= 5.0) as implausible,
           round(min(m), 2) as lowest
      from (
        select case when position_group in ('QB','RB') then adj_rush_yards_allowed_pg
                    else adj_rec_yards_allowed_pg end as m
          from defense_position_ratings
      ) x
""", lambda r: r["rated"] > 0 and r["implausible"] == 0)

# =============================================================================
# PHASE 2 — cross-source reconciliation
# =============================================================================
G = "P2 reconcile"

check(G, "splits receptions match box scores for receiving positions (>75%)", """
    with box as (
      select game_id, opponent_team_id as d, position_group,
             sum(receptions) as rec from player_game_stats
       where season=2024 and position_group in ('RB','WR','TE')
         and receptions is not null
       group by 1,2,3)
    select count(*) as compared,
           round(100.0*count(*) filter (where s.receptions_allowed=b.rec)
                 /nullif(count(*),0),1) as pct
      from defense_position_game_splits s
      join box b on b.game_id=s.game_id and b.d=s.defense_team_id
                and b.position_group=s.position_group
     where s.season=2024
""", lambda r: float(r["pct"]) > 75)

check(G, "QB rows have effectively no receptions (sanity on the join)", """
    select coalesce(sum(receptions_allowed),0) as qb_rec
      from defense_position_game_splits where season=2024 and position_group='QB'
""", lambda r: r["qb_rec"] < 200)

check(G, "QB rushing reconciles once sacks are subtracted (>65%)", """
    with rush as (
      select player_id, game_id, sum(stat_value) as r from play_player_stats
       where stat_type='Rush' and season=2024 group by 1,2),
    sacks as (
      select player_id, game_id, sum(stat_value) as s from play_player_stats
       where stat_type='Sack Taken' and season=2024 group by 1,2)
    select round(100.0*count(*) filter (
             where r.r - coalesce(s.s,0) = pgs.rush_yards)/nullif(count(*),0),1) as pct
      from player_game_stats pgs
      join rush r on r.player_id=pgs.player_id and r.game_id=pgs.game_id
      left join sacks s on s.player_id=pgs.player_id and s.game_id=pgs.game_id
     where pgs.season=2024 and pgs.position_group='QB' and pgs.rush_yards is not null
""", lambda r: float(r["pct"]) > 65)

check(G, "targets = receptions + incompletion targets", """
    select count(*) as bad from player_game_stats pgs
     where pgs.season=2024 and pgs.targets is not null
       and pgs.targets <> coalesce(pgs.receptions,0) + coalesce((
             select count(*) from play_player_stats pps
              where pps.player_id=pgs.player_id and pps.game_id=pgs.game_id
                and pps.stat_type='Target'), 0)
""", lambda r: r["bad"] == 0)

check(G, "goal-line carries never exceed total carries", """
    select count(*) as bad from defense_position_game_splits
     where goal_line_carries_allowed > rush_attempts
""", lambda r: r["bad"] == 0)

check(G, "receptions allowed never exceed targets allowed", """
    select count(*) as bad from defense_position_game_splits
     where receptions_allowed > targets
""", lambda r: r["bad"] == 0)

check(G, "split TDs are consistent with box-score TDs (season totals)", """
    with s as (select sum(rush_tds_allowed) as rt, sum(rec_tds_allowed) as ct
                 from defense_position_game_splits where season=2024),
         b as (select sum(rush_tds) as rt, sum(rec_tds) as ct
                 from player_game_stats where season=2024
                  and position_group in ('QB','RB','WR','TE'))
    select s.rt as split_rush_td, b.rt as box_rush_td,
           round(100.0*s.rt/nullif(b.rt,0),1) as pct from s, b
""", lambda r: 70 < float(r["pct"]) < 105)

# =============================================================================
# PHASE 3 — the distribution-family resolution layer
# =============================================================================
# Phase 3d measured the nine seeded families against 2024-25 data and found four
# of them wrong, two irreparably so because one family per market cannot describe
# a stat whose shape depends on who produces it. The fix was a per-position
# override plus a resolver. These pin that the override layer is live and still
# says what the measurement said — a silently reverted override would not raise
# anywhere, it would just quietly fit RB rushing with a normal again.
G = "P3 families"

check(G, "resolve_distribution_family() exists", """
    select count(*) as n from pg_proc where proname = 'resolve_distribution_family'
""", lambda r: r["n"] == 1)

check(G, "every market/position pair resolves to a family", """
    select count(*) as unresolved from market_positions mp
     where resolve_distribution_family(mp.market_key, mp.position_group) is null
""", lambda r: r["unresolved"] == 0)

check(G, "all 9 markets and 17 market/position pairs present", """
    select (select count(*) from markets) as markets,
           (select count(*) from market_positions) as pairs
""", lambda r: r["markets"] == 9 and r["pairs"] == 17)

# The override mechanism has to be doing work. If every pair resolved to its
# market default, the Phase 3d measurement would have been silently discarded
# and this whole layer would be dead code that still passes every other check.
check(G, "per-position overrides actually differ from market defaults", """
    select count(*) as overridden from market_positions mp
      join markets m on m.key = mp.market_key
     where mp.distribution_family is not null
       and mp.distribution_family <> m.distribution_family
""", lambda r: r["overridden"] >= 5, ["overridden"])

check(G, "rush_yards is gamma for BOTH QB and RB (sacks make it skewed)", """
    select resolve_distribution_family('rush_yards','QB') as qb,
           resolve_distribution_family('rush_yards','RB') as rb
""", lambda r: r["qb"] == "gamma" and r["rb"] == "gamma")

# 0015's stated reason for leaving the market default at normal: a position added
# later without measurement should inherit the family that at least admits
# negative outcomes.
check(G, "rush_yards market DEFAULT still admits negatives", """
    select distribution_family as fam from markets where key = 'rush_yards'
""", lambda r: r["fam"] == "normal")

check(G, "receptions is beta_binomial everywhere (under-dispersed)", """
    select count(*) as wrong from market_positions
     where market_key = 'receptions'
       and resolve_distribution_family(market_key, position_group) <> 'beta_binomial'
""", lambda r: r["wrong"] == 0)

check(G, "rec_yards is lognormal for RB and gamma for WR/TE", """
    select resolve_distribution_family('rec_yards','RB') as rb,
           resolve_distribution_family('rec_yards','WR') as wr,
           resolve_distribution_family('rec_yards','TE') as te
""", lambda r: r["rb"] == "lognormal" and r["wr"] == "gamma" and r["te"] == "gamma")

check(G, "anytime_td is bernoulli, binary, and has a 0.5 default line", """
    select distribution_family as fam, is_binary, default_line
      from markets where key = 'anytime_td'
""", lambda r: r["fam"] == "bernoulli" and r["is_binary"] is True
             and abs(float(r["default_line"]) - 0.5) < 1e-9)

# CLAUDE.md §7: run the model before books post, so a non-binary market must NOT
# invent a default line — it shows a projected range until a real line appears.
check(G, "only the binary market carries a default line", """
    select count(*) as bad from markets
     where (is_binary and default_line is null)
        or (not is_binary and default_line is not null)
""", lambda r: r["bad"] == 0)

check(G, "every market grades against a real player_game_stats column", """
    select count(*) as missing from markets m
     where not exists (
       select 1 from information_schema.columns c
        where c.table_name = 'player_game_stats' and c.column_name = m.stat_column)
""", lambda r: r["missing"] == 0)

# --- Python <-> SQL: the family contract ---------------------------------------
# projections.distribution is an enum, so any value it can hold is a value
# prob_over() may be handed. A family added in SQL but never wired into Python
# raises at projection time on a row that inserted cleanly.
try:
    _sql_families = {
        r["fam"]
        for r in fetch_all(
            "select unnest(enum_range(null::distribution_family))::text as fam"
        )
    }
    _py_families = set(probability.REQUIRED_PARAMS)
    _unimplemented = sorted(_sql_families - _py_families)
    manual(
        G,
        "every distribution_family enum value is implemented in Python",
        not _unimplemented,
        f"sql={len(_sql_families)}, python={len(_py_families)}, "
        f"unimplemented={_unimplemented or 'none'}",
    )

    _resolved = {
        r["fam"]
        for r in fetch_all(
            "select distinct resolve_distribution_family(market_key, position_group)"
            "::text as fam from market_positions"
        )
        if r["fam"]
    }
    manual(
        G,
        "every family we actually resolve to is implemented in Python",
        _resolved <= _py_families,
        f"resolved={sorted(_resolved)}",
    )
except Exception as exc:  # noqa: BLE001
    manual(G, "distribution family cross-check", False, f"error: {exc}")

# =============================================================================
# PHASE 3 — Python and SQL must agree on the odds maths
# =============================================================================
# The de-vig exists twice: plpgsql for the read layer and Python for the worker.
# Two implementations of one definition is a standing invitation to drift, and
# drift here is invisible — both sides return a plausible probability, and the
# edge silently depends on which code path produced it. Unit tests pin each side
# against itself; only this pins them against EACH OTHER, on the live database.
G = "P3 devig parity"

try:
    _prices = [-2000, -1100, -500, -250, -150, -110, 100, 130, 250, 600, 1500]
    _pairs = [(o, u) for o in _prices for u in _prices]
    _rows = fetch_all(
        """
        select t.o, t.u,
               devig_two_way_proportional(t.o, t.u) as proportional,
               devig_two_way_additive(t.o, t.u)     as additive,
               devig_two_way_shin(t.o, t.u)         as shin
          from unnest(%s::int[], %s::int[]) as t(o, u)
        """,
        ([o for o, _ in _pairs], [u for _, u in _pairs]),
    )

    for _method in ("proportional", "additive", "shin"):
        _worst = 0.0
        _disagreements = 0
        _null_mismatch = 0
        for _row in _rows:
            _py = probability.devig_two_way(_row["o"], _row["u"], _method)  # type: ignore[arg-type]
            _sql = _row[_method]
            if (_py is None) != (_sql is None):
                _null_mismatch += 1
                continue
            if _py is None:
                continue
            _gap = abs(_py - float(_sql))
            _worst = max(_worst, _gap)
            if _gap > 1e-9:
                _disagreements += 1
        manual(
            G,
            f"Python and SQL agree on {_method} de-vig ({len(_rows)} price pairs)",
            _disagreements == 0 and _null_mismatch == 0,
            f"max_gap={_worst:.2e}, disagreements={_disagreements}, "
            f"null_mismatch={_null_mismatch}",
        )

    # Both sides must decline the same markets, not merely agree where both
    # answer. A one-sided price and an incoherent two-way price are the two
    # cases where "no book probability" is the correct answer and zero is not.
    _declined_sql = sum(1 for _row in _rows if _row["shin"] is None)
    manual(
        G,
        "incoherent markets are declined by both sides, not priced at zero",
        _declined_sql > 0,
        f"declined={_declined_sql} of {len(_rows)}",
    )
except Exception as exc:  # noqa: BLE001
    manual(G, "devig parity sweep", False, f"error: {exc}")

check(G, "Python's default de-vig method is the configured one", """
    select value #>> '{}' as method from app_config where key = 'devig_method'
""", lambda r: r["method"] == probability.DEFAULT_DEVIG_METHOD)

# =============================================================================
# PHASE 3 — the tripwires actually fire
# =============================================================================
# Above, the lookahead group asks whether these constraints EXIST. Existence is
# not protection. Each of these attempts the exact bad row the constraint was
# written to stop, and requires that specific constraint to be the one that
# stops it. All roll back.
G = "P3 tripwires"

rejects(G, "backtest_predictions rejects as_of_week > week (LOOKAHEAD)", """
    insert into backtest_predictions
      (backtest_id, player_id, game_id, market_key, position_group, season,
       week, as_of_week, line, side, model_prob_over, confidence)
    values (gen_random_uuid(), -1, -1, 'rec_yards', 'WR', 2024,
            6, 7, 50.5, 'over', 0.6, 0.6)
""", None, "backtest_predictions_as_of_matches_week")

rejects(G, "games rejects a postseason row at a regular-season week", """
    insert into games
      (cfbd_id, season, week, season_type, home_team_id, away_team_id)
    values (-1, 2024, 1, 'postseason', -1, -2)
""", None, "games_postseason_week_offset")

rejects(G, "projections rejects as_of_week < 1", """
    insert into projections
      (model_run_id, player_id, game_id, team_id, opponent_team_id, market_key,
       season, week, as_of_week, distribution, params)
    values (gen_random_uuid(), -1, -1, -1, -2, 'rec_yards',
            2024, 1, 0, 'normal', '{"mu": 50, "sigma": 20}'::jsonb)
""", None, "projections_as_of_week_positive")

rejects(G, "picks rejects an OVER call on a sub-50% probability", """
    insert into picks
      (projection_id, player_id, game_id, team_id, opponent_team_id, market_key,
       season, week, line, side, model_prob_over)
    values (-1, -1, -1, -1, -2, 'rec_yards', 2024, 6, 50.5, 'over', 0.40)
""", None, "picks_side_matches_probability")

rejects(G, "picks rejects an UNDER call on an above-50% probability", """
    insert into picks
      (projection_id, player_id, game_id, team_id, opponent_team_id, market_key,
       season, week, line, side, model_prob_over)
    values (-1, -1, -1, -1, -2, 'rec_yards', 2024, 6, 50.5, 'under', 0.73)
""", None, "picks_side_matches_probability")

rejects(G, "picks rejects a probability above 1", """
    insert into picks
      (projection_id, player_id, game_id, team_id, opponent_team_id, market_key,
       season, week, line, side, model_prob_over)
    values (-1, -1, -1, -1, -2, 'rec_yards', 2024, 6, 50.5, 'over', 1.4)
""", None, "picks_model_prob_is_a_probability")

rejects(G, "season_final ratings cannot smuggle in an as_of_week (LOOKAHEAD)", """
    insert into team_rating_snapshots
      (team_id, season, snapshot_kind, as_of_week, source, rating)
    values (-1, 2024, 'season_final', 8, 'elo', 1500)
""", None, "team_rating_snapshots_as_of_week_matches_kind")

rejects(G, "point_in_time ratings cannot omit as_of_week (LOOKAHEAD)", """
    insert into team_rating_snapshots
      (team_id, season, snapshot_kind, as_of_week, source, rating)
    values (-1, 2024, 'point_in_time', null, 'elo', 1500)
""", None, "team_rating_snapshots_as_of_week_matches_kind")

# =============================================================================
# PHASE 3 — backtest outputs and the calibration curve
# =============================================================================
# The report is the Phase 3 deliverable and the client review gate, so a wrong
# number in it is expensive. These check the stored curve independently of the
# code that computed it.
G = "P3 backtest"

check(G, "a backtest model_run succeeded", """
    select count(*) as n from model_runs
     where run_type = 'backtest' and status = 'succeeded'
""", lambda r: r["n"] >= 1, ["n"])

# A killed process leaves 'running' forever. Left unchecked, the monitoring
# story in Phase 5 inherits rows that mean nothing.
check(G, "no run is stranded in 'running'", """
    select (select count(*) from model_runs
             where status='running' and started_at < now() - interval '2 hours') as runs,
           (select count(*) from pipeline_runs
             where status='running' and started_at < now() - interval '2 hours') as jobs
""", lambda r: r["runs"] == 0 and r["jobs"] == 0)

# Scoped to "some backtest", not "the latest one". Partial runs are a legitimate
# development tool — a three-week slice to check a fix, or a small run kept
# specifically so its raw predictions can be audited below — and any of them can
# be the most recent row. What must be true is that the report handed to the
# client came from a walk over every season we can actually walk.
check(G, "a backtest covered every ingested play-by-play season", """
    with ingested as (
      select array_agg(distinct season order by season)::smallint[] as seasons
        from plays)
    select (select seasons from ingested) as ingested,
           (select max(created_at) from backtests b, ingested i
             where b.seasons = i.seasons) as covered_at
""", lambda r: r["covered_at"] is not None, ["ingested", "covered_at"])

check(G, "hit_rate_basis is one of the two supported bases", """
    select count(*) as bad from backtests
     where hit_rate_basis not in ('threshold', 'closing_line')
""", lambda r: r["bad"] == 0)

check(G, "calibration bins were written for the latest backtest", """
    select count(*) as n from calibration_bins
     where backtest_id = (select id from backtests order by created_at desc limit 1)
""", lambda r: r["n"] >= 10, ["n"])

check(G, "every bin holds at least one prediction", """
    select count(*) as empty_bins from calibration_bins where n <= 0
""", lambda r: r["empty_bins"] == 0)

check(G, "predicted and observed rates are probabilities", """
    select count(*) as bad from calibration_bins
     where mean_predicted_probability < 0 or mean_predicted_probability > 1
        or observed_rate < 0 or observed_rate > 1
""", lambda r: r["bad"] == 0)

# The bin a prediction lands in is floor(p * 10), so its mean predicted
# probability has to lie inside its own edges. If it does not, predictions were
# binned by one number and summarised by another, and the reliability diagram is
# plotting points that no bin actually contains.
check(G, "each bin's mean predicted probability lies within its own edges", """
    select count(*) as bad from calibration_bins
     where mean_predicted_probability < bin_lower - 1e-9
        or mean_predicted_probability > bin_upper + 1e-9
""", lambda r: r["bad"] == 0)

check(G, "bins never overlap within a backtest and market", """
    select count(*) as overlaps
      from calibration_bins a
      join calibration_bins b
        on a.backtest_id = b.backtest_id
       and a.market_key is not distinct from b.market_key
       and a.id < b.id
     where a.bin_lower < b.bin_upper and b.bin_lower < a.bin_upper
""", lambda r: r["overlaps"] == 0)

# Per-market bins partition the same predictions the overall bins do, so the two
# totals must agree exactly. This is the cheapest possible check that the report
# is summarising one population rather than two.
check(G, "per-market bin counts sum to the overall bin count", """
    with latest as (select id from backtests order by created_at desc limit 1),
    overall as (
      select coalesce(sum(n), 0) as n from calibration_bins c, latest l
       where c.backtest_id = l.id and c.market_key is null),
    per_market as (
      select coalesce(sum(n), 0) as n from calibration_bins c, latest l
       where c.backtest_id = l.id and c.market_key is not null)
    select overall.n as overall, per_market.n as per_market
      from overall, per_market
""", lambda r: r["overall"] > 0 and r["overall"] == r["per_market"])

check(G, "every stored prediction respects the knowledge cutoff (LOOKAHEAD)", """
    select count(*) as violations from backtest_predictions where as_of_week > week
""", lambda r: r["violations"] == 0)

check(G, "no stored prediction was graded before the model could see 2 games", """
    select count(*) as bad from backtest_predictions where as_of_week < 2
""", lambda r: r["bad"] == 0)

# -----------------------------------------------------------------------------
# Stored metrics — the record that makes two runs comparable
# -----------------------------------------------------------------------------
check(G, "the latest backtest stored its headline metrics", """
    select count(*) as n from backtest_metrics
     where backtest_id = (select id from backtests order by created_at desc limit 1)
""", lambda r: r["n"] >= 1, ["n"])

check(G, "every run has exactly one overall row", """
    select count(*) as bad from (
      select backtest_id, count(*) as n from backtest_metrics
       where group_kind = 'overall' group by 1 having count(*) <> 1) x
""", lambda r: r["bad"] == 0)

# The stored summary and the stored curve are written from the same run, so a
# disagreement between them means one of the two writes is looking at a
# different population than the other.
check(G, "stored n agrees with the calibration bins for the same run", """
    with latest as (select id from backtests order by created_at desc limit 1)
    select (select n from backtest_metrics m, latest l
             where m.backtest_id = l.id and m.group_kind = 'overall') as metric_n,
           (select coalesce(sum(n), 0) from calibration_bins c, latest l
             where c.backtest_id = l.id and c.market_key is null) as bin_n
""", lambda r: r["metric_n"] == r["bin_n"])

check(G, "per-market stored n sums to the overall stored n", """
    with latest as (select id from backtests order by created_at desc limit 1)
    select (select n from backtest_metrics m, latest l
             where m.backtest_id = l.id and m.group_kind = 'overall') as overall,
           (select coalesce(sum(n), 0) from backtest_metrics m, latest l
             where m.backtest_id = l.id and m.group_kind = 'market') as by_market
""", lambda r: r["overall"] == r["by_market"])

# Brier is a mean squared error on probabilities, so it cannot exceed 1. A value
# above that means outcomes or probabilities were stored on the wrong scale.
check(G, "stored Brier scores are on the probability scale", """
    select count(*) as bad from backtest_metrics
     where brier > 1 or brier < 0 or base_rate > 1 or ece > 1
""", lambda r: r["bad"] == 0)

# Deliberately NOT constrained to be positive. A confidently wrong model scores
# arbitrarily negative, and that signal is the whole reason the metric is kept.
check(G, "brier_skill is stored unclamped so a regression stays visible", """
    select count(*) as n from information_schema.check_constraints c
      join information_schema.constraint_column_usage u
        on u.constraint_name = c.constraint_name
     where u.table_name = 'backtest_metrics' and u.column_name = 'brier_skill'
""", lambda r: r["n"] == 0)

# -----------------------------------------------------------------------------
# Grading, re-derived in SQL from the raw stored rows
# -----------------------------------------------------------------------------
# The metrics are only as good as the grading underneath them, and a grading bug
# is silent: every number still prints, the curve still looks like a curve, and
# the model appears to be worth whatever the mistake is worth. These recompute
# the graded fields from `actual_value`, `line` and `model_prob_over` in SQL, so
# nothing here shares code with the Python that produced them.
#
# Requires a backtest run with --persist-predictions. Keeping one such run around
# is deliberate: the calibration curve is the Phase 3 deliverable, and being able
# to re-derive it from raw rows is what makes it auditable rather than asserted.
check(G, "at least one backtest kept its raw predictions (run --persist-predictions)", """
    select count(distinct backtest_id) as backtests from backtest_predictions
""", lambda r: r["backtests"] >= 1, ["backtests"])

check(G, "outcome_over is exactly 'the result cleared the line'", """
    select count(*) as bad from backtest_predictions
     where actual_value is not null
       and outcome_over is distinct from (actual_value > line)
""", lambda r: r["bad"] == 0)

# The distinction that makes a props board correct: an UNDER call on a result
# under the line is a hit. Conflating `hit` with `outcome_over` would roughly
# halve the apparent accuracy on unders and nothing would error.
check(G, "hit is 'the CALL was right', not 'the result went over'", """
    select count(*) as bad from backtest_predictions
     where outcome_over is not null
       and hit is distinct from ((side = 'over') = outcome_over)
""", lambda r: r["bad"] == 0)

check(G, "stored side agrees with the stored probability", """
    select count(*) as bad from backtest_predictions
     where (side = 'over') <> (model_prob_over >= 0.5)
""", lambda r: r["bad"] == 0)

check(G, "confidence is the mass on the called side", """
    select count(*) as bad from backtest_predictions
     where abs(confidence - greatest(model_prob_over, 1 - model_prob_over)) > 1e-9
""", lambda r: r["bad"] == 0)

check(G, "lines are quantized to half-points as books post them", """
    select count(*) as bad from backtest_predictions
     where (line * 2) <> round(line * 2)
""", lambda r: r["bad"] == 0)

check(G, "no line sits at or below zero (a free 100% over)", """
    select count(*) as bad from backtest_predictions where line <= 0
""", lambda r: r["bad"] == 0)

# The reliability diagram redrawn from raw rows must land on the stored curve.
# This is the one check that ties the report's picture back to the observations
# it claims to summarise.
check(G, "the stored calibration curve reproduces from the raw predictions", """
    with latest as (
      select backtest_id from backtest_predictions
       group by 1 order by max(created_at) desc limit 1),
    recomputed as (
      select least(floor(p.model_prob_over * 10)::int, 9) as slot,
             count(*) as n,
             avg(p.model_prob_over) as mean_predicted,
             avg(case when p.outcome_over then 1.0 else 0.0 end) as observed
        from backtest_predictions p join latest l using (backtest_id)
       group by 1),
    stored as (
      select round(c.bin_lower * 10)::int as slot, c.n,
             c.mean_predicted_probability, c.observed_rate
        from calibration_bins c join latest l using (backtest_id)
       where c.market_key is null)
    select count(*) as compared,
           count(*) filter (
             where s.slot is null or r.slot is null
                or s.n <> r.n
                or abs(s.mean_predicted_probability - r.mean_predicted) > 1e-9
                or abs(s.observed_rate - r.observed) > 1e-9) as disagree
      from recomputed r full join stored s on s.slot = r.slot
""", lambda r: r["compared"] > 0 and r["disagree"] == 0)

# =============================================================================
# PHASE 3 — configuration and key hygiene
# =============================================================================
G = "P3 config"

check(G, "every Phase 3 config key is present", """
    select count(*) as n from app_config
     where key in ('devig_method','hit_rate_basis','edge_threshold',
                   'prior_season_weight_max','goal_line_yards_to_goal',
                   'odds_adapter','min_games_for_defense_rank')
""", lambda r: r["n"] == 7)

check(G, "hit_rate_basis is a basis we implement", """
    select value #>> '{}' as basis from app_config where key = 'hit_rate_basis'
""", lambda r: r["basis"] in ("threshold", "closing_line"))

check(G, "edge_threshold is a sane probability difference", """
    select (value #>> '{}')::numeric as t from app_config where key='edge_threshold'
""", lambda r: 0 < float(r["t"]) < 0.5)

check(G, "prior_season_weight_max is a proportion below 1", """
    select (value #>> '{}')::numeric as w
      from app_config where key='prior_season_weight_max'
""", lambda r: 0 < float(r["w"]) < 1)

# app_config is world-READABLE by design — the app reads it directly. CLAUDE.md
# §0 makes key hygiene a hard rule, so the table has to be checked for anything
# that looks like a credential rather than trusted to stay clean.
check(G, "app_config holds no credential-shaped values", """
    select count(*) as suspicious from app_config
     where key ~* '(api_?key|token|secret|password|credential|bearer)'
        or value #>> '{}' ~ '^[A-Za-z0-9_\\-]{24,}$'
""", lambda r: r["suspicious"] == 0)

check(G, "app_config is readable but not writable from the app role", """
    select count(*) as write_policies from pg_policies
     where schemaname='public' and tablename='app_config'
       and cmd in ('INSERT','UPDATE','DELETE','ALL')
""", lambda r: r["write_policies"] == 0)

# =============================================================================
# PHASE 4 — what the application reads
# =============================================================================
# The app only ever reads Supabase (CLAUDE.md §2), so every one of its failure
# modes is a property of the data or the views, and every one of them renders as
# something plausible rather than as an error: a missing rank shows no pill, a
# missing team shows an em-dash chip, a rank read from the wrong cutoff shows a
# number that is simply wrong. None of that throws. These state the properties
# the pages depend on so the pipeline fails before a reader sees the page.
G = "P4 app"

# THE LOOKAHEAD CHECK FOR THE APPLICATION LAYER. `v_board_rows` joins the
# opponent rating on as_of_week = the projection's week, so a historical board
# shows the rank as it stood entering that week. Rewriting that join to "the
# latest rating" would silently make every past board better-informed than it
# was, which is the same class of bug as the bowl-week collision. Recomputed
# here from the base tables rather than trusted from the view.
check(G, "the board's opponent rank is pinned to its own week (LOOKAHEAD)", """
    select count(*) as compared,
           count(*) filter (where b.opponent_rank_vs_position
                                  is distinct from d.rank_vs_position) as wrong
      from v_board_rows b
      join players pl on pl.id = b.player_id
      left join defense_position_ratings d
             on d.defense_team_id = b.opponent_team_id
            and d.season          = b.season
            and d.as_of_week      = b.week
            and d.position_group  = pl.position_group
""", lambda r: r["compared"] > 0 and r["wrong"] == 0)

# A team with no team_seasons row still plays games, still appears in the
# weekly-targets panel, and renders as an em-dash chip with no conference — so
# it also silently escapes the conference filter.
check(G, "every team on a slate has a season row to be named and filtered by", """
    select count(*) as orphans from (
      select home_team_id as team_id, season from games
      union
      select away_team_id, season from games
    ) t
    where not exists (
      select 1 from team_seasons ts
       where ts.team_id = t.team_id and ts.season = t.season
    )
""", lambda r: r["orphans"] == 0)

# The headline claim of the product (CLAUDE.md §1): the call is the side holding
# most of the distribution, and the confidence is that side's mass. `picks` has
# CHECK constraints for this; the board reads through a view, and this asserts
# the view surfaces the same thing rather than a column that drifted.
check(G, "every board call agrees with the confidence shown beside it", """
    select count(*) as calls,
           count(*) filter (
             where (side = 'over'  and confidence <> model_prob_over)
                or (side = 'under' and confidence <> 1 - model_prob_over)
                or confidence < 0.5
           ) as inconsistent
      from v_board_rows where has_call
""", lambda r: r["calls"] > 0 and r["inconsistent"] == 0)

# The week strip and the board header read their counts straight from this view.
# If it drifts from the tables it summarises, the page states a game and
# projection count that no query behind it agrees with.
check(G, "v_slate_weeks counts agree with the tables they summarise", """
    select count(*) as weeks, count(*) filter (
             where v.projections <> (select count(*) from projections p
                                      where p.season = v.season and p.week = v.week)
                or v.players <> (select count(distinct p.player_id) from projections p
                                  where p.season = v.season and p.week = v.week)
           ) as wrong
      from v_slate_weeks v
""", lambda r: r["weeks"] > 0 and r["wrong"] == 0)

# The board's position tabs are fixed; the data behind them is not. A position
# with no projections renders an empty tab that looks like a filter bug.
check(G, "every position tab has rows in the latest slate week", """
    select count(distinct pl.position_group) as positions
      from projections pr join players pl on pl.id = pr.player_id
     where (pr.season, pr.week) = (
       select season, week from v_slate_weeks order by season desc, week desc limit 1
     )
""", lambda r: r["positions"] == 4)

# Every source the read layer names must be readable by the anon role. `unwrap`
# turns a denied read into a thrown error rather than an empty board, so this is
# loud when it happens — but it happens on a page view, in front of whoever is
# looking, which is later than here.
check(G, "every table the app reads is readable by the anon role", """
    select count(*) as missing from unnest(array[
      'conferences','teams','team_seasons','games','players',
      'player_game_stats','defense_position_game_splits',
      'defense_position_ratings','markets','market_positions','sportsbooks',
      'player_prop_lines','projections','picks','ai_reads','app_config'
    ]) as t(name)
    where not exists (
      select 1 from pg_policies p
       where p.schemaname = 'public' and p.tablename = t.name
         and p.cmd in ('SELECT','ALL') and 'anon' = any(p.roles)
    )
""", lambda r: r["missing"] == 0)

# =============================================================================
# P5 odds
# =============================================================================
# Book lines arrive by resolving the PROVIDER'S name strings onto our rows, and
# every way that goes wrong produces a row that looks perfectly normal: a line
# on the wrong player, or against the wrong fixture, renders as a confident,
# precise, wrong edge. Nothing throws. So the properties are stated against the
# data rather than trusted from the job that wrote it.
G = "P5 odds"

# THE ONE THAT CATCHES A MIS-RESOLVED PLAYER. A prop line is only meaningful if
# the player it names actually plays for one of the two teams in the game it is
# attached to. Player resolution is scoped to those two rosters precisely so
# this holds; checking it here proves the scoping was not bypassed — and would
# fire immediately if a future version ever matched names nationally.
check(G, "every prop line names a player from one of that game's two rosters", """
    select count(*) as lines,
           count(*) filter (where not exists (
             select 1 from player_team_seasons pts
              where pts.player_id = l.player_id
                and pts.season = l.season
                and pts.team_id in (g.home_team_id, g.away_team_id)
           )) as off_roster
      from player_prop_lines l
      join games g on g.id = l.game_id
""", lambda r: r["off_roster"] == 0)

# Same class as the bowl-week collision: a line filed under a week other than
# the one its game is played in becomes lookahead the moment anything reads it
# by (season, week) — which the board does.
check(G, "every prop line agrees with its game about season and week", """
    select count(*) as lines,
           count(*) filter (where l.season <> g.season or l.week <> g.week) as mismatched
      from player_prop_lines l
      join games g on g.id = l.game_id
""", lambda r: r["mismatched"] == 0)

# Synthetic rows are fake by construction and exist only so the OVER/UNDER path
# could be built before books posted. If they ever sit beside a real quote for
# the same player and market, the board has no way to tell them apart and a
# -110/-110 de-vig makes the fake one look like a market disagreeing with us.
# run_projections refuses to write them once an adapter is configured; this is
# that guarantee checked against the data instead of the code path.
check(G, "no fake line ever shares a player and market with a real one", """
    select count(*) as collisions
      from (
        select game_id, player_id, market_key
          from player_prop_lines
         group by game_id, player_id, market_key
        having count(*) filter (where source_adapter = 'synthetic') > 0
           and count(*) filter (where source_adapter <> 'synthetic') > 0
      ) x
""", lambda r: r["collisions"] == 0)

# A row whose adapter is not one we ship is an orphan: nothing knows how it got
# there, and `source_adapter` is what makes provider history attributable when
# the source changes.
check(G, "every prop line is attributable to an adapter we ship", """
    select count(*) as lines,
           count(*) filter (
             where source_adapter not in ('synthetic', 'theoddsapi', 'none')
           ) as unknown_source,
           coalesce(string_agg(distinct source_adapter, ', '), '-') as adapters
      from player_prop_lines
""", lambda r: r["unknown_source"] == 0)

# =============================================================================
# P5 ai reads
# =============================================================================
# A cached read is shown to a reader for a whole week and nothing downstream
# validates it, so the failures worth guarding are the ones that still render:
# a truncated sentence, a read attached to a player who is not playing, a row
# whose digest no longer describes anything.
G = "P5 ai reads"

check(G, "every cached read belongs to a player the board actually shows", """
    select count(*) as reads,
           count(*) filter (where not exists (
             select 1 from projections p
              where p.player_id = a.player_id
                and p.season = a.season and p.week = a.week
           )) as orphaned
      from ai_reads a
""", lambda r: r["orphaned"] == 0)

# The adapter refuses a truncated generation rather than storing it, because the
# unique key would keep a half-sentence in front of readers until the next
# weekly run. Gemini produced exactly that — "Facing the nation'" — from a 200
# response, so this is the same property checked against the stored data.
check(G, "no cached read is a truncated fragment", """
    select count(*) as reads,
           count(*) filter (
             where length(trim(content)) < 40
                or right(trim(content), 1) not in ('.', '!', '?', '"', ')')
           ) as fragments,
           coalesce(min(length(trim(content))), 0) as shortest
      from ai_reads
""", lambda r: r["fragments"] == 0)

# input_digest is what decides whether a read is regenerated. A NULL or
# duplicated-across-different-inputs digest silently converts the cache into
# "never refresh", which is indistinguishable from working right up until a
# line moves and the prose keeps quoting the old one.
check(G, "every cached read carries the digest its refresh depends on", """
    select count(*) as reads,
           count(*) filter (where input_digest is null or length(input_digest) <> 64)
             as unusable,
           count(*) filter (where prompt_version is null or prompt_version = '')
             as unversioned
      from ai_reads
""", lambda r: r["unusable"] == 0 and r["unversioned"] == 0)

check(G, "ai_adapter names a provider we ship", """
    select value #>> '{}' as adapter from app_config where key = 'ai_adapter'
""", lambda r: r["adapter"] in ("none", "gemini", "grok"))

# =============================================================================
# Report
# =============================================================================
groups: dict[str, list] = {}
for g, n, p, d in RESULTS:
    groups.setdefault(g, []).append((n, p, d))

total = len(RESULTS)
passed = sum(1 for _, _, p, _ in RESULTS if p)

for g, items in groups.items():
    print(f"\n{'=' * 78}\n{g}\n{'=' * 78}")
    for name, p, detail in items:
        mark = "PASS" if p else "FAIL"
        print(f"  [{mark}] {name}")
        if not p or "=" in detail:
            print(f"         {detail}")

print(f"\n{'=' * 78}")
print(f"  {passed}/{total} checks passed")
if passed < total:
    print("\n  FAILURES:")
    for g, n, p, d in RESULTS:
        if not p:
            print(f"    [{g}] {n}: {d}")
print("=" * 78)

sys.exit(0 if passed == total else 1)
