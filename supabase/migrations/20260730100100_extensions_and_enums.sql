-- =============================================================================
-- 0001 — Enums and shared helpers
-- =============================================================================
-- Controlled vocabularies live as enums where the value set is genuinely closed
-- and shared with the NFL build (positions, sides, run states). Anything with
-- metadata attached (markets, sportsbooks, conferences) is a table instead, so a
-- new market is a row rather than a code change.
-- =============================================================================

-- Positions we model, plus the buckets we need for play attribution.
-- Deliberately sport-agnostic: identical for the NFL adapter.
create type position_group as enum (
  'QB', 'RB', 'WR', 'TE',
  'OL', 'DL', 'LB', 'DB',
  'K', 'P',
  'OTHER'
);

create type season_type as enum ('regular', 'postseason');

create type team_classification as enum ('fbs', 'fcs', 'ii', 'iii', 'other');

-- External team-strength ratings. Priors and sanity checks only — these are team
-- level and cannot express what a defense allows by position (CLAUDE.md §4).
create type rating_source as enum ('sp_plus', 'srs', 'elo', 'fpi');

-- The lookahead guard. See team_rating_snapshots.
create type snapshot_kind as enum ('point_in_time', 'season_final');

create type weather_source as enum ('cfbd', 'open_meteo');

create type distribution_family as enum (
  'normal', 'lognormal', 'gamma', 'poisson', 'negative_binomial', 'bernoulli'
);

create type bet_side as enum ('over', 'under');

create type model_run_type as enum ('weekly', 'backfill', 'backtest');

create type run_status as enum ('running', 'succeeded', 'failed');

-- -----------------------------------------------------------------------------
-- updated_at maintenance
-- -----------------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

comment on function set_updated_at() is
  'Trigger helper: stamps updated_at on UPDATE. Attached to slowly-changing reference tables only; fact and snapshot tables are append-only and have no updated_at.';
