-- =============================================================================
-- 0029 — the board can see where the game is being played
-- =============================================================================
-- Client request: show the stadium and its location under the team names, on
-- the card and on the player page.
--
-- NO NEW DATA IS INGESTED FOR THIS. `venues` has existed since migration 0002
-- and is populated for every game on the slate (99 of 99 on the 2026 opening
-- weekend). The board's view simply never selected it, so the one fact that
-- says WHERE a game is happening stopped at the database — the same shape as
-- `effective_sample` in 0028.
--
-- IT MATTERS MOST WHERE THE TEAM NAMES SAY LEAST. College football plays a
-- large number of neutral-site games, and CLAUDE.md §7 already treats home/away
-- as a secondary control for exactly that reason. On those rows "GT vs COLO"
-- names neither the host nor the city, and the venue is the only column that
-- can. `games.neutral_site` tells a reader that the venue is not a home field;
-- only this tells them what it is instead.
--
-- LEFT JOIN, NOT JOIN. `games.venue_id` is nullable — CFBD does not always
-- carry a venue, particularly for early-posted future games — and an inner join
-- here would silently drop those projections from the board entirely. A card
-- with no stadium line is a cosmetic gap; a player who vanishes from the slate
-- because his stadium is unknown is a defect, and it would present as a row
-- count that quietly disagrees with `getBoardCounts`.
--
-- STATE IS SEPARATE FROM CITY rather than pre-joined into one string. The
-- display format is a UI decision that differs by surface (the card is tight,
-- the player page has room) and international venues carry no state at all, so
-- concatenating here would push a formatting choice into the schema and make
-- "Dublin, " a possible output.
--
-- Appended after `effective_sample` so `create or replace view` stays legal:
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
  v.state                 as venue_state
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
left join defense_position_ratings dpr
       on dpr.defense_team_id = pr.opponent_team_id
      and dpr.season          = pr.season
      and dpr.as_of_week      = pr.week
      and dpr.position_group  = pts.position_group;

comment on column v_board_rows.venue_name is
  'Stadium hosting this game, or NULL where CFBD carries no venue for it. Display only: nothing in the model reads it. The weather features join game_weather directly, and venues.latitude/is_dome are read by the ingest rather than through this view.';

comment on column v_board_rows.venue_city is
  'City hosting this game. Kept separate from venue_state because the display format differs by surface and international venues carry no state — joining them here would make ''Dublin, '' a possible output.';

comment on column v_board_rows.venue_state is
  'State or province, NULL for international venues. See venue_city.';
