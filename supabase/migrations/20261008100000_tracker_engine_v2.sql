-- Engine v2 picks and the public tracker (client 2026-10-08; user-approved).
--
-- The client asked for a public results tracker like his NHL "Engine
-- Snapshot", and for it to count from the model as it is now. The game
-- model's shadow picks (v1) are made on the RAW probability with a 5% edge
-- (worker/core/game_picks.py), which the 2026-10-07 calibration showed to be
-- far too confident. Rather than hide v1's record, v2 is a new, versioned
-- engine, as his own tracker is ("V5"):
--
--   v1  raw probability, edge >= 5%, spreads + totals (+ moneyline until
--       2026-10-03). Graded since week 4. Kept, readable, never rewritten.
--   v2  the CALIBRATED probability the games table shows, edge >= 2%,
--       spreads + totals, no moneyline. Starts with the first model run
--       after this migration: every v2 row is made before its game, so its
--       record is prospective by construction.
--
-- Both live in game_picks, so v2 is frozen at kickoff by the same trigger
-- (game_picks_freeze) that has guarded v1 since 0074.

alter table game_picks
  add column engine text not null default 'v1',
  add column edge   numeric(6, 5);

alter table game_picks
  add constraint game_picks_engine_known check (engine in ('v1', 'v2'));

comment on column game_picks.engine is
  'Which pick rule made this row. v1: raw model probability, edge >= 5% (the shadow test since 2026-09-24). v2: the calibrated probability the games table shows (CALIBRATION_SLOPE), edge >= 2%, from 2026-10-08. A pick stands until the same engine switches side; each engine is graded on its own newest row made before kickoff.';
comment on column game_picks.edge is
  'Model probability minus the vig-free book probability of the side taken, at the price taken (CLAUDE.md §6). Recorded for v2, whose "edge plays" tier (edge >= 3%) is read from it; NULL on v1 rows, whose tier is model_prob >= 0.64 on totals.';

create index game_picks_engine_idx
  on game_picks (engine, game_id, period, market, made_at desc);

-- PUBLIC ONCE THE GAME HAS STARTED. 0078 dropped the public read so nobody
-- could see a pick before its game. A pick on a game that has kicked off is a
-- result, and the tracker exists to publish results; a pick still to play
-- stays private, so the tracker cannot be read as a tip sheet.
create policy game_picks_public_after_kickoff
  on game_picks for select
  to anon, authenticated
  using (
    exists (
      select 1 from games g
       where g.id = game_picks.game_id
         and g.start_date is not null
         and g.start_date <= now()
    )
  );

-- One row per engine, game and market: the pick standing at kickoff (the
-- newest made before it, as grade_game_picks reads it), with the result and
-- units at the price it was frozen at. 'pending' until the final is in.
create view v_tracker_picks
with (security_invoker = true)
as
with standing as (
  select distinct on (p.engine, p.game_id, p.period, p.market)
         p.id, p.engine, p.game_id, p.period, p.market, p.side, p.line,
         p.price, p.model_prob, p.edge, p.made_at, p.sportsbook_id
    from game_picks p
    join games g on g.id = p.game_id
   where p.made_at < g.start_date
     and g.start_date <= now()
   order by p.engine, p.game_id, p.period, p.market, p.made_at desc, p.id desc
),
scored as (
  select s.*,
         g.sport, g.season, g.week, g.start_date,
         g.home_points, g.away_points,
         (g.completed and g.home_points is not null and g.away_points is not null) as final,
         case s.market
           when 'spreads' then
             ((g.home_points - g.away_points) + s.line)
               * case when s.side = 'home' then 1 else -1 end
           when 'totals' then
             ((g.home_points + g.away_points) - s.line)
               * case when s.side = 'over' then 1 else -1 end
           when 'h2h' then
             (g.home_points - g.away_points)
               * case when s.side = 'home' then 1 else -1 end
         end as cover_margin
    from standing s
    join games g on g.id = s.game_id
)
select sc.id, sc.engine, sc.game_id, sc.period, sc.market, sc.side, sc.line,
       sc.price, sc.model_prob, sc.edge, sc.made_at,
       sb.key as sportsbook_key, sb.display_name as sportsbook_name,
       sc.sport, sc.season, sc.week, sc.start_date,
       sc.home_points, sc.away_points,
       th.school as home_school, th.abbreviation as home_abbreviation,
       th.color as home_color,
       ta.school as away_school, ta.abbreviation as away_abbreviation,
       ta.color as away_color,
       case
         when not sc.final then 'pending'
         when sc.cover_margin > 0 then 'win'
         when sc.cover_margin < 0 then 'loss'
         else 'push'
       end as result,
       case
         when not sc.final then null
         when sc.cover_margin > 0 then
           round(case when sc.price > 0 then sc.price / 100.0
                      else 100.0 / (-sc.price) end, 4)
         when sc.cover_margin < 0 then -1
         else 0
       end as units
  from scored sc
  join games g  on g.id = sc.game_id
  join teams th on th.id = g.home_team_id
  join teams ta on ta.id = g.away_team_id
  left join sportsbooks sb on sb.id = sc.sportsbook_id;

comment on view v_tracker_picks is
  'The public tracker: each engine''s pick standing at kickoff, per game and market, for games that have started, with result (win/loss/push/pending) and units won at the frozen price on a 1-unit stake. Reads through game_picks_public_after_kickoff, so a pick still to play never appears.';
