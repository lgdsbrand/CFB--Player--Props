-- =============================================================================
-- 0017 — Settle hit_rate_basis on 'threshold'
-- =============================================================================
-- CLAUDE.md §9.2 left this open with the client. Decided 2026-08-01 on merit,
-- not on cost.
--
-- WHY THRESHOLD. It is what a props board actually displays: "gone over 45.5 in
-- 7 of his last 10" is the CURRENT line applied retrospectively to past games,
-- which is the threshold method by definition. Every comparable product works
-- this way, so it is also what a user reading our board already expects a
-- hit rate to mean.
--
-- WHY NOT CLOSING LINE, beyond the cost. It can only grade a game where a book
-- actually posted a line for that player, and college books post props late and
-- selectively. The last-10 hit-rate chart is a headline feature (CLAUDE.md §7);
-- graded against closing lines a mid-tier receiver's chart would show three
-- bars out of ten. A sparse version of that chart is worse than not having it,
-- because the gaps read as "did not play" rather than "no line existed".
--
-- Separately: historical player props sit behind premium billing on a monthly
-- allowance shared with the client's other models, so for past seasons the
-- closing-line basis is not merely expensive, it is unavailable. That is a
-- budget fact that may change; the two reasons above are not.
--
-- THE PATH FORWARD IS STILL CLEAN. `player_prop_lines` is append-only and
-- already stores full line history, so once odds ingestion runs live, closing
-- lines accrue week by week with no re-ingest of anything else. When enough
-- have accumulated to grade against, this becomes a value change here rather
-- than a schema change — which is the whole reason it was built as config.
-- =============================================================================

update app_config
   set value = '"threshold"'::jsonb,
       description =
         'DECIDED 2026-08-01 (CLAUDE.md §9.2). Hit rates are graded by applying '
         'the current line retrospectively to past games — what every comparable '
         'props board displays, and what a reader already expects "7 of his last '
         '10" to mean. The alternative, grading against the historical closing '
         'line, can only score games where a book actually posted that player, '
         'and college books post late and selectively; the last-10 chart would '
         'be mostly gaps that read as "did not play". Still a config flag: '
         'player_prop_lines is append-only and accrues closing lines once live '
         'odds ingestion runs, so switching later is a value change here, not a '
         'schema change.'
 where key = 'hit_rate_basis';
