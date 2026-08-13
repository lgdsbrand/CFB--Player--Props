-- =============================================================================
-- The season the in-season crons operate on, and the storage cap they check.
-- =============================================================================
-- WHY THIS EXISTS. Every scheduled job resolved its season list from
-- `app_config.backfill_seasons`, which on 2026-08-13 was `[2024, 2025]`. One key
-- was answering two different questions:
--
--   * which seasons does the historical BACKFILL cover, and
--   * which season do the WEEKLY crons refresh.
--
-- Those diverge the moment a new season starts, and the divergence was silent.
-- Measured on production that morning: the daily `ingest_game_lines` cron had
-- rewritten 2024 (2,517 rows) and 2025 (2,622 rows) at 10:00 that same day,
-- while 2026's 104 rows had not been touched since a manual run the day before.
-- The cron had never once fetched the season about to be played, so the 48
-- unpriced week-1 games would have stayed unpriced through kickoff, and nothing
-- reported a fault: every job succeeded, at the wrong season.
--
-- `backfill_seasons` keeps its original meaning (see
-- 20260730101200_backfill_two_seasons.sql, which narrowed it to two seasons for
-- the free tier). `current_season` is the new, separate answer for the crons.
--
-- SET THIS EVERY AUGUST. It is deliberately a row edit rather than a derived
-- value: "which season is it" is ambiguous for eight months of the year, and a
-- clock-based guess would silently roll over mid-bowl-season and start writing
-- next year's empty schedule over a live one.
-- =============================================================================

insert into app_config (key, value, description) values
  ('current_season', '2026'::jsonb,
   'The season the in-season crons operate on, used by the jobs run with --current. Separate from backfill_seasons, which scopes the historical backfill. UPDATE THIS EACH AUGUST: nothing derives it from the clock, because a date-based guess rolls over during bowl season and would start writing the next season''s empty schedule over a live one.'),

  ('db_size_cap_mb', '500'::jsonb,
   'Storage ceiling ingest_stats checks before starting, in MB. 500 is the Supabase free tier, where exceeding the cap makes the project READ-ONLY and every worker write fails. Raise this to the plan''s real limit when the project moves to Pro; it is configuration so the upgrade is a row edit and not a deploy.')
on conflict (key) do nothing;
