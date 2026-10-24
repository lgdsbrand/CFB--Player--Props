-- =============================================================================
-- 0103 -- recycle PostgREST's connections every 10 minutes
-- =============================================================================
-- WHY. Production (Small, 2 GB) restarted uncleanly ~10 times on 2026-10-23,
-- with memory commitment over Supabase's own SEVERE line while CPU sat at ~5%.
-- Measured 2026-10-24: PostgREST prepares every statement it runs and keeps it
-- on that connection, and the web board builds different SQL for every filter
-- combination. 474 distinct v_board_rows statements in the 13 minutes after a
-- restart; one of them retains 0.5-2.4 MB on each connection it runs on
-- (EXPLAIN (MEMORY) and pg_backend_memory_contexts, measured). The pool holds
-- the same ~7 connections for up to 30 minutes, so that memory only grows.
--
-- WHAT. A schema-cache reload makes PostgREST flush its pool: tested on
-- production, 6 of 7 connections were replaced within 5 s (the 7th is its
-- LISTEN connection), with requests in flight left to finish. So a reload every
-- 10 minutes caps what any connection can accumulate, whatever the web sends.
-- The cost is one schema-cache query set (~2.5 s of catalog reads) per run.
--
-- This is the backstop, not the fix. The fix is the web reading the board with
-- a fixed set of statements (commit with this migration); this keeps memory
-- bounded if anything ever multiplies statement shapes again.
-- =============================================================================

create extension if not exists pg_cron;

select cron.schedule(
  'recycle-postgrest-pool',
  '*/10 * * * *',
  $$notify pgrst, 'reload schema'$$
);
