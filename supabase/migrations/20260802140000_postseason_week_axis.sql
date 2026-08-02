-- =============================================================================
-- 0020 — Put postseason games back on the season time axis
-- =============================================================================
-- THE BUG. CFBD numbers postseason games from 1 under seasonType='postseason',
-- so a bowl played on 19 December arrived as "week 1" — the same label as the
-- season opener. `games.week` is documented as "the week ordinal used as the
-- time axis for every point-in-time cutoff in this schema", and it was not
-- monotone in time.
--
-- THE CONSEQUENCE. defense_position_splits_through(season, N) aggregates
-- `week < N`, so for every N >= 2 it included that December game. A week-10
-- defensive rating was fitted partly on a result from the following December.
-- That is the silent, disqualifying lookahead CLAUDE.md §4 calls out, and it
-- reached the projections and the Phase 3 calibration report through the
-- ratings.
--
-- Measured before this ran: 42/46/46 postseason games in 2023/2024/2025, all at
-- week 1; 701 defense_position_game_splits rows; 2,666 player_game_stats rows;
-- 16,305 plays; 18,598 play_player_stats. 310 of the 544 defensive ratings at
-- as_of_week = 10 in 2025 were contaminated, each with games_included inflated
-- by exactly one.
--
-- WHY IT SURVIVED THE AUDIT. `audit_data`'s "P2 lookahead" group has eight
-- checks and every one verifies that a cutoff was applied correctly AGAINST the
-- week column. None asked whether the week column ordered games by time, so the
-- guard was perfectly consistent with the bug. The check that would have caught
-- it is added in the same commit as this migration.
--
-- THE OFFSET IS FIXED AT 20, not max(regular week) + n. The maximum regular
-- week is a moving target: mid-season it is however much of the schedule has
-- been ingested, so a bowl stored against it in September would collide with a
-- real week in November. 20 clears any regular season (the longest here is 16)
-- and leaves a visible gap, so a week in the twenties reads as postseason on
-- sight. It matches POSTSEASON_WEEK_OFFSET in worker/adapters/cfbd/mapping.py,
-- which is where new ingests get it right; this only repairs what is stored.
--
-- IDEMPOTENT. Each statement moves only rows still sitting below the offset, so
-- re-running cannot double-shift. On a fresh `supabase db reset` these tables
-- are empty and every statement is a no-op — the ingest fix is what keeps a
-- rebuilt database correct.
--
-- NOT MOVED, because they hold no postseason rows and never will: projections,
-- picks, player_prop_lines and backtest_predictions are model output, and the
-- model targets regular-season weeks only (see MIN_BACKTEST_WEEK and the
-- season_type filters in the walk-forward and projection slate). Verified at
-- zero rows each before this ran.
-- =============================================================================

-- The denormalized copies move first, while `games.week` still holds the old
-- value — each one is keyed off game_id, so the order only matters for
-- readability, but doing children first keeps every statement independent.

update player_game_stats s
   set week = s.week + 20
  from games g
 where g.id = s.game_id
   and g.season_type = 'postseason'
   and s.week <= 20;

update defense_position_game_splits s
   set week = s.week + 20
  from games g
 where g.id = s.game_id
   and g.season_type = 'postseason'
   and s.week <= 20;

update plays p
   set week = p.week + 20
  from games g
 where g.id = p.game_id
   and g.season_type = 'postseason'
   and p.week <= 20;

update play_player_stats p
   set week = p.week + 20
  from games g
 where g.id = p.game_id
   and g.season_type = 'postseason'
   and p.week <= 20;

update games
   set week = week + 20
 where season_type = 'postseason'
   and week <= 20;

-- -----------------------------------------------------------------------------
-- Make it unrepresentable rather than merely fixed
-- -----------------------------------------------------------------------------
-- The schema already refuses a season_final rating that carries a week
-- (migration 0005). This is the same idea for the season axis: a postseason
-- game may not occupy a week a regular-season game could also occupy, so the
-- collision cannot come back through a hand-written INSERT or a future adapter
-- that forgets the offset.
alter table games
  add constraint games_postseason_week_offset
  check (season_type <> 'postseason' or week > 20);

comment on constraint games_postseason_week_offset on games is
  'Postseason weeks are stored offset past any regular season (see POSTSEASON_WEEK_OFFSET). CFBD restarts postseason numbering at 1, which put bowl games in week 1 and leaked December results into every "through week N" aggregation. week is the time axis for every cutoff in this schema, so it must be monotone in time.';

comment on column games.week is
  'The week ordinal used as the time axis for every point-in-time cutoff in this schema. A feature computed with as_of_week = N may only read games with week < N in the same season. MONOTONE IN TIME BY CONSTRUCTION: postseason games are stored offset past the regular season (week > 20) rather than at the source''s own numbering, which restarts at 1.';
