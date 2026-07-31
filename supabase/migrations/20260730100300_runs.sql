-- =============================================================================
-- 0003 — Run bookkeeping
-- =============================================================================
-- Defined before the fact tables because derived artefacts (opponent-adjusted
-- ratings, projections, backtests) reference the run that produced them. Without
-- this, a backtest number cannot be traced back to the code and configuration
-- that generated it, and "we fixed that" becomes unfalsifiable.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- model_runs — one row per modelling/fitting execution
-- -----------------------------------------------------------------------------
create table model_runs (
  id                uuid primary key default gen_random_uuid(),
  run_type          model_run_type not null,
  model_version     text not null,
  git_sha           text,
  season            smallint,
  week              smallint,
  as_of_week        smallint,
  config            jsonb not null default '{}'::jsonb,
  status            run_status not null default 'running',
  started_at        timestamptz not null default now(),
  finished_at       timestamptz,
  notes             text
);

comment on table model_runs is
  'One row per fit/projection/backtest execution. model_version + git_sha + config make any stored number reproducible.';

comment on column model_runs.as_of_week is
  'KNOWLEDGE CUTOFF for the entire run. Every feature consumed by this run must have been derivable from games with week < as_of_week (same season) plus prior seasons. Everything the run wrote inherits this cutoff. See CLAUDE.md §4.';

comment on column model_runs.config is
  'Full resolved configuration snapshot, including the app_config values in force. Stored per run so a historical backtest is not silently reinterpreted when a config default changes later.';

create index model_runs_type_started_idx on model_runs (run_type, started_at desc);

-- -----------------------------------------------------------------------------
-- pipeline_runs — one row per scheduled job execution
-- -----------------------------------------------------------------------------
create table pipeline_runs (
  id                uuid primary key default gen_random_uuid(),
  job_name          text not null,
  status            run_status not null default 'running',
  started_at        timestamptz not null default now(),
  finished_at       timestamptz,
  rows_written      integer,
  error             text,
  metadata          jsonb not null default '{}'::jsonb
);

comment on table pipeline_runs is
  'Operational log for the Render worker: ingest jobs, refreshes, weekly model runs, AI-read generation. Backs the pipeline monitoring/alerting deliverable in CLAUDE.md §8 Phase 5, and is written by the Phase 1 healthcheck job as proof the worker''s credentials and write path work end to end.';

create index pipeline_runs_job_started_idx on pipeline_runs (job_name, started_at desc);
create index pipeline_runs_status_idx on pipeline_runs (status) where status <> 'succeeded';
