-- =============================================================================
-- 0054 -- sport-dependent prior weighting
--
-- N5b. CLAUDE.md §6 down-weights prior-season production because of the
-- transfer portal and NIL: in college, last year's numbers were frequently put
-- up at another school, in another conference, in another scheme. That
-- reasoning is COLLEGE-SPECIFIC. It does not describe the NFL, and applying the
-- college haircut to NFL players would discard evidence that is demonstrably
-- good.
--
-- MEASURED, not assumed. Correlation between a player's prior-season per-game
-- average and their current-season per-game average, same method both sports,
-- players with >= 6 games in each season (prod, 2026-09-07):
--
--                     CFB 2022-25          NFL 2023-25
--                   r all  stay  move    r all  stay  move
--   RB rush yards   0.476 0.554 0.255    0.629 0.644 0.628
--   WR rec yards    0.440 0.569 0.206    0.672 0.693 0.535
--   TE rec yards    0.494 0.510 0.635    0.727 0.770 0.274
--   QB pass yards   0.377 0.404 0.346    0.373 0.542 0.119
--
-- Two things fall out of that table.
--
--   1. NFL priors are worth much more. On the three non-QB positions the NFL
--      correlation is roughly 1.4x college's, so the ceiling rises 0.5 -> 0.75.
--
--   2. Changing team barely hurts in the NFL and hurts a lot in college.
--      College roughly halves (RB .554->.255, WR .569->.206), which is what the
--      existing 0.5 multiplier encodes and this migration now writes down as
--      configuration instead of a constant. The NFL hardly moves for RB
--      (.644->.628) and WR (.693->.535), so its multiplier is 0.8.
--
-- KNOWN LIMITATION, DELIBERATELY ACCEPTED. The QB row shows NFL quarterback
-- priors are NOT more predictive than college ones (0.373 vs 0.377), so a
-- single per-sport ceiling over-weights NFL QB priors. Carving QB out would add
-- a position dimension to app_config for one cell of the table, on samples of
-- 65 and 19. Left alone on purpose; revisit when NFL grading gives an outcome
-- to fit against rather than a correlation to argue from.
--
-- THESE ARE UNFITTED. They are scaled from correlations, not tuned against
-- profit or calibration -- N5's agreed order is build now, grade later. Do not
-- quote them as evidence the NFL model works.
--
-- The suffix convention is `{key}_{sport}`, read by
-- `worker.db.get_config_value_for_sport`, which falls back to the bare key.
-- College gets NO suffixed row: its values ARE the base key, because every
-- number in app_config was measured on college over six phases.
-- =============================================================================

insert into app_config (key, value, description) values
  ('prior_season_weight_max_nfl', '0.75'::jsonb,
   'NFL ceiling on prior-season weight. Higher than college''s 0.5 because NFL '
   'priors measured far more predictive (r 0.63-0.73 vs 0.44-0.49 on RB/WR/TE). '
   'See migration 0054.'),
  ('changed_team_prior_multiplier', '0.5'::jsonb,
   'Extra haircut on prior-season weight when the player changed team. College: '
   'a transfer''s prior production says much less about their new role. Was a '
   'module constant until migration 0054.'),
  ('changed_team_prior_multiplier_nfl', '0.8'::jsonb,
   'NFL equivalent. Gentler than college''s 0.5 because changing team barely '
   'dented the correlation in the NFL (RB .644->.628, WR .693->.535) while it '
   'roughly halved it in college. See migration 0054.')
on conflict (key) do nothing;
