-- =============================================================================
-- 0025 — ai_adapter back to 'none': the key is still on the free tier
-- =============================================================================
-- Reverts 20260803140000 the same day. That migration switched the reads on
-- because the client reported enabling billing. The key does not agree.
--
-- THE EVIDENCE, from the 429 body rather than from a retry count:
--
--   "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
--   "quotaValue": "20"
--   metric: generativelanguage.googleapis.com/generate_content_free_tier_requests
--
-- Twenty requests per day, free tier, for gemini-3.6-flash. Exactly 20 reads
-- were written for 2025 week 13 and every subsequent call returned 429 with
-- nothing generated. Billing is presumably enabled on a Google Cloud project
-- that this key does not belong to — an AI Studio key issued before billing,
-- or issued against a different project. A NEW key from the billed project is
-- what resolves it, not waiting.
--
-- WHY THIS REVERTS RATHER THAN WAITS. CLAUDE.md §0 treats key and data hygiene
-- as a hard rule, and the standing instruction on this seam has always been the
-- same: Google's FREE tier uses submitted content to improve their products and
-- the PAID tier does not, and these prompts carry the client's model
-- projections. While the key resolves to the free tier, every run of
-- generate_ai_reads submits the client's numbers to a training tier, 20 a day,
-- unattended, on a Wednesday cron. 'none' is a supported product state — the
-- player page renders the empty read slot it was designed around — so the safe
-- position costs nothing but the reads themselves.
--
-- WHAT ALREADY WENT. 20 prompts for 2025 week 13 (9,619 input tokens) were
-- submitted on the free tier before this was understood. Each carries a player
-- name, team, opponent, a projected median or scoring probability, an
-- opponent-adjusted defensive rank and recent box-score lines. Derived numbers
-- about public college football performances — no credentials, no personal
-- data, no client-identifying information — but submitted to a tier that trains
-- on them, which is precisely what the gate existed to prevent. The client
-- needs to know it happened; the cached rows themselves are fine and are kept.
--
-- TO TURN THIS BACK ON: confirm the key is issued from the project with billing
-- active — the probe is one request, and a paid key's 429 (if any) names a
-- per-minute quota, never `...FreeTier`. Then re-apply 20260803140000's value
-- as a new migration.
-- =============================================================================

update app_config
   set value = '"none"'::jsonb,
       description =
         'Which provider writes the weekly cached reads. Reverted to ''none'' '
         'on 2026-08-03: the Gemini key resolves to the FREE tier (429 '
         'GenerateRequestsPerDayPerProjectPerModel-FreeTier, 20/day), and the '
         'free tier trains on submitted content while these prompts carry the '
         'client''s model projections (CLAUDE.md §0). Not a capacity problem — '
         'a key issued from a project without billing. Switch to ''gemini'' '
         'once a key from the billed project is in the worker environment. '
         '''none'' is a supported state: the player page keeps its empty read '
         'slot, cached rows survive, and monitor_pipeline stops expecting the '
         'job.'
 where key = 'ai_adapter';
