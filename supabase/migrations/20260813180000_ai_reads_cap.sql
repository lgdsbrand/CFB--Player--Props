-- =============================================================================
-- 0044 -- raise the AI-read cap to cover every call on the board
-- =============================================================================
-- The cap was 400, set as a spend guard BEFORE the spend was measured. It is
-- now measured, and the ordering it interacts with has been fixed (0267c26),
-- so the number can be chosen rather than guessed.
--
-- Measured on production, 2026 week 1: 1,206 players carry projections and
-- **601 of them carry a call** the board presents. A cap of 400 therefore
-- covered 67% of the calls and cut the rest — and before the ordering fix it
-- did not even cut them in a sensible order, because players were sorted by
-- player_id and the cap always served the 400 lowest ids.
--
-- 650 covers every call with room for the slate to grow. At the measured ~400
-- input tokens per read that is roughly 260k input tokens for a full week,
-- which is not a number worth protecting against at the cost of leaving a third
-- of the board's calls without a read.
--
-- IT IS STILL A CEILING, NOT A TARGET. Nothing changes about the run stopping
-- early and reporting what was left, and `input_digest` still means a re-run
-- costs only what it generates. Approved by the user on 2026-08-13.
-- =============================================================================

update app_config
   set value = '650'::jsonb,
       description =
         'Hard ceiling on generations in one run of generate_ai_reads. Raised '
         'from 400 on 2026-08-13, once the spend was measured: 601 of the '
         '1,206 players on 2026 week 1 carry a call, so 400 covered only 67% '
         'of them. The cap decides HOW MANY; generate_ai_reads.selection_rank '
         'decides WHICH, ordering call-first then edge then confidence to '
         'match the board default sort. Stopping early is recoverable; a '
         'surprise invoice is not.'
 where key = 'ai_reads_max_per_run';
