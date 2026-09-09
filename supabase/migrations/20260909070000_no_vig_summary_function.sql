-- =============================================================================
-- 0060 -- no_vig_summary: the page's total AND its market pills in one scan
-- =============================================================================
-- `/no-vig` fires THREE heavy statements per render: the row page, an exact
-- count for the truncation banner, and `v_no_vig_markets` for the filter pills.
-- All three scan `v_no_vig_rows`, which is ~600 ms warm. Under twelve
-- concurrent readers that page failed 12 of 12 on the live site even after
-- migrations 0058 and 0059 fixed the board — same diagnosis as the board's
-- banner counts, same fix: stop asking for the same scan more than once.
--
-- This collapses the count and the pills into ONE scan, taking the render from
-- three statements to two.
--
-- ---------------------------------------------------------------------------
-- TWO SCOPES, ONE PASS, AND THEY ARE DELIBERATELY DIFFERENT
-- ---------------------------------------------------------------------------
-- The pills and the total do NOT answer the same question, which is why this
-- returns two numbers per market rather than one:
--
--   `quotes`   — the BASE scope: this market's two-way quotes on the slate,
--                ignoring the position/market/shoppable filters. The pills must
--                ignore them, or selecting QB would hide every market a QB does
--                not play and the reader could never get back.
--   `matching` — the page's FULL filter. Summed across markets this is exactly
--                the count `getNoVigCount` used to fetch on its own, and it is
--                what the "Partial slate" banner reports.
--
-- Computing both in one pass is the whole point: `count(*) filter (...)` costs
-- a predicate per row, not a second scan.
--
-- `conference_is_displayed` is hardcoded rather than parameterised because both
-- callers already hardcode it (CLAUDE.md §7 — the board's scope). If that ever
-- becomes a control, it becomes a parameter here in the same change.
--
-- A NULL `position_group` row can never match a position filter, which is the
-- same answer PostgREST's `eq` gives — it excludes nulls. Stated because the
-- three-valued logic is easy to get backwards when writing it as an `or`.
--
-- `v_no_vig_markets` IS NOT DROPPED. Nothing will read it after this, but
-- dropping it is a removal, and migration 0055 took the live page down earlier
-- today by removing something the deployed code still read. It can go in a
-- later migration once this has shipped. See the three-step rule in
-- `migration-before-push-is-wrong-for-removals`.
--
-- Additive: nothing reads this yet, so it is safe to land ahead of the code.
-- =============================================================================

create or replace function no_vig_summary(
  p_sport            sport,
  p_season           integer,
  p_week             integer,
  p_kickoff_cutoff   timestamptz    default null,
  p_position_group   position_group default null,
  p_market_key       text           default null,
  p_shoppable_only   boolean        default false
)
returns table (
  market_key   text,
  market_label text,
  quotes       bigint,
  matching     bigint
)
language sql
stable
as $$
  select
    r.market_key,
    r.market_label,
    count(*)                                     as quotes,
    count(*) filter (
      where (p_position_group is null or r.position_group = p_position_group)
        and (p_market_key     is null or r.market_key     = p_market_key)
        and (not p_shoppable_only     or r.books_at_line  > 1)
    )                                            as matching
  from v_no_vig_rows r
  where r.sport  = p_sport
    and r.season = p_season
    and r.week   = p_week
    and r.conference_is_displayed
    -- A NULL cutoff counts everything; a NULL start_date is a TBD kickoff and
    -- counts as upcoming. Both match `upcomingOnly` and `lib/core/kickoff.ts`.
    and (p_kickoff_cutoff is null
         or r.start_date is null
         or r.start_date >= p_kickoff_cutoff)
  group by r.market_key, r.market_label;
$$;

comment on function no_vig_summary(sport, integer, integer, timestamptz, position_group, text, boolean) is
  'The /no-vig page''s market pills and its exact total in ONE scan of v_no_vig_rows, replacing a separate count and a separate read of v_no_vig_markets. `quotes` is the base slate scope the pills need; `matching` applies the page''s position/market/shoppable filters and sums to the total the truncation banner reports. Two numbers per market because the pills must NOT narrow with the filters, or selecting a position would hide the markets that position does not play.';
