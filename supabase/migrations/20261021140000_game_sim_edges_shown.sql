-- What the site shows of game_sim_edges, and what it grades (phase S4, 2026-10-21).
--
-- The raw period edges (migration 0100) average 3-8% and reach 17%: the same
-- size the full-game table's raw edges were before calibration showed them to
-- be mostly our projection disagreeing with the market on team strength. The
-- user chose to shrink them (2026-10-21) so the site's quarter and half edges
-- are honest in the same sense as its full-game ones.
--
-- THE EDGE IS SHRUNK, NOT THE PROBABILITY TOWARD 50% (user decision 2026-10-21,
-- recorded in CLAUDE.md §11). Shrinking toward 50% flips calls where 50% is the
-- wrong centre: a first-quarter spread of -0.5 loses on a tied quarter, so the
-- book prices home near 48% and our 40% (away) shrank to 49.5% (home). And on a
-- moneyline priced 66% it would invent a 15% underdog edge. Instead:
--
--   shown_edge = k * edge            (our side, never flipped)
--   shown_prob = book_prob + k * (model_prob - book_prob)   (first side)
--
-- with k the full-game table's slopes (CALIBRATION_SLOPE in
-- worker/core/game_picks.py, migration 0085): totals 0.256, spreads 0.0565,
-- and moneylines the spread factor too (a period winner is the same
-- team-strength call). The shown % sits between the book's and ours: a
-- display-only pull toward the book. The raw model_prob stays in game_sim_edges,
-- frozen as made, and never reads a price. Refit k on graded period results
-- each offseason.
--
-- The view replaces 0100's, keeping its columns and adding the shown_* ones.

drop view if exists v_game_sim_edge_results;

create view v_game_sim_edge_results
with (security_invoker = true)
as
with scored as (
  select e.*,
         case when e.period = 'q1' then ls.home_line_scores[1]
              else ls.home_line_scores[1] + ls.home_line_scores[2] end as home_period,
         case when e.period = 'q1' then ls.away_line_scores[1]
              else ls.away_line_scores[1] + ls.away_line_scores[2] end as away_period,
         coalesce(ls.status = 'completed', false) as final,
         case e.market when 'totals' then 0.256 else 0.0565 end as k
    from game_sim_edges e
    left join live_scores ls on ls.game_id = e.game_id
),
outcome as (
  select s.*,
         -- The first side's (home, or over) result against the line: > 0 won.
         case
           when not s.final or s.home_period is null or s.away_period is null then null
           when s.market = 'totals' then (s.home_period + s.away_period) - s.line
           when s.market = 'spreads' then (s.home_period - s.away_period) + s.line
           else (s.home_period - s.away_period)::numeric
         end as x
    from scored s
)
select game_id, period, market, source, line, sportsbook_id,
       first_price, second_price, model_prob, book_prob, side, edge,
       model_mean, made_at, home_period, away_period,
       case when x is null then null
            when x = 0 then 'push'
            when (x > 0) = (side in ('home', 'over')) then 'won'
            else 'lost' end as result,
       round(book_prob + k * (model_prob - book_prob), 5) as shown_prob,
       round(k * edge, 5) as shown_edge,
       k as shrink
  from outcome;

comment on view v_game_sim_edge_results is
  'Each frozen game_sim_edges row with the period''s points from live_scores and the result of its side (won/lost/push, NULL until final). shown_edge = shrink x edge on the same side and shown_prob = book + shrink x (model - book), first side: what the site shows (full-game slopes, totals 0.256, spreads and moneylines 0.0565). Migrations 0100, 0101.';

grant select on v_game_sim_edge_results to anon, authenticated;
