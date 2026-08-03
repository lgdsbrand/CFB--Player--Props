-- =============================================================================
-- 0024 — Switch ai_adapter on: 'none' -> 'gemini'
-- =============================================================================
-- A MIGRATION AND NOT A BARE UPDATE, on purpose. docs/configuration.md promises
-- that a fresh `db push` reproduces the live values exactly, and that where
-- "seeded" and "current" differ a later migration did it deliberately. An
-- UPDATE run by hand breaks both halves of that: the value would be live and
-- unreproducible, which is the same drift Phase 5 already had to repair once in
-- `supabase_migrations.schema_migrations`.
--
-- WHAT THIS UNBLOCKS. The adapter, prompt, cache key and player-page slot all
-- shipped in 5b and have been sitting behind this row. Until now nothing could
-- be generated for real: Google's FREE tier trains on submitted content and
-- these prompts carry the client's model projections, so the standing
-- instruction was to validate on free and enable billing before generating
-- anything. **The client enabled billing on 2026-08-03.** That is the only
-- thing that changed, and it is the only thing this row was waiting on.
--
-- WHY GEMINI RATHER THAN GROK, restated because this row is where the choice
-- actually takes effect and cost is not the reason:
--   * The job is a weekly BURST of ~2,000 latency-insensitive calls, which is
--     the shape Gemini's Batch API is built for. Grok would be paced against a
--     rate limit.
--   * Grok's real edge is live X grounding — genuinely attractive for injury
--     and depth-chart news, and unusable here. It makes identical inputs
--     produce different reads, which breaks `ai_reads.input_digest` and means a
--     read could never be reproduced or audited. This project is built on
--     replayable point-in-time inputs (CLAUDE.md §4).
-- Both adapters are built and `grok` stays a one-row change away.
--
-- WHAT ELSE THIS TURNS ON, which is easy to miss: `monitor_pipeline` gates the
-- `generate_ai_reads` expectation on this key (`enabled_key="ai_adapter"`).
-- From now on a week with no reads is a WARNING finding at 200 hours, where
-- before it was a deliberate configuration the monitor stayed quiet about.
-- That is the intended consequence — an off switch nobody is watching and a
-- broken job nobody is watching look identical from the board.
--
-- Reversible: set this back to 'none' and the player page returns to the empty
-- read slot it was designed around. Cached rows are not deleted by doing so.
-- =============================================================================

update app_config
   set value = '"gemini"'::jsonb,
       description =
         'SWITCHED ON 2026-08-03, once the client enabled Gemini billing — the '
         'free tier trains on submitted content and these prompts carry the '
         'client''s model projections (CLAUDE.md §0). Which provider writes the '
         'weekly cached reads; the API key being set is not what decides it. '
         'Gemini over Grok because the job is a weekly burst of ~2,000 '
         'latency-insensitive calls rather than a conversation, and because '
         'Grok''s live X grounding would make identical inputs produce '
         'different reads, breaking ai_reads.input_digest and with it any '
         'chance of reproducing or auditing a read. Set back to ''none'' to '
         'stop generating; the player page keeps its empty read slot and '
         'cached rows survive. Note this also arms monitor_pipeline''s '
         'generate_ai_reads expectation.'
 where key = 'ai_adapter';
