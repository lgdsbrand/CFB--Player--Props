-- =============================================================================
-- 0056 -- put is_upcoming back beside start_date, so a deploy can lag a migration
-- =============================================================================
-- MIGRATION 0055 TOOK THE LIVE PAGE DOWN FOR THE MINUTES IT TOOK TO NOTICE.
-- It replaced `v_no_vig_markets.is_upcoming` with `start_date`, and the code
-- that reads `is_upcoming` was still the code deployed on Vercel. PostgREST
-- answers a select for a column that does not exist with a 400, so the moment
-- 0055 landed on production, `/no-vig` returned 500 for BOTH sports -- measured,
-- not theorised, on https://cfb-player-props.vercel.app.
--
-- THE RULE THIS BREAKS. "Migration before push" is the sequence this project has
-- proven eight times, and it is correct for an ADDITIVE migration: the new
-- column has to exist before the code that reads it ships. It is exactly WRONG
-- for a removal, where the old code is still running and still reading the old
-- column. A migration that drops or renames anything has to be split in two:
--
--   1. add the new shape, keep the old one   -- deploy-safe in either order
--   2. deploy the code
--   3. drop the old shape, in a later migration, once nothing reads it
--
-- This is step 1 done late. `is_upcoming` comes back exactly as it was, so the
-- currently-deployed code works again; `start_date` stays, so the fixed code
-- works too. Neither reader can break the other.
--
-- `is_upcoming` IS STILL WRONG AND IS STILL THE REASON FOR 0055. It cuts at
-- `now()` where the table cuts at 04:00 ET, so it under-reports the slate all
-- day on a game day -- 84 rec_yards quotes against the table's 343, measured on
-- 2026 cfb week 1 at Sat 2026-09-05 20:00 ET. Nothing should read it. It is kept
-- only so a deploy may lag a migration, and it should be dropped in a later
-- migration once `getNoVigMarkets` has shipped on `start_date`.
--
-- Grouping on both adds no rows: `is_upcoming` is a function of `start_date` and
-- `now()`, and `start_date` is already in the GROUP BY. Measured worst case for
-- the scope the page reads is 120 rows, against PostgREST's 1,000-row cap.
-- =============================================================================

drop view if exists v_no_vig_markets;

create view v_no_vig_markets
with (security_invoker = true)
as
select
  r.season,
  r.week,
  r.sport,
  r.market_key,
  r.market_label,
  r.market_emoji,
  r.conference_is_displayed,
  -- The game's kickoff, for the caller to cut against its own slate-day cutoff.
  -- NULL is a TBD kickoff and stays in: a null cannot be proven to be in the
  -- past, and `upcomingOnly` keeps it for exactly that reason.
  r.start_date,
  count(*)                                        as quotes,
  count(*) filter (where r.books_at_line > 1)     as shoppable,
  -- DEPRECATED, kept only so a deploy may lag a migration. See the header:
  -- this cuts at now() and the table it sits above cuts at 04:00 ET.
  (r.start_date is null or r.start_date >= now()) as is_upcoming
from v_no_vig_rows r
group by 1, 2, 3, 4, 5, 6, 7, 8, 11;

comment on view v_no_vig_markets is
  'Per-market quote counts for the no-vig page''s filter, one row per (season, week, sport, market, conference scope, KICKOFF). Exists because PostgREST refuses aggregates for anon and because counting in the page would count a truncated fetch. Cut start_date with the CALLER''s slate-day cutoff; is_upcoming is deprecated and cuts at now(), which disagrees with the table it sits above. ALWAYS filter on season and week, which lead the GROUP BY.';

comment on column v_no_vig_markets.start_date is
  'Kickoff of the game these quotes are on. Cut it with the caller''s own cutoff (`upcomingOnly` in lib/data/query.ts), never with now() -- a started game stays on this page until the slate day rolls over at 04:00 ET, and a boolean computed here could not know that. NULL is a TBD kickoff and counts as upcoming.';

comment on column v_no_vig_markets.is_upcoming is
  'DEPRECATED -- cuts at now(), while the table this filter sits above cuts at the 04:00 ET slate-day rollover, so it under-reports all day on a game day (84 quotes against 343, measured 2026 cfb week 1). Kept only so a deployed reader can lag this migration. Use start_date and the caller''s own cutoff; drop this column once nothing reads it.';
