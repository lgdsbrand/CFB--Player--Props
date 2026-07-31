-- =============================================================================
-- 0005 — Position splits, opponent-adjusted defensive ratings, external
--        ratings snapshots, weather
-- =============================================================================
-- This file carries the two anti-lookahead structures the project depends on.
--
-- (A) OBSERVATION vs INFERENCE. defense_position_game_splits stores what a
--     defense allowed in ONE game — an observation, true forever, needing no
--     cutoff. defense_position_ratings stores the opponent-ADJUSTED figure,
--     which is an inference fitted across games and therefore only meaningful
--     relative to a knowledge cutoff. Splitting them means "cumulative through
--     week N" is computed by aggregating games with week < N, so lookahead is
--     structurally impossible rather than a convention someone must remember.
--
-- (B) POINT-IN-TIME vs SEASON-FINAL ratings. CFBD serves end-of-season SP+/FPI
--     for historical seasons. Those numbers are legitimately useful as sanity
--     checks, and catastrophic if a backtest reads them as if they were known in
--     week 3. team_rating_snapshots stores both kinds but a CHECK constraint
--     guarantees season-final rows carry a NULL as_of_week — so any feature
--     query joining on as_of_week cannot pick them up, whatever the author
--     intended. See CLAUDE.md §4.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- defense_position_game_splits — raw, atomic, one game
-- -----------------------------------------------------------------------------
create table defense_position_game_splits (
  id                        bigint generated always as identity primary key,
  game_id                   bigint not null references games(id) on delete cascade,
  defense_team_id           bigint not null references teams(id),
  offense_team_id           bigint not null references teams(id),
  season                    smallint not null,
  week                      smallint not null,
  position_group            position_group not null,

  plays                     smallint,
  rush_attempts             smallint,
  rush_yards_allowed        smallint,
  rush_tds_allowed          smallint,
  targets                   smallint,
  receptions_allowed        smallint,
  rec_yards_allowed         smallint,
  rec_tds_allowed           smallint,
  first_downs_allowed       smallint,
  explosive_plays_allowed   smallint,
  goal_line_carries_allowed smallint,
  goal_line_targets_allowed smallint,
  ppa_allowed               numeric,

  computed_at               timestamptz not null default now(),
  unique (game_id, defense_team_id, position_group),
  constraint defense_position_game_splits_teams_differ
    check (defense_team_id <> offense_team_id)
);

comment on table defense_position_game_splits is
  'What ONE defense allowed to ONE position in ONE game, aggregated from play_player_stats. Raw and opponent-UNADJUSTED by design. Because rows are atomic per game, any "through week N" figure is an aggregation over week < N and therefore cannot leak the future. Opponent-adjusted values live in defense_position_ratings.';

comment on column defense_position_game_splits.goal_line_carries_allowed is
  'Carries allowed inside the goal-line threshold. Feeds the defensive side of the anytime-TD model — the rate at which this defense allows scores to this position (CLAUDE.md §6).';

create index defense_position_game_splits_team_idx
  on defense_position_game_splits (defense_team_id, season, week, position_group);
create index defense_position_game_splits_lookup_idx
  on defense_position_game_splits (season, week, position_group);

-- -----------------------------------------------------------------------------
-- defense_position_ratings — opponent-adjusted, as-of a cutoff
-- -----------------------------------------------------------------------------
create table defense_position_ratings (
  id                          bigint generated always as identity primary key,
  defense_team_id             bigint not null references teams(id) on delete cascade,
  season                      smallint not null,
  as_of_week                  smallint not null,
  position_group              position_group not null,
  adjustment_version          text not null,
  model_run_id                uuid references model_runs(id) on delete set null,

  games_included              smallint not null,

  -- raw per-game rates over the included games, for transparency in the UI
  rush_yards_allowed_pg       numeric,
  rec_yards_allowed_pg        numeric,
  receptions_allowed_pg       numeric,
  rush_tds_allowed_pg         numeric,
  rec_tds_allowed_pg          numeric,

  -- opponent-adjusted equivalents — what actually feeds projections
  adj_rush_yards_allowed_pg   numeric,
  adj_rec_yards_allowed_pg    numeric,
  adj_receptions_allowed_pg   numeric,
  adj_rush_tds_allowed_pg     numeric,
  adj_rec_tds_allowed_pg      numeric,
  adj_ppa_allowed             numeric,

  -- shrunk toward the national mean when games_included is small; surfaced so
  -- early-season uncertainty is visible rather than implied (CLAUDE.md §6)
  shrinkage_weight            numeric,
  rank_vs_position            smallint,

  computed_at                 timestamptz not null default now(),
  unique (defense_team_id, season, as_of_week, position_group, adjustment_version),
  constraint defense_position_ratings_as_of_week_positive check (as_of_week >= 1),
  constraint defense_position_ratings_games_included_nonneg check (games_included >= 0)
);

