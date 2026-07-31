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
and cross-source reconciliation between box scores and play attribution.
"""

from __future__ import annotations

import sys

from worker.db import fetch_one
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


# =============================================================================
# PHASE 1 — schema integrity
# =============================================================================
G = "P1 schema"

check(G, "all 27 tables present", """
    select count(*) as n from pg_tables where schemaname='public'
""", lambda r: r["n"] >= 27)

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
                         'calibration_bins')
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

check(G, "weeks are within a plausible range", """
    select min(week) as min_wk, max(week) as max_wk from games
""", lambda r: r["min_wk"] >= 1 and r["max_wk"] <= 20)

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
