-- Rule plays: the client's line-gap rules as a third pick engine (2026-10-09).
--
-- The client asked for daily and weekly recommended plays on his own rules:
-- a spread 4+ points off the book's, a total 6+ points off, a moneyline the
-- model gives 60%+ at -150 or better. They are made by the game model run
-- (worker/core/game_picks.py `evaluate_rules`), frozen at kickoff by the same
-- trigger as every other pick (0074), and graded on their own tracker card.
--
--   rules  spreads and totals by POINTS between the fair line the games table
--          shows and the book's line; totals 7+ is the pre-registered top
--          tier. Moneylines fire on the model's own 60%+ on a favourite
--          priced -150 to -101, and record a win % blended toward the book's
--          price (user decision 2026-10-09, CLAUDE.md §11).
--
-- UNLIKE v1 AND v2 THEY ARE PUBLIC BEFORE KICKOFF. 0078 and 0090 kept picks
-- private until their game started so the tracker could not be read as a tip
-- sheet. Rule plays are a tip sheet by request: the site lists them ahead of
-- the games. v1 and v2 stay private until kickoff, unchanged.

alter table game_picks
  drop constraint game_picks_engine_known;
alter table game_picks
  add constraint game_picks_engine_known check (engine in ('v1', 'v2', 'rules'));

alter table game_picks
  add column model_line numeric(5, 1),
  add column gap        numeric(5, 1),
  add column raw_prob   numeric(6, 5);

comment on column game_picks.engine is
  'Which pick rule made this row. v1: raw model probability, edge >= 5% (the shadow test since 2026-09-24). v2: the calibrated probability the games table shows (CALIBRATION_SLOPE), edge >= 2%, from 2026-10-08. rules: the client''s line-gap rules (spread 4+ points, total 6+, moneyline: model 60%+ on a favourite at -150 to -101, recorded at a win % blended toward the book), from 2026-10-09. A pick stands until the same engine switches side; each engine is graded on its own newest row made before kickoff.';
comment on column game_picks.edge is
  'Model probability minus the vig-free book probability of the side taken, at the price taken (CLAUDE.md §6). Recorded for v2 (its "edge plays" tier reads it) and rules; NULL on v1 rows, whose tier is model_prob >= 0.64 on totals.';
comment on column game_picks.model_line is
  'Rule plays: our number in the line''s own terms when the pick was made, the home team''s fair spread or the fair total. NULL for moneylines and for v1/v2.';
comment on column game_picks.raw_prob is
  'Rule moneylines: the model''s own win probability for the side, which fired the rule. model_prob holds the blended one (ML_BLEND_WEIGHTS) that is shown and graded. NULL elsewhere.';
comment on column game_picks.gap is
  'Rule plays: points between model_line and line when the pick was made (4+ spreads, 6+ totals; 7+ is the totals top tier). NULL for moneylines and for v1/v2.';

create policy game_picks_rule_plays_public
  on game_picks for select
  to anon, authenticated
  using (engine = 'rules');

-- The tracker view gains the two columns, appended so `create or replace`
-- keeps every existing column in place. Unchanged otherwise (see 0090).
create or replace view v_tracker_picks
with (security_invoker = true)
as
with standing as (
  select distinct on (p.engine, p.game_id, p.period, p.market)
         p.id, p.engine, p.game_id, p.period, p.market, p.side, p.line,
         p.price, p.model_prob, p.edge, p.made_at, p.sportsbook_id,
         p.model_line, p.gap, p.raw_prob
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
       end as units,
       sc.model_line, sc.gap, sc.raw_prob
  from scored sc
  join games g  on g.id = sc.game_id
  join teams th on th.id = g.home_team_id
  join teams ta on ta.id = g.away_team_id
  left join sportsbooks sb on sb.id = sc.sportsbook_id;

-- The plays list on /games: each rule play standing per game and market,
-- whether or not its game has started, so the week's list can carry its
-- results beside what is still to play. Rule rows only; the policy above is
-- what makes them readable before kickoff.
create view v_rule_plays
with (security_invoker = true)
as
with standing as (
  select distinct on (p.game_id, p.period, p.market)
         p.id, p.game_id, p.market, p.side, p.line, p.price, p.model_prob,
         p.edge, p.model_line, p.gap, p.raw_prob, p.made_at, p.sportsbook_id
    from game_picks p
    join games g on g.id = p.game_id
   where p.engine = 'rules'
     and p.period = 'full'
     and p.made_at < g.start_date
   order by p.game_id, p.period, p.market, p.made_at desc, p.id desc
)
select s.id, s.game_id, s.market, s.side, s.line, s.price, s.model_prob,
       s.edge, s.model_line, s.gap, s.raw_prob, s.made_at,
       sb.display_name as sportsbook_name,
       g.sport, g.season, g.week, g.start_date, g.neutral_site,
       g.home_points, g.away_points,
       (g.completed and g.home_points is not null and g.away_points is not null) as final,
       th.school as home_school, th.abbreviation as home_abbreviation,
       th.color as home_color, th.alt_color as home_alt_color,
       ta.school as away_school, ta.abbreviation as away_abbreviation,
       ta.color as away_color, ta.alt_color as away_alt_color
  from standing s
  join games g  on g.id = s.game_id
  join teams th on th.id = g.home_team_id
  join teams ta on ta.id = g.away_team_id
  left join sportsbooks sb on sb.id = s.sportsbook_id;

comment on view v_rule_plays is
  'Rule plays (engine rules, migration 0091): the play standing per game and market, made before kickoff, with the game and both teams. Public before kickoff by design (game_picks_rule_plays_public); grading is in v_tracker_picks.';

grant select on v_rule_plays to anon, authenticated;
