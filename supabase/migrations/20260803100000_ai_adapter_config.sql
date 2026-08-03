-- =============================================================================
-- Phase 5b: which AI provider writes the weekly reads.
-- =============================================================================
-- CLAUDE.md §9 wants an unconfirmed vendor choice to be configuration rather
-- than code, and §7 wants one short read per player per week, cached. The
-- client has Gemini and Grok keys and no Anthropic or OpenAI key.
--
-- DEFAULT IS 'none', deliberately. The pipeline runs, writes no reads, and the
-- player page keeps rendering the empty read slot it has had since Phase 4d.
-- Switching provider is this row, not a deploy.
--
-- NO KEY GOES IN THIS TABLE. app_config is world-readable under RLS and there
-- is an audit check for credential-shaped values here. Keys come from the
-- environment (GEMINI_API_KEY / GROK_API_KEY) and nowhere else.
-- =============================================================================

insert into app_config (key, value, description) values
  ('ai_adapter', '"none"'::jsonb,
   'Which AI provider generates the weekly player reads: "none", "gemini" or "grok". Default "none" — the job reports it is switched off, writes nothing and exits 0, and the app degrades to the empty read slot. The API key itself NEVER lives here; this table is world-readable (CLAUDE.md §0).'),

  ('ai_reads_max_per_run', '400'::jsonb,
   'Hard ceiling on generations in a single run. A busy week has ~1,700 players with projections, so an unbounded job is an unbounded bill against someone else''s account. The run stops at this number and reports how many were left, which is recoverable; a surprise invoice is not.'),

  ('ai_reads_min_confidence', '0.0'::jsonb,
   'Only generate reads for picks at least this confident. 0.0 means every projected player. Raising it is the cheapest way to cut spend, because the reads nobody opens are the ones on calls the model is least sure about.')
on conflict (key) do nothing;
