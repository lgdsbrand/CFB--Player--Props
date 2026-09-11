-- =============================================================================
-- 0065 — First-quarter NFL markets, INACTIVE: rows for book lines to point at
-- =============================================================================
-- WHY ROWS NOW, WHY INACTIVE
-- --------------------------
-- player_prop_lines.market_key references markets(key), so a DraftKings
-- first-quarter line cannot be stored until its market exists. Lines not
-- captured before kickoff can only be bought later from the historical
-- endpoint at 10x the credits, so the capture cannot wait for the board work.
--
-- is_active = false keeps them off BOTH boards: `projections.market_catalogue()`
-- and web `catalogue.ts readMarkets()` read active markets only. That is what
-- lets these rows ship before `markets` has a sport column -- activating them
-- without one would put first-quarter tabs on the college board.
--
-- NOT hidden from /no-vig, on purpose. mv_no_vig_rows (0062) joins markets
-- without reading is_active, so captured lines appear there as DraftKings
-- first-quarter pills. They are real two-way prices, which is what that page
-- is for; hiding them would mean rebuilding the materialised view.
--
-- WHICH MARKETS
-- -------------
-- The three yardage markets DraftKings posts, and nothing else a book offers
-- (there is no first-quarter receptions or passing-TD market anywhere).
-- q1_anytime_td is left out: one-way on every book, so it can never be
-- de-vigged or graded, and the model needs no line to state a probability.
--
-- Families match `projections.FIRST_QUARTER_FAMILIES`. No market_positions
-- rows: first_quarter_catalogue() derives positions from the parent market, and
-- an inactive market needs none until it is shown.
--
-- ladder_step is 5 because markets_ladder_step_matches_binary (0026) requires a
-- positive whole step on every non-binary market. It is a placeholder the
-- constraint forces, not a display decision -- that is made when these go live.
-- =============================================================================

insert into markets
  (key, display_name, short_label, emoji, stat_column, distribution_family,
   is_binary, default_line, unit, sort_order, is_active, ladder_step) values
  ('q1_pass_yards', 'Q1 Passing Yards',   'Q1 PASS YDS', '🎯', 'q1_pass_yards', 'gamma',        false, null, 'yards', 110, false, 5),
  ('q1_rush_yards', 'Q1 Rushing Yards',   'Q1 RUSH YDS', '🏃', 'q1_rush_yards', 'hurdle_gamma', false, null, 'yards', 150, false, 5),
  ('q1_rec_yards',  'Q1 Receiving Yards', 'Q1 REC YDS',  '🙌', 'q1_rec_yards',  'hurdle_gamma', false, null, 'yards', 180, false, 5);
