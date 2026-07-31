-- =============================================================================
-- 0011 — Row Level Security
-- =============================================================================
-- Posture:
--   * RLS is enabled on EVERY table. A table with RLS enabled and no policy
--     denies all access, so anything omitted below is closed by default.
--   * The Next.js app uses the anon key and only ever READS. There are no
--     insert/update/delete policies anywhere in this schema — writes happen
--     exclusively from the Python worker using the service role, which bypasses
--     RLS. A leaked anon key therefore cannot mutate anything.
--   * Read access is granted on public sports data and model output. Operational
--     tables (raw play-by-play, run logs, backtest internals) stay closed: they
--     are large, internal, and nothing in the UI needs them.
--
-- CLAUDE.md §0 treats this repo as public-adjacent, so key hygiene is a hard
-- rule: the service role key never reaches the browser bundle.
-- =============================================================================

alter table conferences                   enable row level security;
alter table teams                         enable row level security;
alter table team_seasons                  enable row level security;
alter table venues                        enable row level security;
alter table games                         enable row level security;
alter table players                       enable row level security;
alter table player_team_seasons           enable row level security;
alter table player_game_stats             enable row level security;
alter table plays                         enable row level security;
alter table play_player_stats             enable row level security;
alter table defense_position_game_splits  enable row level security;
alter table defense_position_ratings      enable row level security;
alter table team_rating_snapshots         enable row level security;
alter table game_weather                  enable row level security;
alter table markets                       enable row level security;
alter table market_positions              enable row level security;
alter table sportsbooks                   enable row level security;
alter table player_prop_lines             enable row level security;
alter table model_runs                    enable row level security;
alter table pipeline_runs                 enable row level security;
alter table projections                   enable row level security;
alter table picks                         enable row level security;
alter table ai_reads                      enable row level security;
alter table backtests                     enable row level security;
alter table backtest_predictions          enable row level security;
alter table calibration_bins              enable row level security;
alter table app_config                    enable row level security;

-- -----------------------------------------------------------------------------
-- Public read: reference data, actuals, model output
-- -----------------------------------------------------------------------------
do $$
declare
  t text;
  public_read_tables text[] := array[
    'conferences',
    'teams',
    'team_seasons',
    'venues',
    'games',
    'players',
    'player_team_seasons',
    'player_game_stats',
    'defense_position_game_splits',
    'defense_position_ratings',
    'team_rating_snapshots',
    'game_weather',
    'markets',
    'market_positions',
    'sportsbooks',
    'player_prop_lines',
    'projections',
    'picks',
    'ai_reads',
    'app_config'
  ];
begin
  foreach t in array public_read_tables loop
    execute format(
      'create policy %I on public.%I for select to anon, authenticated using (true)',
      t || '_public_read', t
    );
  end loop;
end;
$$;

-- -----------------------------------------------------------------------------
-- Closed: internal / operational tables
-- -----------------------------------------------------------------------------
-- No policies, plus an explicit REVOKE so the intent is visible in the grants
-- and not only in the absence of a policy.
-- plays / play_player_stats: millions of rows with no UI consumer — the app
-- reads the aggregates in defense_position_game_splits instead.
revoke all on table plays                    from anon, authenticated;
revoke all on table play_player_stats        from anon, authenticated;
revoke all on table model_runs               from anon, authenticated;
revoke all on table pipeline_runs            from anon, authenticated;
revoke all on table backtests                from anon, authenticated;
revoke all on table backtest_predictions     from anon, authenticated;
revoke all on table calibration_bins         from anon, authenticated;

-- -----------------------------------------------------------------------------
-- Write access
-- -----------------------------------------------------------------------------
-- Intentionally none. The worker connects with the service role (RLS bypass) or
-- as the database owner over a direct Postgres connection. If a future feature
-- needs authenticated writes, add a narrowly scoped policy here rather than
-- widening any of the above.
