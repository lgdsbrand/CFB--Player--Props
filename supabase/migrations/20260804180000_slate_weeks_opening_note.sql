-- =============================================================================
-- 0027 — v_slate_weeks now legitimately contains weeks 1 and 2
-- =============================================================================
-- SUPERSEDES THE REASONING IN MIGRATION 0018, whose header states that "weeks 1
-- and 2 of any season are exactly that case and always will be ... the first
-- projectable week of a season is week 3". That was true when it was written and
-- is not any more. 0018's file is left as history; this is the correction, and
-- it lives on the object a reader inspects rather than in a file they would have
-- to know to open.
--
-- The VIEW ITSELF needed no change, which is the part worth noticing. It was
-- built to be driven from `projections` rather than from `games` precisely so
-- the selector could never offer a week that renders empty — so when Phase 6c
-- started publishing the opening weekends, the week selector picked them up with
-- no code change at all. A view defined over what exists beats one defined over
-- what someone expected to exist.
--
-- Phase 6b graded those weeks before they were published: weeks 1-2 came out the
-- best-calibrated stratum of the season, ECE 0.0184 against 0.0191 and 0.0215
-- for the rest. See docs/phase-6b-opening-weekend.md.
-- =============================================================================

comment on view v_slate_weeks is
  'Weeks with model output, for the week selector above the board. Driven from projections rather than games so the selector cannot offer a week that renders empty — which is why it needed no change when Phase 6c began publishing weeks 1 and 2, superseding migration 0018''s note that week 3 is the first projectable week. The kickoff range is what lets the strip label a week by its dates — college football is a weekly sport, so the date selector in CLAUDE.md §7 (built for the client''s daily MLB board) selects a week here and shows the span it covers.';
