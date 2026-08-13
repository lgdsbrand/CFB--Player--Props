-- =============================================================================
-- 0040 -- the ladder reaches the read layer
-- =============================================================================
-- Migration 0039 stores `projections.ladder`. Nothing reads it yet, because
-- `v_board_rows` is the only projection surface the app selects from.
--
-- `create or replace view` may only APPEND a column, which is what this does, so
-- production keeps serving the board in the window between this migration landing
-- and the code that selects the column being pushed. That is the ordering the
-- deploy runbook depends on, and the reason a view change is safe to ship first.
--
-- THE COLUMN IS ADDED TO THE VIEW BUT NOT TO THE BOARD'S SELECT LIST. The read
-- layer keeps one column list for the 1,000-row board page and a second, wider
-- one for a single player, because a ladder is 5 to 7 objects of jsonb per row
-- and the board renders none of them. Adding it to the shared list would put
-- ~7,000 rung objects on the wire for every page of a board that shows a card
-- per row and a ladder on none.
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

  g.sport,

  pr.ladder
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

comment on column v_board_rows.ladder is
  'Alternate-line rungs from projections.ladder (migration 0039), ascending by line. Selected only by the PLAYER-DETAIL query, never by the board page: it is 5-7 jsonb objects per row and the board renders none of them. NULL for anytime_td, which is a single probability by construction. These are model probabilities with no book comparison attached — a rung is not a priced market, and an edge quoted against a line nobody posted would be inventing one.';
