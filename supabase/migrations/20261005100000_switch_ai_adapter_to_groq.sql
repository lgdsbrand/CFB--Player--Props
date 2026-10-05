-- =============================================================================
-- 0080 — Switch ai_adapter: 'none' -> 'groq'
-- =============================================================================
-- A MIGRATION AND NOT A BARE UPDATE, for the reason 0024 gives: a fresh
-- `db push` must reproduce the live value, and a hand-run UPDATE would not.
--
-- WHY GROQ. The client's own site runs on Groq (his README: "Groq (Llama 3
-- 70B)"), so a Groq key is the one he already has. Gemini was switched on and
-- back off in August because his key was a free-tier key; Grok was never used.
-- Groq is NOT Grok: it is the inference host at api.groq.com, adapter
-- `worker/adapters/ai/groq.py`, default model openai/gpt-oss-120b (Groq shut
-- down llama-3.3-70b-versatile on 2026-08-16).
--
-- SET AHEAD OF THE KEY, ON PURPOSE. The user's instruction (2026-10-05) is
-- that adding the key should be the only step left. With this row set and no
-- GROQ_API_KEY on the service, `generate_ai_reads` records a run with
-- `awaiting_key = 'GROQ_API_KEY'` and exits 0, and `monitor_pipeline` lists the
-- job as waiting for that key on every check. The weekly run after the key is
-- added generates the reads.
--
-- APPLY AFTER THE CODE THAT KNOWS 'groq' IS DEPLOYED. Code older than this
-- migration raises "Unknown AI adapter 'groq'" in the Wednesday job and fails
-- the daily audit check "ai_adapter names a provider we ship".
--
-- Reversible: set it back to 'none'. Cached reads are not deleted.
-- =============================================================================

update app_config
   set value = '"groq"'::jsonb,
       description =
         'Which provider writes the weekly cached AI reads: none, gemini, grok '
         'or groq. Set to groq 2026-10-05 because the client''s own site runs '
         'on Groq. The key lives on the Render service (GROQ_API_KEY), never '
         'here; until it is added the job records that it is waiting for it '
         'and exits 0. Set to none to switch the reads off.'
 where key = 'ai_adapter';
