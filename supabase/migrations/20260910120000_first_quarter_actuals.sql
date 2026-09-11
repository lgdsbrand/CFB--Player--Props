-- =============================================================================
-- 0063 -- first-quarter actuals on player_game_stats
-- =============================================================================
-- The client asked for first-quarter props on the NFL (2026-09-10). Books post
-- four Q1 player markets -- anytime TD, and passing, rushing and receiving
-- yards, the yardage three DraftKings-only. Grading them, and giving the player
-- page a Q1 game log and hit rate, needs Q1 actuals, and no provider serves
-- those per player. We already hold the raw material: every play carries its
-- quarter (`plays.period`) and is attributed to players in `play_player_stats`.
--
-- ---------------------------------------------------------------------------
-- WHY COLUMNS HERE AND NOT A TABLE OF THEIR OWN
-- ---------------------------------------------------------------------------
-- `player_game_stats` is the only home for realised outcomes (0004), and
-- `markets.stat_column` names a column of it: grading, hit rates and the
-- backtest all resolve a market that way. A Q1 market is then a catalogue row
-- whose stat_column is `q1_rec_yards`, and nothing needs a second code path.
--
-- ---------------------------------------------------------------------------
-- DERIVED, NOT INGESTED -- and proven before these columns existed
-- ---------------------------------------------------------------------------
-- `build_quarter_stats` fills them from play attribution (worker/core/
-- quarter_stats.py). Its derivation, run over ALL quarters on 2025 NFL
-- player-games (19,400 rows on production), reproduces the box score exactly
-- for completions, pass TDs, carries, rush TDs, receptions and receiving TDs;
-- on 99.99% of rows for passing and rushing yards; 99.90% for receiving yards
-- (worst gap 33, a lateral); 99.54% for targets, every row within 2. The same
-- derivation filtered to quarter 1 is what lands here.
--
-- The NFL box-score ingest upserts only the columns it sends, so re-ingesting a
-- game leaves these untouched; the daily chain re-derives them after the plays.
--
-- ---------------------------------------------------------------------------
-- NULL MEANS NOT DERIVED; ZERO MEANS DID NOTHING IN THE QUARTER
-- ---------------------------------------------------------------------------
-- A game with no play-by-play keeps NULL, so a hit rate excludes it rather than
-- grading it as a zero. That is also why `q1_offensive_tds` is a PLAIN column
-- and not generated like `offensive_tds`: coalescing two NULLs to 0 would turn
-- "not derived" into "did not score". One statement writes all three TD
-- columns, so they cannot drift apart.
--
-- ---------------------------------------------------------------------------
-- NO PASS ATTEMPTS, deliberately
-- ---------------------------------------------------------------------------
-- A sack carries the passer's `Incompletion` row in the NFL attribution: 2025
-- completions plus incompletions total 19,819 against 18,369 box-score
-- attempts, the gap being ~1,371 sacks. An attempts column would be wrong in a
-- way nothing downstream could see, and no book posts a Q1 attempts market.
--
-- ADD COLUMN with no default changes the catalogue only: no table rewrite and
-- no long lock. A generated STORED column would have rewritten every row.
-- =============================================================================

alter table player_game_stats
  add column q1_pass_yards    smallint,
  add column q1_rush_attempts smallint,
  add column q1_rush_yards    smallint,
  add column q1_rush_tds      smallint,
  add column q1_targets       smallint,
  add column q1_receptions    smallint,
  add column q1_rec_yards     smallint,
  add column q1_rec_tds       smallint,
  add column q1_offensive_tds smallint;

comment on column player_game_stats.q1_pass_yards is
  'First-quarter passing yards: plays.yards_gained on the plays this player completed. From the play rather than play_player_stats because NFL Completion rows carry no yards. NULL = not derived (no play-by-play for the game).';

comment on column player_game_stats.q1_targets is
  'First-quarter targets: plays with a Reception or a Target row for this player. Target rows exist on incompletions only, so the two never double-count.';

comment on column player_game_stats.q1_offensive_tds is
  'First-quarter rushing + receiving touchdowns, the offensive_tds definition applied to quarter 1 -- a Touchdown row counts only with the same player''s Rush or Reception on the play, which excludes the passer. A plain column, not generated, so NULL (not derived) is never coalesced into 0 (did not score). Written with the other q1_ columns by build_quarter_stats.';
