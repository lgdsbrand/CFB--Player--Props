-- =============================================================================
-- 0037 -- v_slate_games: one row per game, for the Analyze Games index
-- =============================================================================
-- The client asked for a game-first view of what the model already produces:
-- every game on the slate with its spread and total, the props grouped
-- underneath, and the position matchups that stand out. This is the index's
-- read. The DETAIL page needs no new object -- `v_board_rows` already carries
-- the spread, the total, both poll ranks and the venue on every row.
--
-- IT IS NOT A PREDICTION MODEL. Full game-outcome prediction is out of scope
-- (CLAUDE.md §10) and stays there. Everything here is either a fact about the
-- schedule, a number a book published, or a count of model output that already
-- exists. Nothing in this view estimates who wins.
--
-- ---------------------------------------------------------------------------
-- DRIVEN FROM `games`, WHICH IS THE OPPOSITE OF v_slate_weeks
-- ---------------------------------------------------------------------------
-- `v_slate_weeks` is driven from `projections` so the week selector can never
-- offer a week that renders empty. The same reasoning gives the opposite answer
-- here, and the difference is worth stating because the two views sit beside
-- each other and look like they should match.
--
-- A week with no projections is not a week the product has anything to say
-- about. A GAME with no projections is: it still has a kickoff, a venue, a
-- spread and a total, and "no player in this game clears the usage threshold"
-- is a real answer rather than a missing one. So the view carries every game and
-- reports the counts, and the PAGE decides what to show.
--
-- That decision is not being dodged -- it is being put where the conference
-- filter already lives. Commits a3206e9 and 270c933 both fixed controls that led
-- to an empty board, and the rule they established is that a link must lead
-- somewhere. The index applies it by dropping games whose offenses the board
-- does not cover, which is a conference question, and conference membership is
-- season-scoped in `team_seasons` rather than being a property of a game.
--
-- ---------------------------------------------------------------------------
-- THE COUNTS
-- ---------------------------------------------------------------------------
-- `calls` counts PROJECTIONS THAT HAVE A PICK, not picks. A projection can carry
-- several picks -- one per sportsbook -- so counting `picks` rows would report a
-- number several times larger than the card count a reader then sees, and it
-- would grow the day a second book is ingested without anything changing.
-- `v_board_rows` resolves the same ambiguity with a `limit 1` lateral.
--
-- The counts are NOT scoped to the displayed conferences. They describe the
-- game; the filtering is the page's job, and a count that silently meant
-- something different from its label would be worse than no count.
-- =============================================================================

create view v_slate_games
with (security_invoker = true)
as
select
  g.id                    as game_id,
  g.sport,
  g.season,
  g.week,
  g.season_type,
  g.start_date,
  g.start_time_tbd,
  g.neutral_site,
  g.completed,
  g.home_points,
  g.away_points,

  g.home_team_id,
  ht.school               as home_school,
  ht.abbreviation         as home_abbreviation,
  ht.color                as home_color,
  ht.alt_color            as home_alt_color,

  g.away_team_id,
  at.school               as away_school,
  at.abbreviation         as away_abbreviation,
  at.color                as away_color,
  at.alt_color            as away_alt_color,

  v.name                  as venue_name,
  v.city                  as venue_city,
  v.state                 as venue_state,

  -- HOME PERSPECTIVE, matching CFBD's own convention: a negative spread means
  -- the home team is favoured. `v_board_rows.team_spread` flips it to the
  -- player's team because a card is about one team; a game card shows both, so
  -- flipping here would only raise the question of which side it referred to.
  gl.spread               as home_spread,
  gl.over_under           as game_total,
  gl.providers            as game_line_providers,

  hr.rank                 as home_poll_rank,
  ar.rank                 as away_poll_rank,

  coalesce(b.projections, 0) as projections,
  coalesce(b.players, 0)     as players,
  coalesce(b.calls, 0)       as calls
from games g
join teams ht on ht.id = g.home_team_id
join teams at on at.id = g.away_team_id
left join venues v on v.id = g.venue_id
left join v_game_line_consensus gl on gl.game_id = g.id
left join lateral (
  select r.rank
  from team_poll_rankings r
  where r.team_id = g.home_team_id and r.season = g.season and r.week = g.week
  order by poll_priority(r.poll)
  limit 1
) hr on true
left join lateral (
  select r.rank
  from team_poll_rankings r
  where r.team_id = g.away_team_id and r.season = g.season and r.week = g.week
  order by poll_priority(r.poll)
  limit 1
) ar on true
left join lateral (
  select
    count(*)::integer                     as projections,
    count(distinct pr.player_id)::integer as players,
    count(*) filter (
      where exists (select 1 from picks pk where pk.projection_id = pr.id)
    )::integer                            as calls
  from projections pr
  where pr.game_id = g.id
) b on true;

comment on view v_slate_games is
  'One row per game for the Analyze Games index: schedule facts, the venue, the consensus spread and total, both poll ranks entering the week, and how much model output sits behind the game. Driven from games rather than from projections -- unlike v_slate_weeks -- because a game with no props is still a game with a kickoff and a line, and the decision about which games to LIST is a conference question the page answers. Carries no prediction of the outcome; CLAUDE.md §10 puts that out of scope.';

comment on column v_slate_games.home_spread is
  'Median across providers, HOME perspective: negative means the home team is favoured. Deliberately not flipped to either side, unlike v_board_rows.team_spread, because a game card shows both teams and a flipped number would be ambiguous.';

comment on column v_slate_games.calls is
  'Projections that have at least one pick, NOT the number of picks. A projection carries one pick per sportsbook, so counting picks would report a number that grows when a second book is ingested and that no longer matches what a reader can count on screen.';

comment on column v_slate_games.projections is
  'Every projection on the game, across both teams and all conferences. Not narrowed to the displayed conferences: the count describes the game, and the page narrows the LIST.';
