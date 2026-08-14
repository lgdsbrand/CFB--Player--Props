-- =============================================================================
-- 0045 -- v_cheat_sheet: props whose recent history has cleared today's line
-- =============================================================================
-- The client asked for "a 80% & 100% hit rate list of all props that have hit
-- for those percentages". This is the read behind it.
--
-- ---------------------------------------------------------------------------
-- WHY THIS GRADES IN SQL WHEN `lib/core/hit-rate.ts` SAYS IT MUST NOT
-- ---------------------------------------------------------------------------
-- That module opens by explaining that a hit rate is a question about a LINE,
-- and the line is only known at render time -- so computing it in SQL would
-- mean materialising a figure per (player, market, line). That reasoning is
-- still correct and this view does not contradict it, because the question is
-- different in two ways.
--
--   1. THE LINE IS IN THE DATABASE HERE. The player page's chart re-grades
--      against a line the reader is moving with a stepper, which is why it
--      cannot be precomputed. This grades against the ONE line stored on the
--      pick -- exactly one per prop, already written by the odds ingest.
--
--   2. IT HAS TO FILTER BEFORE IT RETURNS. The board reads ~25 players' game
--      logs because it renders 25 cards. This asks a question of every priced
--      prop on the slate -- 2,600 on a real week, spread over ~800 players --
--      and keeps the ~120 that clear 80%. Doing that in the app means fetching
--      every one of those logs first: ~20 sequential PostgREST round trips to
--      throw 95% of the result away. Measured on production, this view answers
--      the heaviest week in 780 ms, and the plan attributes most of that to
--      `v_board_rows` rather than to the grading.
--
-- The grading RULES are not re-derived here -- they are transcribed from
-- `gradeGames` and `hitRate`, and any divergence is a bug in this file:
--
--   * a game with no value for the market is dropped BEFORE the window is
--     taken, so a receiver's L5 means his last five games with a receiving
--     line in the box score, not five rows that might include three blanks;
--   * a PUSH is excluded from the denominator rather than counted as a loss.
--     Books post half-points to avoid pushes, so this rarely fires on a real
--     line -- but `anytime_td` sits at 0.5 and `pass_tds` at whole numbers, and
--     `value > line` alone would file every exact tie as an under.
--
-- ---------------------------------------------------------------------------
-- NO LOOKAHEAD (CLAUDE.md §4)
-- ---------------------------------------------------------------------------
-- `l.week < b.week` and the same season. A prop in week 9 is graded on weeks
-- 1-8 only. Grading a week-9 prop on week 9's own box score would produce a
-- "cheat sheet" that is right every time and worth nothing, and it would be
-- invisible on screen because the number would simply look good.
--
-- ---------------------------------------------------------------------------
-- WHY `hit_rate` AND `hit_side` ARE MATERIALISED
-- ---------------------------------------------------------------------------
-- PostgREST cannot compare two columns to each other, so a client cannot ask
-- for `overs / decided >= 0.8`. Filtering after the fetch is not an option: the
-- 1,000-row cap truncates silently, and a truncated slate would drop players
-- off the sheet in a way that looks exactly like them not qualifying. So the
-- rate is a column and the threshold is a `gte` the database applies.
--
-- Over and under are two sides of one number (pushes are already excluded), so
-- one row carries the side that HIT and its rate, which is always >= 0.5. An
-- exact 2-2 split resolves to 'over' at 0.500 and is filtered out by any
-- threshold worth listing; the tie-break is cosmetic.
--
-- ---------------------------------------------------------------------------
-- THE MODEL'S CALL TRAVELS WITH IT, AND THAT IS NOT DECORATION
-- ---------------------------------------------------------------------------
-- `model_side` and `display_confidence` are on every row so the page can show
-- where the model DISAGREES with the streak. It frequently will: a hit rate is
-- a fact about games already played against a line drawn today, and the model
-- is a forecast. Publishing the streak alone would let this page contradict the
-- board silently, on the same player, on the same market, in the same week.
--
-- ---------------------------------------------------------------------------
-- ALWAYS FILTER ON season AND week
-- ---------------------------------------------------------------------------
-- Unfiltered, this grades every priced prop in the database. Every caller in
-- `lib/data/cheat-sheet.ts` pins both, and the planner pushes them down into
-- `v_board_rows` before the lateral runs.
-- =============================================================================

create view v_cheat_sheet
with (security_invoker = true)
as
select
  b.projection_id,
  b.pick_id,
  b.sport,
  b.season,
  b.week,

  b.player_id,
  b.player_name,
  b.position_group,

  b.team_id,
  b.team_school,
  b.team_abbreviation,
  b.team_color,
  b.team_alt_color,

  b.opponent_team_id,
  b.opponent_school,
  b.opponent_abbreviation,
  b.opponent_rank_vs_position,

  b.game_id,
  b.start_date,
  b.is_home,
  b.neutral_site,

  b.market_key,
  b.market_label,
  b.market_emoji,
  b.is_binary,
  b.line,

  -- The model's own view of the same prop, for the agreement badge.
  b.side as model_side,
  b.display_confidence,
  b.edge,
  b.has_call,
  b.has_book_line,
  b.sportsbook_key,

  b.conference_name,
  b.conference_is_displayed,

  w.window_size,
  g.decided,
  g.pushes,
  greatest(g.overs, g.unders) as hits,
  case when g.overs >= g.unders then 'over' else 'under' end as hit_side,
  (greatest(g.overs, g.unders)::numeric / nullif(g.decided, 0)) as hit_rate

