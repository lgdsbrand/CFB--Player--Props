-- =============================================================================
-- 0059 -- board_counts: six counts in one scan instead of six
-- =============================================================================
-- THE FREE HALF OF THE BOARD'S CONCURRENCY CEILING. `getBoardCounts` fires SIX
-- exact counts over `v_board_rows` in parallel -- identical base filter, one
-- extra predicate each -- so a single board render evaluates that view six
-- times just to fill the banner numbers, plus twice more for the row count and
-- the row page.
--
-- Measured on production 2026-09-09, cfb 2026 week 2:
--
--   six counts in parallel, as the page fires them   1.78-1.94 s each,
--                                                    2,012 ms wall clock
--   the same six as count(*) filter in ONE query        ~100 ms
--
-- Each count is only ~100 ms ALONE. They take 1.8 s each because they are
-- competing with each other for the same CPU, which is the whole diagnosis:
-- twelve concurrent readers currently mean SEVENTY-TWO simultaneous evaluations
-- of this view. One per render makes that twelve.
--
-- This does not make the database faster. It stops asking it to do the same
-- work six times, which is the lever that costs nothing --
-- `board-ceiling-is-cpu-not-query-shape`. The alternative is a bigger Supabase
-- compute instance, which is a billing decision.
--
-- ---------------------------------------------------------------------------
-- A FUNCTION, NOT A VIEW, AND THE REASON IS THE PARAMETERS
-- ---------------------------------------------------------------------------
-- `v_no_vig_markets` solves the same "PostgREST refuses aggregates for anon"
-- problem with a grouped view. That does not work here: two of the six counts
-- depend on values known only at request time -- the edge threshold, which
-- comes from `app_config` through the app, and the kickoff cutoff, which is the
-- 04:00 ET slate-day rollover computed in `lib/core/slate-days.ts`. A grouped
-- view would have to bake both in, and baking the rollover into SQL is exactly
-- the duplication migration 0055 was written to avoid.
--
-- So the caller passes them, the same way it passes them to the six queries
-- today. `defense_position_splits_through` is the existing precedent for an
-- RPC on this project.
--
-- BOTH CONSTANTS STAY IN THE APP. `p_synthetic_book_key` is passed rather than
-- hardcoded as 'dev' here, because that key already has two homes to keep in
-- step (`lib/data/odds.ts` and `worker/jobs/run_projections.py`) and a third
-- copy buried in SQL is how they drift.
--
-- SECURITY INVOKER by default, which is what we want: `v_board_rows` is a
-- security_invoker view and the caller is anon, so the function must not
-- escalate. STABLE, not IMMUTABLE -- it reads tables.
--
-- Additive: nothing reads this yet, so it can land before the code that calls
-- it. That is the safe ordering, unlike migration 0055 earlier the same day.
-- =============================================================================

create or replace function board_counts(
  p_sport              sport,
  p_season             integer,
  p_week               integer,
  p_edge_threshold     numeric,
  p_synthetic_book_key text,
  p_displayed_only     boolean     default true,
  p_kickoff_cutoff     timestamptz default null
)
returns table (
  rows_all             bigint,
  with_call            bigint,
  with_book_line       bigint,
  with_dev_line        bigint,
  with_even_book_price bigint,
  over_threshold       bigint
)
language sql
stable
as $$
  select
    count(*),
    count(*) filter (where b.has_call),
    count(*) filter (where b.has_book_line),
    count(*) filter (where b.sportsbook_key = p_synthetic_book_key),
    -- Exactly 0.500, not "close to it". The caveat this feeds claims edge IS
    -- confidence minus 50%, which is only exactly true at the midpoint, so a
    -- tolerance here would put rows under a notice that misdescribes them.
    count(*) filter (where b.book_prob_over = 0.5
                       and b.sportsbook_key <> p_synthetic_book_key),
    count(*) filter (where b.edge >= p_edge_threshold)
  from v_board_rows b
  where b.sport  = p_sport
    and b.season = p_season
    and b.week   = p_week
    and (not p_displayed_only or b.conference_is_displayed)
    -- A NULL cutoff means "count everything", matching the caller that omits
    -- it. A NULL start_date is a TBD kickoff and counts as upcoming, the same
    -- call `upcomingOnly` and `lib/core/kickoff.ts` make.
    and (p_kickoff_cutoff is null
         or b.start_date is null
         or b.start_date >= p_kickoff_cutoff);
$$;

comment on function board_counts(sport, integer, integer, numeric, text, boolean, timestamptz) is
  'The board banner''s six counts in ONE scan of v_board_rows. Replaces six parallel exact counts that measured 2,012 ms of wall clock together against ~100 ms for this -- each is ~100 ms alone and they were competing for the same CPU. A function rather than a grouped view because the edge threshold and the 04:00 ET kickoff cutoff are only known at request time, and baking the slate-day rollover into SQL is the duplication migration 0055 exists to avoid.';
