-- =============================================================================
-- 0052 -- has_kicked_off, so a started game can stay on the board
-- =============================================================================
-- The client asked for the day's projections to stay visible until the day's
-- games are over, rather than each one vanishing at its own kickoff.
--
-- Hiding them was deliberate and the reason still holds: a CFBD week is ten
-- calendar days wide, so before this rule existed the board carried 402 rows of
-- already-played games on 2026 week 1 -- including every one of that week's 66
-- edges, sorted to the TOP, because the board orders by edge. A settled prop is
-- not a bet, and it was outranking the ones a reader could still play.
--
-- So the answer is not to stop hiding them; it is to keep them and sort them
-- last. That needs a sortable column, and this is it.
--
-- ---------------------------------------------------------------------------
-- WHY A VIEW COLUMN AND NOT A FILTER IN THE APP
-- ---------------------------------------------------------------------------
-- The board pages in SQL -- `range(offset, offset + limit - 1)` over an ORDER BY
-- chain -- so "started games last" has to be part of that chain. Partitioning
-- the rows after they arrive would only reorder the current page, which is the
-- kind of fix that looks right on a short slate and silently fails on a long
-- one. PostgREST can only order by a column, so the predicate becomes one.
--
-- `now()` is STABLE, so it is evaluated once per statement: every row in one
-- read is classified against the same instant, and two rows either side of a
-- kickoff cannot disagree about what time it is.
--
-- COALESCE, BECAUSE A NULL KICKOFF IS UPCOMING. CFBD publishes early-week
-- schedules with `start_time_tbd` and a null start_date. `null <= now()` is
-- null, not false, so without the coalesce a TBD game would sort as neither and
-- PostgREST's nullsFirst would decide where it landed. Shown-not-hidden is the
-- same call `lib/core/kickoff.ts` makes for the same reason: dropping a row
-- because a field is missing is a silent data loss dressed up as a product rule.
--
-- APPENDED AT THE END OF THE SELECT LIST. `create or replace view` may only add
-- columns after the existing ones, and `v_cheat_sheet` is built on this view --
-- reordering or retyping anything here would fail, or worse, would require
-- dropping a view the cheat sheet depends on.
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

  pr.ladder,

  coalesce(g.start_date <= now(), false) as has_kicked_off
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

comment on column v_board_rows.has_kicked_off is
  'Whether this game had started as of the moment the query ran. Evaluated from now() at read time, so it is not stored and cannot go stale. The board sorts on it FIRST -- ascending, so upcoming rows come before started ones -- which is what lets a started game stay on the board until the slate day rolls over without burying the plays a reader can still make. NULL start_date (a TBD kickoff) counts as not started.';
