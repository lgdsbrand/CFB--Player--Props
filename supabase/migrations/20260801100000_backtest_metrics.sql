-- =============================================================================
-- 0016 — Persist backtest metrics so runs can be compared
-- =============================================================================
-- `backtests` records the CONFIG of a run but none of its RESULTS, so the only
-- record of how a model version scored was the rendered HTML. Comparing two
-- runs meant diffing a report, and re-rendering one cost a full 40-minute walk.
-- During the Phase 3 variance-calibration work that cost two walks in one
-- session, which is the whole argument for this table.
--
-- Shape: one row per (run, grouping). The report already computes overall, by
-- market, by position, by season phase and by season, so storing them in a
-- single long table rather than five wide ones means adding a grouping later is
-- a new `group_kind` value and not a migration.
--
-- Closed under RLS like every other backtest internal (migration 0011): this is
-- model diagnostics, not something the board reads.
-- =============================================================================

create table backtest_metrics (
  id                bigint generated always as identity primary key,
  backtest_id       uuid not null references backtests(id) on delete cascade,

  group_kind        text not null,
  group_key         text not null,

  n                 integer  not null,
  base_rate         numeric  not null,
  brier             numeric  not null,
  brier_skill       numeric  not null,
  log_loss          numeric  not null,
  ece               numeric  not null,
  sharpness         numeric  not null,

  created_at        timestamptz not null default now(),

  unique (backtest_id, group_kind, group_key),
  constraint backtest_metrics_group_kind_known check (
    group_kind in ('overall', 'market', 'position', 'phase', 'season')
  ),
  constraint backtest_metrics_n_positive check (n > 0),
  constraint backtest_metrics_rates_are_probabilities check (
    base_rate between 0 and 1
    and brier     between 0 and 1
    and ece       between 0 and 1
    and sharpness between 0 and 0.5
  )
);

comment on table backtest_metrics is
  'Headline metrics per run per grouping. Exists so two model versions can be compared with a query instead of by diffing rendered HTML and re-running a 40-minute walk to produce it.';

comment on column backtest_metrics.group_kind is
  'Which dimension this row summarises: overall, market, position, phase (early/late season) or season.';

comment on column backtest_metrics.group_key is
  'The value within that dimension — a market key, a position group, a season. Empty string for group_kind = ''overall'', so the unique constraint works without a nullable key.';

comment on column backtest_metrics.brier_skill is
  'Brier score against always predicting the base rate. Positive means the model beats that baseline; zero means it adds nothing. Not constrained to [0,1] — a confidently wrong model scores arbitrarily negative, and that is exactly the signal worth keeping.';

comment on column backtest_metrics.ece is
  'Expected calibration error: mean gap between stated and observed probability, weighted by bin size. The single number closest to the question CLAUDE.md §6 asks.';

comment on column backtest_metrics.sharpness is
  'Mean |p - 0.5|. Read alongside ECE, never alone: a model that always says 50% is perfectly calibrated and useless.';

create index backtest_metrics_lookup_idx
  on backtest_metrics (group_kind, group_key, backtest_id);

alter table backtest_metrics enable row level security;