comment on table defense_position_ratings is
  'Opponent-adjusted defensive strength by position — the core signal behind defensive ranks, the defense-detail view and the weekly targets list (CLAUDE.md §5). Raw defensive numbers mislead badly because schedules are unbalanced, so nothing here feeds a projection unadjusted.';

comment on column defense_position_ratings.as_of_week is
  'KNOWLEDGE CUTOFF. This row was fitted using ONLY games with week < as_of_week in this season (plus down-weighted prior seasons). It is what was knowable ENTERING week N. Rows are append-only: never UPDATE a snapshot, insert a new as_of_week. A backtest predicting week N must join on as_of_week = N.';

comment on column defense_position_ratings.rank_vs_position is
  'Rank among FBS defenses on the adjusted figure at this cutoff. Backs the "opponent rank vs position" sort and the defense-rank filter in CLAUDE.md §7.';

comment on column defense_position_ratings.adjustment_version is
  'Identifies the opponent-adjustment method. Part of the unique key so a revised method can be computed alongside the old one and compared, instead of silently overwriting history.';

create index defense_position_ratings_lookup_idx
  on defense_position_ratings (season, as_of_week, position_group, rank_vs_position);

-- -----------------------------------------------------------------------------
-- team_rating_snapshots — external team-strength ratings
-- -----------------------------------------------------------------------------
create table team_rating_snapshots (
  id                    bigint generated always as identity primary key,
  team_id               bigint not null references teams(id) on delete cascade,
  season                smallint not null,
  source                rating_source not null,
  snapshot_kind         snapshot_kind not null,
  as_of_week            smallint,
  rating                numeric,
  ranking               smallint,
  offense_rating        numeric,
  defense_rating        numeric,
  special_teams_rating  numeric,
  raw                   jsonb,
  ingested_at           timestamptz not null default now(),

  -- The guard. A season-final rating has no valid week at which it was known,
  -- so it is forbidden from carrying one; a point-in-time rating must have one.
  constraint team_rating_snapshots_as_of_week_matches_kind check (
    (snapshot_kind = 'point_in_time' and as_of_week is not null and as_of_week >= 1)
    or
    (snapshot_kind = 'season_final' and as_of_week is null)
  )
);

comment on table team_rating_snapshots is
  'SP+, SRS, Elo and FPI. Team level only: these are priors and sanity checks and cannot say what a defense is bad against by position — that is what defense_position_ratings is for (CLAUDE.md §4).';

comment on column team_rating_snapshots.snapshot_kind is
  'point_in_time: the value as published entering as_of_week, usable as a model feature. season_final: the end-of-season value, usable ONLY for retrospective evaluation and sanity checks. The CHECK constraint forces season_final rows to have a NULL as_of_week, so a feature query joining on as_of_week physically cannot return them. Applying end-of-season ratings to earlier games is the silent, disqualifying bug CLAUDE.md §4 calls out; this makes it unrepresentable rather than merely discouraged.';

-- Separate partial unique indexes: one value per week for point-in-time, one
-- value per season for season-final.
create unique index team_rating_snapshots_point_in_time_key
  on team_rating_snapshots (team_id, season, source, as_of_week)
  where snapshot_kind = 'point_in_time';

create unique index team_rating_snapshots_season_final_key
  on team_rating_snapshots (team_id, season, source)
  where snapshot_kind = 'season_final';

create index team_rating_snapshots_lookup_idx
  on team_rating_snapshots (season, as_of_week, source)
  where snapshot_kind = 'point_in_time';

-- -----------------------------------------------------------------------------
-- game_weather
-- -----------------------------------------------------------------------------
create table game_weather (
  id                  bigint generated always as identity primary key,
  game_id             bigint not null references games(id) on delete cascade,
  source              weather_source not null,
  temperature_f       numeric,
  dew_point_f         numeric,
  humidity            numeric,
  precipitation_in    numeric,
  snowfall_in         numeric,
  wind_speed_mph      numeric,
  wind_direction_deg  numeric,
  pressure_mb         numeric,
  condition           text,
  is_indoor           boolean,
  observed_at         timestamptz,
  ingested_at         timestamptz not null default now(),
  unique (game_id, source)
);

comment on table game_weather is
  'CFBD weather where present, Open-Meteo as the fallback for outdoor venues (CLAUDE.md §4). Both sources may coexist for a game; the model prefers cfbd and falls back to open_meteo.';
