-- =============================================================================
-- 0018 — v_slate_weeks: the weeks the board can actually show
-- =============================================================================
-- The week selector needs to know which weeks have model output, and how many
-- games sit behind each. That is a GROUP BY, and PostgREST refuses aggregate
-- functions for the anon role — deliberately, since an un-bounded aggregate is
-- an easy way to make a public endpoint expensive.
--
-- The alternative to this view is the app reading tens of thousands of rows to
-- count them client-side, which is worse in every respect.
--
-- DRIVEN FROM `projections`, NOT `games`. A week with a schedule but no
-- projections is a dead click: the board would open on it and render nothing.
-- Weeks 1 and 2 of any season are exactly that case and always will be —
-- features may only read weeks strictly before the target and a player needs
-- two games before anything is projected for him, so the first projectable week
-- of a season is week 3.
-- =============================================================================

create view v_slate_weeks
with (security_invoker = true)
as
select
  p.season,
  p.week,
  count(distinct p.game_id)::integer as games,
  count(*)::integer                  as projections,
  count(distinct p.player_id)::integer as players,
  min(g.start_date)                  as first_kickoff,
  max(g.start_date)                  as last_kickoff
from projections p
join games g on g.id = p.game_id
group by p.season, p.week;

comment on view v_slate_weeks is
  'Weeks with model output, for the week selector above the board. Driven from projections rather than games so the selector cannot offer a week that renders empty. The kickoff range is what lets the strip label a week by its dates — college football is a weekly sport, so the date selector in CLAUDE.md §7 (built for the client''s daily MLB board) selects a week here and shows the span it covers.';
