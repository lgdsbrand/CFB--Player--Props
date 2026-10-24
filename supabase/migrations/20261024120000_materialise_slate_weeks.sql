-- =============================================================================
-- 0102 -- materialise the slate weeks: 170,000 rows aggregated to 30, per visit
-- =============================================================================
-- `v_slate_weeks` feeds the week strip above the board. Measured on production
-- 2026-10-24: every call scans all ~170,000 projections and sorts them twice for
-- the COUNT(DISTINCT)s, 761 ms on average through PostgREST, spilling ~44 MB to
-- temp files each time, to return about 30 rows. It was the second most
-- expensive read on the site by total time, and those rows change only when
-- projections do.
--
-- Same shape as 0062 (`mv_no_vig_rows`): a materialised view, and the old name
-- kept as a thin security_invoker passthrough with the identical column list,
-- so the web read layer and check-schema need no change.
--
-- THE REFRESH HAS ONE OWNER. `run_projections` is the only writer of
-- `projections`, so it refreshes this straight after its write, in the same
-- run, and fails if the refresh fails. Kickoff times come from `games` and can
-- move between projection runs; that is at most a day of staleness on the
-- strip's date labels (run_projections runs daily for both sports), never a
-- missing or phantom week.
--
-- RLS: projections and games are public-read (`qual = true` for anon and
-- authenticated), so the MV exposes nothing anon could not already select.
-- =============================================================================

create materialized view mv_slate_weeks as
select
  p.season,
  p.week,
  count(distinct p.game_id)::integer   as games,
  count(*)::integer                    as projections,
  count(distinct p.player_id)::integer as players,
  min(g.start_date)                    as first_kickoff,
  max(g.start_date)                    as last_kickoff,
  g.sport
from public.projections p
join public.games g on g.id = p.game_id
group by p.season, p.week, g.sport;

-- REFRESH ... CONCURRENTLY needs a unique index; it also serves the reader's
-- sport filter.
create unique index mv_slate_weeks_key on mv_slate_weeks (sport, season, week);

grant select on mv_slate_weeks to anon, authenticated;

create or replace view v_slate_weeks
with (security_invoker = true)
as
select season, week, games, projections, players, first_kickoff, last_kickoff, sport
from mv_slate_weeks;

comment on materialized view mv_slate_weeks is
  'Precomputed v_slate_weeks (migration 0102). Refreshed by run_projections, the only writer of projections, straight after its write. Read v_slate_weeks, not this.';
