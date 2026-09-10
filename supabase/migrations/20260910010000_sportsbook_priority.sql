-- =============================================================================
-- 0061 -- headline the books the client can actually bet at
-- =============================================================================
-- The client opened the board and asked why every row said BetOnline.ag: "I
-- don't have that, would we be able to prioritize the primary like FanDuel,
-- DraftKings, fanatics?" He was right, and it was not a display bug.
--
-- `sportsbooks.priority` has existed since migration 0006 and `v_board_rows`
-- has always ordered its book lateral by `coalesce(bk.priority, 32767), pk.id`.
-- But NOTHING EVER SET IT. `ensure_sportsbooks` (worker/jobs/ingest_odds.py)
-- inserts `(key, display_name)` only, so every book that has ever arrived took
-- the column default of 100 and the whole table tied. A total tie makes the
-- tiebreak `pk.id` -- raw pick insertion order -- which is arbitrary, stable,
-- and happened to favour one book. Measured on production, 2026 week 1:
--
--     NFL      BetOnline.ag 779 of 811 board rows   96.1%
--     college  BetOnline.ag 966 of 1,029 board rows 93.9%
--
-- This was invisible for as long as it mattered least: until the odds adapter
-- went live on 2026-09-01 every line on the board was the synthetic DEV book,
-- so the tie had nothing to sort. It has been showing a real, unbettable book
-- since.
--
-- ---------------------------------------------------------------------------
-- WHY THIS COSTS NO COVERAGE -- the view ORDERS, it does not FILTER
-- ---------------------------------------------------------------------------
-- The lateral in `v_board_rows` picks the first row of an ordering over the
-- picks that EXIST for that player-market. Re-ranking the books therefore
-- cannot drop a row: a player-market that no primary book prices still shows,
-- headlined by whichever book does price it. That is CLAUDE.md §9.1's "degrade
-- gracefully" applied to the book axis, and it is why this is safe to apply to
-- a slate mid-week. Measured, 2026 week 1 player-markets with at least one
-- FanDuel / DraftKings / Fanatics quote:
--
--     NFL      1,052 of 1,062   99.1%
--     college  1,437 of 1,791   80.2%
--
-- The college fifth without a primary book is exactly the set that keeps its
-- current book, unchanged. Per-book odds were never hidden either way -- the
-- card lists every book; priority only decides which one is headlined.
--
-- ---------------------------------------------------------------------------
-- THE ORDER IS BY COVERAGE, NOT BY THE ORDER HE LISTED THEM
-- ---------------------------------------------------------------------------
-- He wrote "FanDuel, DraftKings, fanatics". DraftKings goes first anyway,
-- because FanDuel does not post three of our nine markets at all. NFL week 1:
--
--     DraftKings  1,027 player-markets across 9 markets
--     FanDuel       906 player-markets across 6  -- no pass_attempts,
--                                                   pass_completions,
--                                                   rush_attempts
--     Fanatics      588 player-markets across 9
--
-- Ranking FanDuel first would headline it wherever it competes and then hand
-- those three markets to a lower-priority book regardless, so the board would
-- be LESS consistent, not more. Coverage order gives the most rows on one book.
--
-- ---------------------------------------------------------------------------
-- WHY UPSERT RATHER THAN UPDATE, AND WHY EVERY OTHER BOOK IS LEFT AT 100
-- ---------------------------------------------------------------------------
-- A bare `update ... where key = 'fanatics'` is a silent no-op on any database
-- that has not seen that book yet -- and then `ensure_sportsbooks` inserts it
-- later at the default 100 and the ordering is quietly wrong again. Seeding the
-- row instead makes the priority true whether the book has been seen or not.
--
-- `do update` deliberately touches ONLY `priority`. `display_name` belongs to
-- the adapter, which learns it from the provider; overwriting it here would
-- fight the ingest every six hours.
--
-- The other books are NOT demoted explicitly. Leaving the default at 100 means
-- an unranked book -- including one that first appears years from now -- sorts
-- below all three primaries automatically, with no list to maintain. That is
-- the same reasoning migration 0021 used to reject a hardcoded provider name
-- for game lines. Gaps of ten leave room to rank a fourth book without
-- renumbering.
-- =============================================================================

insert into sportsbooks (key, display_name, priority) values
  ('draftkings', 'DraftKings', 10),
  ('fanduel',    'FanDuel',    20),
  ('fanatics',   'Fanatics',   30)
on conflict (key) do update set priority = excluded.priority;

comment on column sportsbooks.priority is
  'Lower sorts first when choosing which book to HEADLINE on a card; per-book odds are still shown for every book. Set by migration 0061 to the books the client can actually bet at, ordered by measured market coverage (DraftKings 10, FanDuel 20, Fanatics 30). Everything else keeps the default 100 on purpose, so an unranked or brand-new book sorts below the primaries with no list to maintain. NOTE: ingest_odds.ensure_sportsbooks does not write this column -- it relies on that default.';
