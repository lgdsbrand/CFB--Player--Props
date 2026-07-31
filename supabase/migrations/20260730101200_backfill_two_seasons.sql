-- =============================================================================
-- 0012 — Narrow the initial backfill to two seasons
-- =============================================================================
-- Phase 2 validates the position-split engine and the opponent adjustment end
-- to end before committing storage. play_player_stats runs roughly 1M rows per
-- season across all FBS, and the development database is on Supabase's free
-- tier, so the backfill starts at two complete seasons and widens once the
-- splits have been spot-checked and look sane.
--
-- 2024 and 2025 are the two most recent COMPLETE seasons — the 2026 season has
-- not kicked off yet as of this migration.
--
-- Widening later is a config change plus an incremental ingest, not a rebuild:
-- nothing in the schema assumes a season count.
-- =============================================================================

update app_config
   set value = '[2024, 2025]'::jsonb,
       description = 'Seasons the Phase 2 backfill covers. Starts at two complete '
                     'seasons to validate the position-split engine before committing '
                     'storage — play_player_stats runs roughly 1M rows per season '
                     'across all FBS. Widen once the splits look sane.'
 where key = 'backfill_seasons';
