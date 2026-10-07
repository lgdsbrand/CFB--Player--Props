-- =============================================================================
-- 0083 — Flag a projection whose team has no prior season in the model
-- =============================================================================
-- FOUND THE DAY THE EDGE WENT PUBLIC (2026-10-06). North Dakota State and
-- Sacramento State joined FBS in 2026. Ingest covers FBS games, so the model
-- has no 2025 season for NDSU and one game for Sacramento State, rates both
-- far below the market, and their games topped the new Lines table by edge:
-- NDSU @ UNLV +47.3% (book NDSU -3, model UNLV -24.4), Sacramento State @
-- Bowling Green +39.1%. Every other team that week had a full FBS season.
--
-- THE USER'S DECISION: for such a game the site shows the book's lines but
-- not the model's spread, total, win chance or edges, with a note saying why.
-- The shadow picks are unchanged (they are a private, graded test and still
-- price these games), so this is a display flag, not a model change.
--
-- Set by `run_game_model` when either team completed fewer than
-- `MIN_PRIOR_GAMES` games in the previous season in our data
-- (worker/core/game_model.py). An addition with a default, so rows written by
-- older code read as "has a prior season", which was the old behaviour.
-- =============================================================================

alter table game_projections
  add column missing_prior_season boolean not null default false;

comment on column game_projections.missing_prior_season is
  'True when either team completed fewer than MIN_PRIOR_GAMES games last season in our data — in practice a team new to FBS, whose previous season the model never saw. The site then shows the book''s lines but none of the model''s numbers or edges for the game (user decision 2026-10-06). Picks are unaffected. See migration 0083.';
