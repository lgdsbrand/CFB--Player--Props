-- =============================================================================
-- 0033 -- the board can see who is ranked
-- =============================================================================
-- Backs the client's Top 25 filter. Two ranks, not one: the player's own team
-- and his opponent's. The filter itself only needs the first, but the second
-- costs the same join and is what a game-first view will need next.
--
-- PINNED TO THE PROJECTION'S OWN WEEK, exactly like the defense-rank join three
-- lines below it. `tr.week = pr.week` is the whole anti-lookahead guarantee:
-- migration 0032 establishes that CFBD's week N poll is the poll published
-- ENTERING week N, so this reads the ranking as it stood before the game, not
-- as the season finished. A historical board therefore shows #1 beside a team
-- that was #1 that week and lost.
--
-- AP TOP 25 IS THE POLL, hardcoded here rather than configured. It is the one
-- the audience recognises, and the table stores the Coaches and CFP polls too,
-- so changing which one the board shows is a view migration rather than a
-- re-ingest. A scalar subquery reading app_config on every row would have cost
-- more than the flexibility is worth today.
--
-- NULL MEANS UNRANKED, and that is a fact rather than missing data: only 25
-- teams carry a rank in any week, so the vast majority of rows are NULL by
-- construction. Nothing may render that as a zero or a 26.
--
-- Appended after the game-line columns so `create or replace view` stays legal.
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
left join team_poll_rankings tr
       on tr.team_id = pr.team_id
      and tr.season  = pr.season
      and tr.week    = pr.week
      and tr.poll    = 'AP Top 25'
left join team_poll_rankings orank
       on orank.team_id = pr.opponent_team_id
      and orank.season  = pr.season
      and orank.week    = pr.week
      and orank.poll    = 'AP Top 25'
left join defense_position_ratings dpr
       on dpr.defense_team_id = pr.opponent_team_id
      and dpr.season          = pr.season
      and dpr.as_of_week      = pr.week
      and dpr.position_group  = pts.position_group;

comment on column v_board_rows.team_poll_rank is
  'AP Top 25 rank of THIS PLAYER''S team entering this week, or NULL when unranked -- which is most rows, since only 25 teams are ranked. Point-in-time: the poll published before this week was played, never the season''s final one.';

comment on column v_board_rows.opponent_poll_rank is
  'AP Top 25 rank of the opponent entering this week, same basis as team_poll_rank. Not used by the Top 25 filter, which scopes to the player''s own team the way the conference filter does; it is here for game-first views.';