from v_board_rows b
join markets m on m.key = b.market_key

-- L5 and L10, the two windows the board's own hit-rate control offers
-- (`app_config.hit_rate_windows`). Emitted as rows rather than as l5_/l10_
-- column pairs so that one `eq` picks a window and one `gte` applies the
-- threshold -- with column pairs, every threshold would need its own column.
cross join (values (5), (10)) as w(window_size)

cross join lateral (
  select
    count(*) filter (where r.value <> b.line) as decided,
    count(*) filter (where r.value >  b.line) as overs,
    count(*) filter (where r.value <  b.line) as unders,
    count(*) filter (where r.value =  b.line) as pushes
  from (
    select
      case m.stat_column
        when 'pass_yards'       then l.pass_yards
        when 'pass_tds'         then l.pass_tds
        when 'pass_attempts'    then l.pass_attempts
        when 'pass_completions' then l.pass_completions
        when 'interceptions'    then l.interceptions
        when 'rush_yards'       then l.rush_yards
        when 'rush_attempts'    then l.rush_attempts
        when 'rush_tds'         then l.rush_tds
        when 'targets'          then l.targets
        when 'receptions'       then l.receptions
        when 'rec_yards'        then l.rec_yards
        when 'rec_tds'          then l.rec_tds
        when 'offensive_tds'    then l.offensive_tds
        -- Deliberately no ELSE. A market added by INSERT with an unmapped
        -- stat_column produces NULL, which the filter below drops, so it shows
        -- as "not enough games" rather than as a confident wrong number from a
        -- coincidental match. Same rule as STAT_COLUMN_FIELDS in hit-rate.ts.
      end::numeric as value
    from v_player_game_log l
    where l.player_id = b.player_id
      and l.season    = b.season
      -- No lookahead: earlier weeks of the same season only.
      and l.week      < b.week
      -- Dropped BEFORE the window is taken, matching gradeGames. Inside the
      -- subquery rather than outside it, so a blank game does not consume one
      -- of the five slots and silently shorten the sample.
      and case m.stat_column
            when 'pass_yards'       then l.pass_yards
            when 'pass_tds'         then l.pass_tds
            when 'pass_attempts'    then l.pass_attempts
            when 'pass_completions' then l.pass_completions
            when 'interceptions'    then l.interceptions
            when 'rush_yards'       then l.rush_yards
            when 'rush_attempts'    then l.rush_attempts
            when 'rush_tds'         then l.rush_tds
            when 'targets'          then l.targets
            when 'receptions'       then l.receptions
            when 'rec_yards'        then l.rec_yards
            when 'rec_tds'          then l.rec_tds
            when 'offensive_tds'    then l.offensive_tds
          end is not null
    order by l.week desc
    limit w.window_size
  ) r
) g

where b.line is not null;

comment on view v_cheat_sheet is
  'One row per (priced prop, hit-rate window) carrying how often the player has already cleared TODAY''S line in his recent games, plus the model''s own call on the same prop. Grading is transcribed from gradeGames/hitRate in lib/core/hit-rate.ts -- nulls dropped before the window is taken, pushes excluded from the denominator -- and is point-in-time (same season, strictly earlier weeks). ALWAYS filter on season and week; unfiltered it grades every priced prop in the database.';

comment on column v_cheat_sheet.hit_rate is
  'The winning side''s share of DECIDED games, so it is always >= 0.5 and never null unless nothing was decided. Materialised rather than computed by the client because PostgREST cannot compare two columns, and filtering after the fetch would meet the silent 1,000-row cap.';

comment on column v_cheat_sheet.hit_side is
  'Which side of the line the history fell on -- NOT the model''s pick, which is model_side. The two disagree often and the page shows both; a streak is a fact about games already played, a call is a forecast.';

comment on column v_cheat_sheet.decided is
  'Games in the window that resolved either way. Below the window size when the player has played fewer games or when a game pushed. A rate over a tiny denominator is the failure mode of every cheat sheet -- 3 of 3 is 100% and means almost nothing -- so callers impose a minimum (lib/core/cheat-sheet.ts).';

comment on column v_cheat_sheet.line is
  'The line the prop carries NOW, applied to every past game. That is the `threshold` hit-rate basis of CLAUDE.md §9.2, settled in migration 0017: the player never actually faced this number in those games. The alternative, grading each game against its own closing line, needs a paid historical line backfill this project does not have.';
