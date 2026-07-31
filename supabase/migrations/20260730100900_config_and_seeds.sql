-- =============================================================================
-- 0009 — Runtime configuration and seed data
-- =============================================================================
-- app_config is the single runtime source of truth for values that must be
-- changeable without a deploy, and specifically for the two decisions CLAUDE.md
-- §9 leaves open with the client. Environment variables may override these for
-- local development; the worker snapshots the resolved values into
-- model_runs.config so a historical run is never silently reinterpreted when a
-- default changes.
-- =============================================================================

create table app_config (
  key           text primary key,
  value         jsonb not null,
  description   text,
  updated_at    timestamptz not null default now()
);

comment on table app_config is
  'Runtime configuration, readable by both the worker and the web app. NEVER store secrets here — this table is world-readable and all credentials come from environment variables (CLAUDE.md §0).';

create trigger app_config_set_updated_at
  before update on app_config
  for each row execute function set_updated_at();

insert into app_config (key, value, description) values
  ('edge_threshold', '0.05'::jsonb,
   'Minimum edge for a pick to qualify as an edge. Matches the 5% used by the client''s existing MLB pitcher model (CLAUDE.md §6).'),

  ('edges_only_default', 'false'::jsonb,
   'Default state of the EDGES ONLY toggle on the board.'),

  ('hit_rate_basis', '"threshold"'::jsonb,
   'OPEN WITH CLIENT (CLAUDE.md §9.2). "threshold" grades past games against a fixed line and needs no paid line backfill; "closing_line" grades against the historical closing line and does. Default is threshold; player_prop_lines already stores full line history so the switch needs no re-ingest of anything but odds.'),

  ('hit_rate_windows', '[5, 10]'::jsonb,
   'Rolling windows offered by the hit-rate filter (L5 / L10).'),

  ('devig_method', '"proportional"'::jsonb,
   'UNCONFIRMED (CLAUDE.md §6). Method used to strip vig before comparing to a model probability. Must match the client''s MLB pitcher model; proportional is the common convention but their implementation has not been inspected.'),

  ('odds_adapter', '"none"'::jsonb,
   'OPEN WITH CLIENT (CLAUDE.md §9.1). Which odds adapter to run. "none" means no lines are ingested and the board shows model leans only — the app degrades gracefully rather than breaking.'),

  ('displayed_conference_source', '"conferences.is_displayed"'::jsonb,
   'Reminder that the conference filter is driven by data, not a hardcoded list, so post-realignment changes are a row edit.'),

  ('goal_line_yards_to_goal', '10'::jsonb,
   'Distance-to-goal threshold defining a goal-line opportunity for the anytime-TD model (CLAUDE.md §6).'),

  ('prior_season_weight_max', '0.5'::jsonb,
   'Ceiling on how much prior-season production can contribute to a projection in week 1, decaying as the current season accumulates. Transfer portal and NIL churn mean prior-year output often happened at another school (CLAUDE.md §6).'),

  ('min_games_for_defense_rank', '2'::jsonb,
   'Minimum games before a defense receives a published rank vs position; below this the rating is shown as provisional.'),

  ('backfill_seasons', '[2022, 2023, 2024, 2025]'::jsonb,
   'Seasons the Phase 2 backfill covers. Drives storage: play_player_stats runs roughly 1M rows per season across all FBS.');

-- -----------------------------------------------------------------------------
-- Conferences
-- -----------------------------------------------------------------------------
-- Names follow CFBD's strings; Phase 2 ingest reconciles against the API rather
-- than trusting this seed. is_displayed marks the five conferences shown in the
-- UI filter — it never restricts ingest, which covers all FBS.
insert into conferences (name, abbreviation, classification, is_displayed) values
  ('SEC',              'SEC',   'fbs', true),
  ('Big Ten',          'B1G',   'fbs', true),
  ('Big 12',           'B12',   'fbs', true),
  ('ACC',              'ACC',   'fbs', true),
  ('American Athletic','AAC',   'fbs', true),
  ('Pac-12',           'PAC',   'fbs', false),
  ('Mountain West',    'MW',    'fbs', false),
  ('Sun Belt',         'SBC',   'fbs', false),
  ('Conference USA',   'CUSA',  'fbs', false),
  ('Mid-American',     'MAC',   'fbs', false),
  ('FBS Independents', 'IND',   'fbs', false);

-- -----------------------------------------------------------------------------
-- Markets
-- -----------------------------------------------------------------------------
-- distribution_family values here are starting points to be validated against
-- the data in Phase 3, not settled choices. Note rush_yards is normal rather
-- than gamma because NCAA charges sacks as rushing losses, so QB rushing yards
-- for a game are genuinely able to go negative.
insert into markets
  (key, display_name, short_label, emoji, stat_column, distribution_family,
   is_binary, default_line, unit, sort_order) values
  ('pass_yards',       'Passing Yards',    'PASS YDS', '🎯', 'pass_yards',       'normal',            false, null, 'yards',   10),
  ('pass_tds',         'Passing TDs',      'PASS TD',  '🔥', 'pass_tds',         'poisson',           false, null, 'tds',     20),
  ('pass_attempts',    'Pass Attempts',    'ATT',      '⚡', 'pass_attempts',    'negative_binomial', false, null, 'attempts',30),
  ('pass_completions', 'Completions',      'CMP',      '⚡', 'pass_completions', 'negative_binomial', false, null, 'comps',   40),
  ('rush_yards',       'Rushing Yards',    'RUSH YDS', '🏃', 'rush_yards',       'normal',            false, null, 'yards',   50),
  ('rush_attempts',    'Rush Attempts',    'CARRIES',  '🏃', 'rush_attempts',    'negative_binomial', false, null, 'carries', 60),
  ('receptions',       'Receptions',       'REC',      '🙌', 'receptions',       'negative_binomial', false, null, 'catches', 70),
  ('rec_yards',        'Receiving Yards',  'REC YDS',  '🙌', 'rec_yards',        'gamma',             false, null, 'yards',   80),
  ('anytime_td',       'Anytime TD',       'ATD',      '🏈', 'offensive_tds',    'bernoulli',         true,  0.5,  'td',      90);

-- Market applicability per position, following CLAUDE.md §6 exactly.
-- rush_yards covers QB and RB; rush_attempts is an RB market only.
insert into market_positions (market_key, position_group, sort_order) values
  ('pass_yards',       'QB', 10),
  ('pass_tds',         'QB', 20),
  ('pass_attempts',    'QB', 30),
  ('pass_completions', 'QB', 40),
  ('rush_yards',       'QB', 50),
  ('rush_yards',       'RB', 10),
  ('rec_yards',        'RB', 20),
  ('rush_attempts',    'RB', 30),
  ('receptions',       'RB', 40),
  ('receptions',       'WR', 10),
  ('rec_yards',        'WR', 20),
  ('receptions',       'TE', 10),
  ('rec_yards',        'TE', 20),
  ('anytime_td',       'QB', 60),
  ('anytime_td',       'RB', 50),
  ('anytime_td',       'WR', 30),
  ('anytime_td',       'TE', 30);
