-- =============================================================================
-- 0067 — The first-quarter NFL markets go live
-- =============================================================================
-- APPLY THIS ONLY AFTER THE WEB CODE THAT FILTERS markets ON `sport` IS
-- DEPLOYED. Its own file, and separate from 0066, for exactly that reason.
--
-- 0066 added the columns; every one of them is additive and invisible to the
-- running site. THIS one is not. `readMarkets()` selects active markets, so the
-- moment these three rows flip, whatever version of the app is deployed puts
-- them in its stat selector -- and the version deployed before this work has no
-- sport predicate, which would list Q1 PASS YDS on the COLLEGE board, for a
-- market no college book anywhere posts.
--
-- That is the failure mode recorded in [[migration-before-push-is-wrong-for-removals]]
-- seen from the other side: there the migration removed something the live code
-- still read, here it reveals something the live code does not yet know how to
-- hide. Same rule -- a migration may only run ahead of a deploy while nothing a
-- reader can see depends on the difference.
--
-- WHAT IS ALREADY TRUE WHEN THIS RUNS
-- -----------------------------------
-- Rows exist (0065), the hurdle_gamma family exists (0064), the actuals they
-- grade against exist and reconcile (0063), and 0066 has told them which sport
-- they belong to, which market each is a segment of, and that they publish no
-- call. This flips one boolean.
--
-- WHAT THIS DOES NOT DO
-- ---------------------
-- It does not put a single row on the board by itself. The board is one row per
-- PROJECTION (v_board_rows), and no Q1 projection is written until
-- `run_projections --sport nfl` is run with the first-quarter markets enabled.
-- Nor does it attach a line: that is `capture_first_quarter`, hourly, and
-- DraftKings is the only book that posts these at all.
--
-- q1_anytime_td is deliberately still absent from `markets` entirely. It is
-- one-way at every book, so it can never be de-vigged or graded
-- ([[anytime-td-cannot-show-an-edge]]).
-- =============================================================================

update markets
   set is_active = true
 where key in ('q1_pass_yards', 'q1_rush_yards', 'q1_rec_yards');

-- Refuse to leave a Q1 market active without the metadata every reader needs.
-- An active market with no sport lands on both boards; one with no parent has
-- no positions, so it would appear in no stat selector at all and read as a
-- market that silently does nothing.
do $$
declare
  bad int;
begin
  select count(*) into bad
    from markets
   where is_active
     and key like 'q1\_%'
     and (sport is null or parent_market_key is null);
  if bad > 0 then
    raise exception
      'markets: % active first-quarter market(s) missing sport or parent '
      '(did migration 0066 run?)', bad;
  end if;
end $$;
