-- Phase 5f: sort the board on the number the board shows.
--
-- THE DEFECT. `picks.confidence` is the mass past the line on the CALLED side.
-- For anytime TD the called side is `under` on 1,832 of 1,882 picks — most
-- players do not score — so confidence there is the probability a player FAILS
-- to score, and it runs high precisely where the pick is least interesting.
--
-- The card never shows that number. `market-row.tsx` renders `model_prob_over`
-- for a binary market, because CLAUDE.md §1 says touchdowns are "expressed as
-- an anytime-scorer probability, never a projected count". So the board sorted
-- and graded on one number while displaying another, and they run in OPPOSITE
-- directions.
--
-- Invisible while every row carried a synthetic development line, because edge
-- decided the order first. On a real slate no book has priced yet, anytime TD
-- is the only market with a call at all (`markets.default_line` is 0.5), so
-- confidence IS the sort — and the board opened with the players most certain
-- not to score, each badged A+.
--
-- `display_confidence` is that one choice, made once, in the place both the
-- ordering and the read layer can see it. Appended at the end of the select
-- list so `create or replace view` is legal.

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
  end                     as display_confidence
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
left join defense_position_ratings dpr
       on dpr.defense_team_id = pr.opponent_team_id
      and dpr.season          = pr.season
      and dpr.as_of_week      = pr.week
      and dpr.position_group  = pts.position_group;

comment on column v_board_rows.display_confidence is
  'The headline probability as the card renders it: model_prob_over for a binary market (the anytime-scorer probability, CLAUDE.md §1), otherwise picks.confidence. Sort and filter on THIS, never on confidence — for anytime TD the called side is under on ~97% of picks, so confidence there is the probability of NOT scoring and orders the board backwards.';
