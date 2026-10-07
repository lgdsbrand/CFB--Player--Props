-- 0085: the games table's model probabilities are CALIBRATED (comments only).
--
-- Since 2026-10-07 `run_game_model` stores the model's probability at the
-- book's line pulled toward 50% by one slope per market, fitted on the
-- 2023-2025 walk-forward backtest (CALIBRATION_SLOPE in
-- worker/core/game_picks.py): a raw 70% spread side covered about 51% of
-- the time, a raw 70% total about 55%. The columns, their constraints and
-- the site's edge arithmetic are unchanged; only what the number means is,
-- so only the comments change.
--
-- Shadow picks are NOT calibrated: game_picks.model_prob stays the raw
-- probability the pick was made on.

comment on column game_projections.spread_model_home_prob is
  'The model''s CALIBRATED probability that the home team covers spread_line: the share of simulated margins covering, pushes excluded, pulled toward 50% by CALIBRATION_SLOPE (worker/core/game_picks.py, migration 0085). Edge = this minus the vig-free home price, or its mirror for the away side (CLAUDE.md §6). Shadow picks use the raw probability, not this.';
comment on column game_projections.total_model_over_prob is
  'The model''s CALIBRATED probability the game goes over total_line: the share of simulated totals over, pushes excluded, pulled toward 50% by CALIBRATION_SLOPE (migration 0085). Shadow picks use the raw probability, not this.';
