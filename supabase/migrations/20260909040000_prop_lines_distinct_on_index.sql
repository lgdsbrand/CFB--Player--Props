-- =============================================================================
-- 0057 -- the index the no-vig DISTINCT ON has always wanted
-- =============================================================================
-- `v_no_vig_rows` opens with a DISTINCT ON that keeps the latest quote per
-- (season, week, game, player, market, book). Postgres can answer a DISTINCT ON
-- with no sort at all when an index already delivers the rows in the ORDER BY's
-- order -- and no index did, so every read of the view sorted the week.
--
-- Measured on production before this index, 2026 nfl week 1:
--
--   Index Scan using player_prop_lines_lookup_idx   59,561 rows kept
--     Rows Removed by Filter: 22,427                 <- the whole table scanned
--   Incremental Sort  (59,561 rows)                  <- 800+ ms of the query
--   Unique -> 12,319 rows
--
-- `player_prop_lines_lookup_idx` is (game_id, player_id, market_key,
-- captured_at desc). It carries neither season nor week, so the week filter can
-- only be applied as a FILTER after the scan -- all 81,988 stored lines read to
-- keep 59,561 -- and its column order is a prefix of the DISTINCT ON key but
-- not the whole of it, so the sort still had to run (as an Incremental Sort,
-- which is why it was merely slow rather than catastrophic).
--
-- This index matches the DISTINCT ON key EXACTLY, in order, including
-- `captured_at desc` last. That buys two separate things:
--
--   * season and week LEAD the index, so `where season = ... and week = ...`
--     becomes a range scan and the week is the only thing read;
--   * the remaining columns are in the DISTINCT ON's own order, so the rows
--     arrive already grouped and the sort disappears entirely.
--
-- WHY THIS WAITED. It costs disk, and until 2026-09-09 there was none: prod ran
-- at 531 MB with 8.9 MB usable. **Supabase Pro was paid that day** and the guard
-- went to 2000 MB, at which point ~5 MB of index is noise. See
-- worker/worker/db.py and the storage note there.
--
-- NOT `create index concurrently`. CONCURRENTLY cannot run inside a transaction
-- block, and this migration is applied transactionally with its ledger row --
-- both halves or neither (docs/runbook.md). A plain CREATE INDEX takes an
-- ACCESS EXCLUSIVE lock, which on 82k narrow rows is a sub-second build; the
-- only writer is `ingest_odds` on a six-hourly cron, and this was applied
-- between runs. On a table an order of magnitude larger, do it CONCURRENTLY in
-- a migration of its own and record the ledger row separately.
--
-- THE OLD INDEX STAYS. `player_prop_lines_lookup_idx` still serves lookups that
-- name a game and player without a season, and dropping an index is a removal --
-- the class of change that took the live page down earlier the same day
-- (migration 0056). If it proves redundant, drop it in its own migration after
-- checking pg_stat_user_indexes.idx_scan.
-- =============================================================================

create index if not exists player_prop_lines_latest_idx
  on player_prop_lines (
    season, week, game_id, player_id, market_key, sportsbook_id,
    captured_at desc
  );

comment on index player_prop_lines_latest_idx is
  'Matches the DISTINCT ON key of v_no_vig_rows exactly, so the latest-quote-per-book de-duplication needs no sort and reads only the week asked for. Any change to that DISTINCT ON must change this index with it, or the view silently goes back to sorting the season.';
