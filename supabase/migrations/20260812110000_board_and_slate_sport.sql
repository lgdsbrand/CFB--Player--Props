-- =============================================================================
-- 0036 -- the board and the week strip learn about sport
-- =============================================================================
-- Migration 0035 added the dimension. This is the pair of views the app reads
-- that would otherwise MIX the two sports the moment NFL rows exist, and both
-- would do it silently.
--
-- `v_board_rows` is the obvious one: without a sport to filter on, the college
-- board would list NFL players.
--
-- `v_slate_weeks` is the one worth naming, because the failure is not a wrong
-- row but a wrong COUNT. It aggregates by (season, week), so an NFL week 3 and a
-- college week 3 would collapse into one strip entry whose game and player
-- counts are the sum of both sports. Nothing would look broken -- the numbers
-- would just be wrong, and the week the board opened on would be chosen from a
-- mixed slate. Grouping by sport as well is the whole fix.
--
-- SPORT COMES FROM `games`, not from the team or the player, on both views. All
-- three agree by construction and always will -- there is no cross-sport game --
-- but they have to be read from ONE of them or the two views could eventually
-- disagree about what a row belongs to. The game is the right choice because it
-- is the object the week axis already hangs off.
--
-- `create or replace view` may only APPEND columns, which is what both of these
-- do, so the deployed app keeps working in the window between this migration
-- reaching production and the code that filters on it being pushed. That is the
-- ordering the deploy runbook depends on.
-- =============================================================================

create or replace view v_board_rows
with (security_invoker = true)
as
select
  pr.id                   as projection_id,
  p.id                    as pick_id,
  pr.season,
  pr.week,
  pr.market_key,
  m.display_name          as market_name,
  m.short_label           as market_label,
  m.emoji                 as market_emoji,
  m.is_binary,

  pl.id                   as player_id,
  pl.name                 as player_name,
  pts.position_group,

  t.id                    as team_id,
  t.school                as team_school,
  t.abbreviation          as team_abbreviation,
  t.color                 as team_color,
  t.alt_color             as team_alt_color,

  o.id                    as opponent_team_id,
  o.school                as opponent_school,
  o.abbreviation          as opponent_abbreviation,

  g.id                    as game_id,
  g.start_date,
  g.neutral_site,
  (g.home_team_id = t.id) as is_home,

  p.line,
  p.side,
  p.confidence,
  p.model_prob_over,
  p.book_prob_over,
  p.edge,
  coalesce(p.has_book_line, false) as has_book_line,
  (p.id is not null)      as has_call,
  p.over_price,
  p.under_price,
  p.sportsbook_key,
  p.sportsbook_name,

  -- secondary detail only: never the headline claim (CLAUDE.md §1)
  pr.p50                  as projected_median,
  pr.p10                  as projected_p10,
  pr.p90                  as projected_p90,
  pr.prior_weight,

  dpr.rank_vs_position    as opponent_rank_vs_position,
  c.name                  as conference_name,
  c.is_displayed          as conference_is_displayed,

  -- The headline probability as the CARD states it, on one scale, so ordering
  -- and filtering cannot disagree with the number under the reader's eye.
  case
    when p.id is null then null
    when m.is_binary  then p.model_prob_over
    else p.confidence
  end                     as display_confidence,

  pr.effective_sample,

  v.name                  as venue_name,
  v.city                  as venue_city,
  v.state                 as venue_state,

  -- From the PLAYER'S team, not the home team. See migration 0031.
  case
    when gl.spread is null then null
    when g.home_team_id = pr.team_id then gl.spread
    else -gl.spread
  end                     as team_spread,
  gl.over_under           as game_total,
  gl.providers            as game_line_providers,

  tr.rank                 as team_poll_rank,
  orank.rank              as opponent_poll_rank,

  g.sport
from projections pr
join markets m             on m.key = pr.market_key
join players pl            on pl.id = pr.player_id
join teams t               on t.id = pr.team_id
join teams o               on o.id = pr.opponent_team_id
join games g               on g.id = pr.game_id
left join lateral (
  select
    pk.id,
    pk.line,
    pk.side,
    pk.confidence,
    pk.model_prob_over,
    pk.book_prob_over,
    pk.edge,
    pk.has_book_line,
    pk.over_price,
    pk.under_price,
    bk.key          as sportsbook_key,
    bk.display_name as sportsbook_name
  from picks pk
  left join sportsbooks bk on bk.id = pk.sportsbook_id
  where pk.projection_id = pr.id
  order by coalesce(bk.priority, 32767), pk.id
  limit 1
) p on true
left join player_team_seasons pts
       on pts.player_id = pr.player_id
      and pts.team_id   = pr.team_id
      and pts.season    = pr.season
left join team_seasons ts  on ts.team_id = pr.team_id and ts.season = pr.season
left join conferences c    on c.id = ts.conference_id
left join venues v         on v.id = g.venue_id
left join v_game_line_consensus gl on gl.game_id = g.id
left join lateral (
  select r.rank
  from team_poll_rankings r
  where r.team_id = pr.team_id and r.season = pr.season and r.week = pr.week
  order by poll_priority(r.poll)
  limit 1
) tr on true
left join lateral (
  select r.rank
  from team_poll_rankings r
  where r.team_id = pr.opponent_team_id and r.season = pr.season and r.week = pr.week
  order by poll_priority(r.poll)
  limit 1
) orank on true
left join defense_position_ratings dpr
       on dpr.defense_team_id = pr.opponent_team_id
      and dpr.season          = pr.season
      and dpr.as_of_week      = pr.week
      and dpr.position_group  = pts.position_group;

comment on column v_board_rows.sport is
  'The one place the sport dimension is denormalised onto a row (migration 0035). It is here because the board already pays for the join to games, and because the alternative -- filtering the board through a second lookup -- would put the sport filter on a different code path from the conference and Top 25 filters that sit beside it in the UI.';

-- -----------------------------------------------------------------------------
-- v_slate_weeks
-- -----------------------------------------------------------------------------
create or replace view v_slate_weeks
with (security_invoker = true)
as
select
  p.season,
  p.week,
  count(distinct p.game_id)::integer   as games,
  count(*)::integer                    as projections,
  count(distinct p.player_id)::integer as players,
  min(g.start_date)                    as first_kickoff,
  max(g.start_date)                    as last_kickoff,
  g.sport
from projections p
join games g on g.id = p.game_id
group by p.season, p.week, g.sport;

comment on view v_slate_weeks is
  'Weeks with model output, for the week selector above the board. Driven from projections rather than games so the selector cannot offer a week that renders empty -- which is why it needed no change when Phase 6c began publishing weeks 1 and 2, superseding migration 0018''s note that week 3 is the first projectable week. Grouped by sport as of migration 0036: two sports both playing a week 3 are two entries, not one entry whose counts are their sum. The kickoff range is what lets the strip label a week by its dates -- college football is a weekly sport, so the date selector in CLAUDE.md §7 (built for the client''s daily MLB board) selects a week here and shows the span it covers.';
