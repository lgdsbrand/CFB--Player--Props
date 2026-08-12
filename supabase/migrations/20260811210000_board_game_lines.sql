-- =============================================================================
-- 0031 -- the board can see the game's spread and total
-- =============================================================================
-- Client request: show the game line on the player card as context for the
-- prop. A 62-point total says more about a rushing prop than most of what the
-- card currently carries.
--
-- COSTS NO ODDS API CREDITS. `game_lines` comes from CFBD (migration 0030), not
-- from the metered provider that serves player props. Worth repeating here
-- because the two are one word apart in conversation and a long way apart in
-- the budget.
--
-- -----------------------------------------------------------------------------
-- THE SPREAD IS FLIPPED TO THE PLAYER'S OWN TEAM
-- -----------------------------------------------------------------------------
-- `game_lines.spread` is from the HOME team's perspective (verified: 228 of 228
-- rows agreed with CFBD's own formattedSpread across the 2025 week-8 slate). A
-- card is about a PLAYER, and half of them are on the road, so rendering the
-- stored number directly would tell an away-team reader his 10-point underdog
-- is a 10-point favourite. Every number would still look plausible.
--
-- The flip is done HERE rather than in the component so there is one definition
-- of "this player's spread" shared by the card, the player page and anything
-- built on top of them later.
--
-- NULL STAYS NULL. `-gl.spread` on a NULL is NULL anyway, but the CASE says so
-- explicitly: a game nobody priced must read as absent, and 0 would render as a
-- pick-em, which is a real and different thing.
--
-- `game_line_providers` travels with the numbers so a consensus of one book can
-- be told apart from a consensus of three without another query.
--
-- Appended after the venue columns so `create or replace view` stays legal:
-- Postgres permits new columns only at the end of an existing view.
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
  gl.providers            as game_line_providers
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
left join defense_position_ratings dpr
       on dpr.defense_team_id = pr.opponent_team_id
      and dpr.season          = pr.season
      and dpr.as_of_week      = pr.week
      and dpr.position_group  = pts.position_group;

comment on column v_board_rows.team_spread is
  'The game spread FROM THIS PLAYER''S TEAM''S perspective: negative means his team is favoured. game_lines stores it home-side, and roughly half of all cards are for away players, so the flip happens here rather than in a component -- one definition, shared by the card and the player page. NULL means no provider priced the game; it is never 0, which would read as a pick-em.';

comment on column v_board_rows.game_total is
  'Median over/under across the providers that priced this game. Display context only -- nothing in the model reads it, and it must stay that way: a game total is the market''s view of the same game the model is projecting.';

comment on column v_board_rows.game_line_providers is
  'How many providers backed team_spread and game_total. Lets a one-book consensus be distinguished from a three-book one without a second query.';
