-- The props record on the site (2026-10-16): names on the graded picks, and a
-- summary the page reads in one call.
--
-- 1. v_prop_pick_results gains the player's name, his team and opponent codes
--    and the market's label, appended (`create or replace view` may only add
--    columns at the end). Unchanged otherwise; see 0096.
--
-- 2. v_prop_record: W-L-P, voids, pending and units per sport, week, market and
--    EDGE BAND, over picks whose game has kicked off. Bands, not tiers, so a
--    pick is counted once; the page sums bands into the cumulative tiers it
--    shows (all calls, 2%+, 5%+, 10%+). Summarised in SQL because a season of
--    locked picks is many thousands of rows, past PostgREST's 1,000-row cap.
--
--      band  10  edge >= 10%      2  2% <= edge < 5%
--             5  5% <= edge < 10% 0  0 <= edge < 2%
--            -1  edge below 0 (the call's side priced richer by the book)
--          null  no edge (a one-sided price): in "all calls" only

create or replace view v_prop_pick_results
with (security_invoker = true)
as
with scored as (
  select l.*,
         g.sport, g.start_date,
         (g.completed and exists (
            select 1 from player_game_stats s where s.game_id = l.game_id
         )) as final,
         case m.stat_column
           when 'pass_yards'       then gl.pass_yards
           when 'pass_tds'         then gl.pass_tds
           when 'pass_attempts'    then gl.pass_attempts
           when 'pass_completions' then gl.pass_completions
           when 'interceptions'    then gl.interceptions
           when 'rush_yards'       then gl.rush_yards
           when 'rush_attempts'    then gl.rush_attempts
           when 'rush_tds'         then gl.rush_tds
           when 'targets'          then gl.targets
           when 'receptions'       then gl.receptions
           when 'rec_yards'        then gl.rec_yards
           when 'rec_tds'          then gl.rec_tds
           when 'offensive_tds'    then gl.offensive_tds
         end::numeric as actual,
         case when l.side = 'over' then l.over_price else l.under_price end as price
    from prop_pick_locks l
    join games g   on g.id = l.game_id
    join markets m on m.key = l.market_key
    left join v_player_game_log gl
      on gl.player_id = l.player_id and gl.game_id = l.game_id
),
graded as (
  select s.*,
         case
           when not s.final then 'pending'
           when s.actual is null then 'void'
           when s.actual = s.line then 'push'
           when (s.actual > s.line) = (s.side = 'over') then 'win'
           else 'loss'
         end as result
    from scored s
)
select gr.id, gr.game_id, gr.player_id, gr.team_id, gr.opponent_team_id,
       gr.market_key, gr.season, gr.week, gr.sport, gr.start_date,
       gr.sportsbook_id, sb.key as sportsbook_key, sb.display_name as sportsbook_name,
       gr.line, gr.side, gr.price, gr.over_price, gr.under_price,
       gr.model_prob_over,
       case when gr.side = 'over' then gr.model_prob_over
            else 1 - gr.model_prob_over end as model_prob,
       gr.book_prob_over, gr.edge, gr.projected_median, gr.made_at,
       gr.actual, gr.result,
       case gr.result
         when 'win' then
           case when gr.price is null then null
                when gr.price > 0 then round(gr.price / 100.0, 4)
                else round(100.0 / (-gr.price), 4) end
         when 'loss' then -1
         when 'push' then 0
         when 'void' then 0
       end as units,
       pl.name as player_name,
       t.abbreviation as team_abbreviation,
       o.abbreviation as opponent_abbreviation,
       mk.short_label as market_label
  from graded gr
  left join sportsbooks sb on sb.id = gr.sportsbook_id
  join players pl on pl.id = gr.player_id
  join teams t    on t.id = gr.team_id
  join teams o    on o.id = gr.opponent_team_id
  join markets mk on mk.key = gr.market_key;

create view v_prop_record
with (security_invoker = true)
as
select r.sport, r.season, r.week, r.market_key, r.market_label,
       case
         when r.edge is null  then null
         when r.edge >= 0.10  then 10
         when r.edge >= 0.05  then 5
         when r.edge >= 0.02  then 2
         when r.edge >= 0     then 0
         else -1
       end as band,
       count(*) filter (where r.result = 'win')     as wins,
       count(*) filter (where r.result = 'loss')    as losses,
       count(*) filter (where r.result = 'push')    as pushes,
       count(*) filter (where r.result = 'void')    as voids,
       count(*) filter (where r.result = 'pending') as pending,
       coalesce(sum(r.units) filter (where r.result in ('win', 'loss')), 0) as units,
       -- A win at a side with no price has no units; counted so the page can
       -- say so rather than quietly understate the units.
       count(*) filter (where r.result = 'win' and r.price is null) as unpriced_wins
  from v_prop_pick_results r
 where r.start_date <= now()
 group by 1, 2, 3, 4, 5, 6;

comment on view v_prop_record is
  'The props record: W-L-P, voids, pending and units per sport, week, market and edge band (10, 5, 2, 0, -1 for 10%+, 5-10%, 2-5%, 0-2%, negative; null = no edge), over locked picks whose game has kicked off. The page sums bands into its cumulative tiers.';
