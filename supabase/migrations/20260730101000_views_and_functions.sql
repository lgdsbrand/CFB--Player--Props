-- =============================================================================
-- 0010 — Read layer: as-of functions and board views
-- =============================================================================
-- Feature reads go through as-of-parameterised functions so a caller must state
-- its knowledge cutoff explicitly. There is deliberately no convenience view
-- that returns "current season defensive splits" with no cutoff — that is the
-- shape of query that produces lookahead bias.
--
-- Views are security_invoker so the querying role's RLS applies rather than the
-- view owner's.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Cumulative position splits strictly before a cutoff week
-- -----------------------------------------------------------------------------
create or replace function defense_position_splits_through(
  p_season   integer,
  p_as_of_week integer
)
returns table (
  defense_team_id       bigint,
  position_group        position_group,
  games_included        integer,
  rush_attempts         bigint,
  rush_yards_allowed    bigint,
  rush_tds_allowed      bigint,
  targets               bigint,
  receptions_allowed    bigint,
  rec_yards_allowed     bigint,
  rec_tds_allowed       bigint,
  rush_yards_allowed_pg numeric,
  rec_yards_allowed_pg  numeric
)
language sql
stable
as $$
  select
    s.defense_team_id,
    s.position_group,
    count(distinct s.game_id)::integer            as games_included,
    sum(s.rush_attempts)::bigint                  as rush_attempts,
    sum(s.rush_yards_allowed)::bigint             as rush_yards_allowed,
    sum(s.rush_tds_allowed)::bigint               as rush_tds_allowed,
    sum(s.targets)::bigint                        as targets,
    sum(s.receptions_allowed)::bigint             as receptions_allowed,
    sum(s.rec_yards_allowed)::bigint              as rec_yards_allowed,
    sum(s.rec_tds_allowed)::bigint                as rec_tds_allowed,
    round(sum(s.rush_yards_allowed)::numeric
          / nullif(count(distinct s.game_id), 0), 2) as rush_yards_allowed_pg,
    round(sum(s.rec_yards_allowed)::numeric
          / nullif(count(distinct s.game_id), 0), 2) as rec_yards_allowed_pg
  from defense_position_game_splits s
  where s.season = p_season
    and s.week < p_as_of_week
  group by s.defense_team_id, s.position_group;
$$;

comment on function defense_position_splits_through(integer, integer) is
  'Raw cumulative position splits using ONLY games with week < p_as_of_week. The strict inequality is the point: calling this for week N returns exactly what was knowable entering week N. Opponent adjustment is applied downstream and stored in defense_position_ratings.';

-- -----------------------------------------------------------------------------
-- Latest posted line per player / market / book
-- -----------------------------------------------------------------------------
create view v_latest_prop_lines
with (security_invoker = true)
as
select distinct on (l.game_id, l.player_id, l.market_key, l.sportsbook_id)
  l.id            as line_id,
  l.game_id,
  l.player_id,
  l.market_key,
  l.sportsbook_id,
  b.key           as sportsbook_key,
  b.display_name  as sportsbook_name,
  l.season,
  l.week,
  l.line,
  l.over_price,
  l.under_price,
  devig_two_way(l.over_price, l.under_price) as book_prob_over,
  l.captured_at,
  l.is_closing
from player_prop_lines l
join sportsbooks b on b.id = l.sportsbook_id
order by l.game_id, l.player_id, l.market_key, l.sportsbook_id, l.captured_at desc;

comment on view v_latest_prop_lines is
  'Most recent line per player, market and book, with the de-vigged fair over probability attached. player_prop_lines itself stays append-only history.';

-- -----------------------------------------------------------------------------
-- Main board
-- -----------------------------------------------------------------------------
create view v_board_rows
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
  c.is_displayed          as conference_is_displayed
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

comment on view v_board_rows is
  'Denormalised main board: one row per PROJECTION, with the pick left-joined on. Driving from projections rather than picks is what implements the late-line behaviour in CLAUDE.md §7 — college books post props on Thursday or Friday, so early in the week a row exists showing the model''s lean (projected_median / p10 / p90) with has_call = false, and the OVER/UNDER call, confidence and edge fill in once a book posts. Filtering to has_call or has_book_line gives the market-attached subset. The lateral picks the highest-priority book so the board is one row per player-market; per-book odds for a row come from v_latest_prop_lines. The opponent rank join is pinned to as_of_week = the projection''s week, so a historical board shows the rank as it stood then rather than as it ended up.';

-- -----------------------------------------------------------------------------
-- Player game log
-- -----------------------------------------------------------------------------
create view v_player_game_log
with (security_invoker = true)
as
select
  s.player_id,
  s.game_id,
  s.season,
  s.week,
  s.position_group,
  s.is_home,
  o.abbreviation      as opponent_abbreviation,
  o.school            as opponent_school,
  g.start_date,
  g.neutral_site,
  s.pass_attempts, s.pass_completions, s.pass_yards, s.pass_tds, s.interceptions,
  s.rush_attempts, s.rush_yards, s.rush_tds,
  s.targets, s.receptions, s.rec_yards, s.rec_tds,
  s.offensive_tds
from player_game_stats s
join games g on g.id = s.game_id
join teams o on o.id = s.opponent_team_id;

comment on view v_player_game_log is
  'Per-game actuals for the player detail view''s game log and last-N hit-rate chart (CLAUDE.md §7). Hit-rate calculation stays in the app/worker because it depends on app_config.hit_rate_basis.';
