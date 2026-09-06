-- =============================================================================
-- 0049 -- NFL identifiers
-- =============================================================================
-- Migration 0035 added the `sport` dimension and deliberately stopped short of
-- an external identifier for the second sport: "cfbd_id is a CFBD identifier and
-- stays that; the NFL adapter adds its own column when it knows what its
-- provider keys on. Designing a generic external_ref now would be guessing at a
-- provider not yet chosen."
--
-- The provider is now chosen -- nflverse -- so this migration adds the columns
-- 0035 left for it, and nothing else. No existing constraint is dropped or
-- reshaped, which is the property that matters here: every `on conflict` clause
-- in the worker names an index, and 0035 broke one by replacing
-- `conferences.name` with `(sport, name)` in a way nothing could see. Adding a
-- nullable column and a new unique index cannot have that effect.
--
-- ---------------------------------------------------------------------------
-- WHY THESE THREE KEYS, AND WHY THEY ARE DIFFERENT SHAPES
-- ---------------------------------------------------------------------------
-- Measured from the live nflverse releases on 2026-09-05, not assumed:
--
--   * `players.gsis_id`   -- '00-0023459'. The NFL's own GSIS identifier. It is
--     the durable, league-owned key: nflverse surfaces it but does not own it,
--     and most other NFL data sources carry it too, so keying on it does not
--     marry this project to one provider. Text, because the leading zeroes and
--     the hyphen are part of it.
--
--   * `teams.nfl_abbr`    -- 'KC', 'SF', 'JAX'. 32 of them, stable.
--     NOT `teams.abbreviation`, which already exists: that column holds CFBD's
--     abbreviation, is not unique, and is nullable. Overloading it would give
--     one column two meanings and no key, which is how a join silently starts
--     resolving to the wrong row.
--
--   * `games.nflverse_id` -- '2025_01_PIT_NYJ'. Unlike the two above this one
--     really is nflverse's own construction rather than a league identifier,
--     and it is named for what it is rather than dressed up as something
--     neutral.
--
-- ---------------------------------------------------------------------------
-- ONLY ONE OF THEM GETS A CHECK, AND THE ASYMMETRY IS DELIBERATE
-- ---------------------------------------------------------------------------
-- 0035 gave `teams` and `games` a conditional CHECK requiring a cfbd_id on
-- college rows. That was not a data-quality assertion; it was the interlock that
-- makes `default 'cfb'` safe, since an NFL row that forgets to set `sport` has
-- no cfbd_id and is rejected instead of quietly joining the college board.
--
-- That interlock already protects the NFL direction. An NFL row defaulting to
-- 'cfb' still fails `games_cfb_requires_cfbd_id`. So a matching NFL-side CHECK
-- buys nothing structural and only asserts data quality -- which is worth it
-- exactly where the source guarantees the value, and a trap everywhere else:
--
--   * `teams.nfl_abbr`   -- CHECKED. All 32 rows carry an abbreviation in every
--     source, and there are 32 of them forever. Requiring it is free.
--
--   * `players.gsis_id`  -- NOT CHECKED. Measured: 1 of the 2,946 rows in the
--     2026 roster file has a blank gsis_id. This is the same reason
--     `cfbd_athlete_id` is nullable -- the source genuinely omits it -- and a
--     CHECK here would reject a real player over a missing id.
--
--   * `games.nflverse_id` -- NOT CHECKED, and this one is a schedule question
--     rather than a data one. The 2026 schedule may be ingested from The Odds
--     API, which serves it free and already resolves to our team pairs, in
--     which case the nflverse id is attached later or not at all. A CHECK would
--     make the choice of schedule source a migration-level commitment, which is
--     precisely the guess 0035 refused to make.
--
-- ---------------------------------------------------------------------------
-- THE INDEX 0035 SAID TO WIDEN WHEN THIS DAY CAME
-- ---------------------------------------------------------------------------
-- "NO NEW INDEXES. Every row is 'cfb' today, so an index on `sport` has no
-- selectivity and the planner would ignore it. When NFL data lands and the
-- column starts discriminating, `games_season_week_idx` and its siblings are
-- the ones to widen."
--
-- NFL data lands this week, so `games_season_week_idx` is widened to lead with
-- sport, and replaced rather than supplemented.
--
-- BE PRECISE ABOUT WHY THAT IS SAFE, BECAUSE THE OBVIOUS REASON IS WRONG.
-- `(season, week)` is NOT a prefix of `(sport, season, week)` -- the prefixes
-- are `(sport)`, `(sport, season)` and `(sport, season, week)` -- so the usual
-- "a prefix still serves the old query" argument does not apply here, and the
-- worker does query this table without a sport filter (`SeasonContext.build`,
-- `week_slices`, `load_games`).
--
-- What makes it safe is cardinality, not prefixes. `sport` has one distinct
-- value today and will have two, so the planner can scan the leading column's
-- handful of ranges and still use the index. Measured on production before
-- shipping this, with EXPLAIN ANALYZE on 3,652 rows:
--
--   select ... where season = 2026                      -> Index Scan, cost 88.94
--   select ... where season = 2026 and week = 1         -> Index Scan, cost 73.44
--   select ... where sport = 'cfb' and season and week  -> Index Scan, cost 32.61
--
-- All three use the index; the sport-filtered read is simply cheaper, and that
-- is the read that matters, since every web query filters on sport
-- (web/lib/core/sport.ts) and the board is the surface with a measured
-- concurrency ceiling. Keeping both indexes would buy a little worker-side cost
-- back on a table small enough for the difference to be noise.
--
-- Its siblings are left alone on purpose. `games_home_team_idx` and
-- `games_away_team_idx` lead with a team id, which already implies a sport --
-- a team belongs to exactly one -- so widening them would add a column that
-- can never discriminate. `games_start_date_idx` is used by the slate window,
-- which spans sports by design.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- the identifiers
-- -----------------------------------------------------------------------------
alter table teams   add column nfl_abbr     text unique;
alter table players add column gsis_id      text unique;
alter table games   add column nflverse_id  text unique;

comment on column teams.nfl_abbr is
  'nflverse/NFL team abbreviation (KC, SF, JAX). The NFL join key, deliberately separate from teams.abbreviation, which holds CFBD''s non-unique abbreviation for college teams.';

comment on column players.gsis_id is
  'The NFL''s own GSIS player identifier (00-0023459). League-owned rather than provider-owned, so it survives a change of NFL data source. Nullable because the source omits it for a small number of players, exactly as cfbd_athlete_id is.';

comment on column games.nflverse_id is
  'nflverse game key (2025_01_PIT_NYJ). Unlike gsis_id and nfl_abbr this is the provider''s own construction, not a league identifier, and is named accordingly.';

-- -----------------------------------------------------------------------------
-- the one CHECK worth having -- see the header for why the other two would be
-- traps rather than guarantees
-- -----------------------------------------------------------------------------
alter table teams
  add constraint teams_nfl_requires_abbr
  check (sport <> 'nfl' or nfl_abbr is not null);

comment on constraint teams_nfl_requires_abbr on teams is
  'The mirror of teams_cfb_requires_cfbd_id. Every NFL team has an abbreviation in every source and there are 32 of them, so requiring it costs nothing and makes nfl_abbr a real key rather than an optional label.';

-- -----------------------------------------------------------------------------
-- widen the one index whose column order now discriminates
-- -----------------------------------------------------------------------------
create index games_sport_season_week_idx on games (sport, season, week);

drop index games_season_week_idx;
