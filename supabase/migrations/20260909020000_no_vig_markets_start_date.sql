-- =============================================================================
-- 0055 -- v_no_vig_markets buckets by kickoff, not by a boolean of its own
-- =============================================================================
-- THE BUG. `/no-vig` cut its market pills and its table with two different
-- rules, so the filter above the table described a quarter of the slate below
-- it:
--
--   pills : v_no_vig_markets.is_upcoming = start_date >= now()
--   table : kickoffCutoff() -> slateDayStart() -> 04:00 ET of the slate day
--
-- Those agree only while no game has kicked off. Between a kickoff and the next
-- 04:00 ET rollover they do not, and in the awkward direction: this view DROPS a
-- started game that the table KEEPS -- keeping it is deliberate, see migration
-- 0052 and `lib/core/kickoff.ts`.
--
-- Measured on production, 2026 cfb week 1 at Sat 2026-09-05 20:00 ET:
--
--   market       quotes   pills rule   table rule
--   rec_yards       546           84          343
--   rush_yards      318           54          191
--   receptions      210           38          130
--   pass_yards      178           31          113
--   pass_tds        176           31          111
--
-- The pills render no counts, so the visible symptom is not a wrong number: it
-- is that a market whose quotes all sit on started games DISAPPEARS from the
-- filter while its rows are still listed -- rows a reader can see and cannot
-- select -- and that the pill ORDER is sorted on a different row set than the
-- one being ordered.
--
-- ---------------------------------------------------------------------------
-- WHY start_date AND NOT A CORRECTED BOOLEAN
-- ---------------------------------------------------------------------------
-- The obvious fix is to recompute `is_upcoming` against the 04:00 ET rollover
-- here. That writes the rollover rule into SQL a second time, and the rule
-- already lives in `lib/core/slate-days.ts`, which is deliberately
-- database-free so it can be tested without a Supabase connection. Two copies
-- of a product rule is how the board and this page came to disagree in the
-- first place.
--
-- Exposing the kickoff instead lets the CALLER apply its own cutoff, with the
-- identical predicate the table uses -- `upcomingOnly(cutoff)` in
-- `lib/data/query.ts`, the same helper, the same string. The rule stays stated
-- once, in TypeScript, and this view only classifies each row.
--
-- GRAIN. Bucketing on the kickoff instead of a boolean multiplies rows by the
-- number of distinct kickoff times in the week, not by the number of games:
-- measured on 2026 cfb week 1, 24 distinct kickoffs against 5 markets carrying
-- two-way quotes, so ~120 rows for the scope the page reads. That is well under
-- PostgREST's silent 1,000-row cap, and the caller checks for the cap anyway --
-- a truncated aggregate would drop a market from its own filter, which is the
-- bug this migration exists to fix.
--
-- COLUMN ORDER IS NOT FREE HERE, SO THIS DROPS AND RECREATES. `create or
-- replace view` may only append columns and cannot change one's type, and this
-- replaces `is_upcoming` (boolean, position 8) with `start_date` (timestamptz).
-- Nothing in the database depends on this view -- it has no dependents, only the
-- two readers in `lib/data/no-vig.ts` and `scripts/check-schema.mjs` -- so a
-- drop is safe. `v_no_vig_rows` is untouched.
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
  -- The game's kickoff, for the caller to cut against its own slate-day
  -- cutoff. NULL is a TBD kickoff and stays in: a null cannot be proven to be
  -- in the past, and `upcomingOnly` keeps it for exactly that reason.
  r.start_date,
  count(*)                                        as quotes,
  count(*) filter (where r.books_at_line > 1)     as shoppable
from v_no_vig_rows r
group by 1, 2, 3, 4, 5, 6, 7, 8;

comment on view v_no_vig_markets is
  'Per-market quote counts for the no-vig page''s filter, one row per (season, week, sport, market, conference scope, KICKOFF). Exists because PostgREST refuses aggregates for anon and because counting in the page would count a truncated fetch. Buckets on start_date rather than an is_upcoming boolean so the caller applies the SAME cutoff as the table it sits above -- the two used different rules and the pills described a quarter of the slate. ALWAYS filter on season and week, which lead the GROUP BY.';

comment on column v_no_vig_markets.start_date is
  'Kickoff of the game these quotes are on. Cut it with the caller''s own cutoff (`upcomingOnly` in lib/data/query.ts), never with now() -- a started game stays on this page until the slate day rolls over at 04:00 ET, and a boolean computed here could not know that. NULL is a TBD kickoff and counts as upcoming.';
