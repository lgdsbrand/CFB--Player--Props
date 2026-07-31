-- =============================================================================
-- 0008 — Backtesting and calibration
-- =============================================================================
-- The Phase 3 deliverable is a calibration report, reviewed before any UI work
-- (CLAUDE.md §6, §8). These tables give it a home now so the schema is reviewed
-- once rather than twice.
--
-- The question the report must answer: when the model says 60%, does it hit
-- ~60%? That is calibration_bins.mean_predicted_probability against
-- calibration_bins.observed_rate.
-- =============================================================================

create table backtests (
  id                uuid primary key default gen_random_uuid(),
  model_run_id      uuid not null references model_runs(id) on delete cascade,
  name              text not null,
  seasons           smallint[] not null,
  hit_rate_basis    text not null,
  config            jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now()
);

comment on table backtests is
  'One evaluation of one model_run across a set of seasons, point-in-time only.';

comment on column backtests.hit_rate_basis is
  'Which grading basis this backtest used: ''threshold'' (a fixed line applied to past games — the default, and what most props sites do) or ''closing_line'' (graded against the historical closing line, which needs a paid line backfill). Unresolved with the client, so it is configuration rather than a hardcoded choice (CLAUDE.md §9.2). Recorded per backtest because results are not comparable across bases.';

create table backtest_predictions (
  id                bigint generated always as identity primary key,
  backtest_id       uuid not null references backtests(id) on delete cascade,
  player_id         bigint not null references players(id) on delete cascade,
  game_id           bigint not null references games(id) on delete cascade,
  market_key        text not null references markets(key),
  position_group    position_group not null,
  season            smallint not null,
  week              smallint not null,
  as_of_week        smallint not null,

  line              numeric(6, 2) not null,
  side              bet_side not null,
  model_prob_over   numeric not null,
  book_prob_over    numeric,
  confidence        numeric not null,
  edge              numeric,

  actual_value      numeric,
  outcome_over      boolean,
  hit               boolean,

  created_at        timestamptz not null default now(),
  constraint backtest_predictions_as_of_matches_week
    check (as_of_week <= week)
);

comment on table backtest_predictions is
  'Per-prediction backtest record with the realised outcome attached. One row per graded pick.';

comment on constraint backtest_predictions_as_of_matches_week on backtest_predictions is
  'A prediction for week N may not be made with knowledge from after week N. This is the cheapest possible tripwire for lookahead bias — an off-by-one in the backtest harness fails the INSERT instead of quietly producing a flattering number.';

comment on column backtest_predictions.outcome_over is
  'Whether the actual value cleared the line, independent of which side was called.';

comment on column backtest_predictions.hit is
  'Whether the CALL was correct. Distinct from outcome_over: an under call on a result under the line is a hit.';

create index backtest_predictions_backtest_idx
  on backtest_predictions (backtest_id, market_key);
create index backtest_predictions_confidence_idx
  on backtest_predictions (backtest_id, confidence);

create table calibration_bins (
  id                          bigint generated always as identity primary key,
  backtest_id                 uuid not null references backtests(id) on delete cascade,
  market_key                  text references markets(key),
  position_group              position_group,
  bin_lower                   numeric not null,
  bin_upper                   numeric not null,
  n                           integer not null,
  mean_predicted_probability  numeric not null,
  observed_rate               numeric not null,
  created_at                  timestamptz not null default now(),
  constraint calibration_bins_range check (bin_lower < bin_upper)
);

comment on table calibration_bins is
  'The calibration curve. mean_predicted_probability vs observed_rate per confidence bin answers "when the model says 60%, does it hit ~60%?" — the question CLAUDE.md §6 says matters far more than a flashy point projection. NULL market_key or position_group means the bin is aggregated across that dimension.';

create index calibration_bins_backtest_idx
  on calibration_bins (backtest_id, market_key, bin_lower);
