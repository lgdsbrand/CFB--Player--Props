-- =============================================================================
-- 0026 — A transfer-portal grouping for backtest metrics
-- =============================================================================
-- Migration 0016 said adding a grouping later would be "a new `group_kind` value
-- and not a migration", and then constrained the column to five literals. This
-- is that migration, and it is the constraint working as intended: a typo'd
-- group_kind should still be rejected.
--
-- WHY THIS CUT. Phase 6b opened the walk to weeks 1-2, where a projection is a
-- prior season and nothing else. Phase 6a measured that the prior does not
-- travel evenly: WR receptions correlate 0.402 year over year for players who
-- stayed and 0.112 for those who moved, RB rush attempts 0.481 against 0.208 —
-- while anytime TD holds up for both. So "can the board open with the season"
-- has two answers, and the honest fallback if the numbers disappoint is a
-- NARROWER opening board (returning starters on the same team) rather than no
-- board at all. That decision needs the two cells reported apart.
-- =============================================================================

alter table backtest_metrics
  drop constraint backtest_metrics_group_kind_known;

alter table backtest_metrics
  add constraint backtest_metrics_group_kind_known check (
    group_kind in ('overall', 'market', 'position', 'phase', 'season', 'transfer')
  );

comment on column backtest_metrics.group_kind is
  'Which dimension this row summarises: overall, market, position, phase (opening/early/late season), season, or transfer (season phase x whether the player changed team, which is what a weeks 1-2 projection rests on).';
