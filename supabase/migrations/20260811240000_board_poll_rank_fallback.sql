-- =============================================================================
-- 0034 -- the Top 25 filter must not depend on ONE poll existing
-- =============================================================================
-- CAUGHT BEFORE DEPLOYMENT, against production data. Migration 0033 pinned the
-- board's rank to 'AP Top 25'. On 2026 that poll does not exist yet: on 11
-- August the only published poll is the preseason Coaches Poll, so the live
-- board carried 4,494 rows and 0 ranks, and the brand-new Top 25 toggle would
-- have returned an empty board on every click.
--
-- That is the same defect as commit a3206e9, "Stop offering controls that lead
-- to an empty board", and it is worth naming twice: a control that is present,
-- enabled and always empty is worse than one that is absent, because the reader
-- concludes the DATA is missing rather than the filter.
--
-- The AP preseason poll lands in mid-August and this would have started working
-- on its own within a fortnight, which is precisely what makes it dangerous --
-- it would have looked broken exactly during the client's review window and
-- fixed itself before anyone finished diagnosing it.
--
-- THE RULE NOW: best available poll, in a fixed preference order, per team-week.
-- AP where it exists, Coaches otherwise, the CFP committee last. All three are
-- legitimate answers to "is this team ranked"; none of them is available in
-- every week of every season.
--
-- A LATERAL RATHER THAN COALESCE OF THREE JOINS. Three left joins would multiply
-- rows whenever two polls both ranked a team -- which is most weeks, since AP
-- and Coaches publish together -- and the bug would present as a duplicated
-- board rather than as a wrong number.
-- =============================================================================

-- IMMUTABLE so the planner may use it inside the ORDER BY of a lateral without
-- re-evaluating it per row, and so it can be indexed later if this ever gets hot.
create or replace function poll_priority(poll text)
returns integer
language sql
immutable
parallel safe
as $$
  select case poll
           when 'AP Top 25'                  then 1
           when 'Coaches Poll'               then 2
           when 'Playoff Committee Rankings' then 3
           else 4
         end
$$;

comment on function poll_priority(text) is
  'Preference order for which poll the board reports when several rank a team in the same week. AP is the one the audience recognises; Coaches covers the preseason gap before AP publishes; the CFP committee is last because it only exists from November and ranks a different question. Unknown polls sort last rather than being excluded, so a poll CFBD adds later degrades to a fallback instead of vanishing.';

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

  -- From the PLAYER'S team, not the home team. See the migration header.
  case
    when gl.spread is null then null
    when g.home_team_id = pr.team_id then gl.spread
    else -gl.spread
  end                     as team_spread,
  gl.over_under           as game_total,
  gl.providers            as game_line_providers,

  tr.rank                 as team_poll_rank,
  orank.rank              as opponent_poll_rank
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

comment on column v_board_rows.team_poll_rank is
  'Rank of THIS PLAYER''S team entering this week in the best available poll (see poll_priority), or NULL when unranked -- which is most rows, since only 25 teams are ranked. Point-in-time: the poll published before this week was played, never the season''s final one.';

comment on column v_board_rows.opponent_poll_rank is
  'Same basis, for the opponent. Not used by the Top 25 filter, which scopes to the player''s own team the way the conference filter does; it is here for game-first views.';
