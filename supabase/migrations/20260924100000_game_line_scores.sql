-- =============================================================================
-- 0076 — Points by quarter, as CFBD reports them
-- =============================================================================
-- The game model projects 1Q and 1H as well as the full game (CLAUDE.md §11),
-- and grading those needs the score at the end of each period. CFBD's /games
-- response has always carried it (`homeLineScores: [0, 7, 0, 14]`); the
-- ingest simply dropped it. Stored as CFBD sends it rather than rebuilt from
-- play-by-play, because the play feed's running score is a derived number
-- with its own gaps, and this one is the official line score.
--
-- One element per period; overtime periods follow the fourth. NULL for a game
-- not yet played, and for any game whose provider sends no line score (the
-- NFL adapter, for now).
--
-- AN ARRAY THAT DOES NOT SUM TO THE FINAL IS STORED ANYWAY, and the view below
-- says so per game (`line_scores_consistent`) rather than hiding the row.
-- Deciding what a grade does with a bad row is the grader's job, and it can
-- only make that decision if the row is visible.
-- =============================================================================

alter table games
  add column home_line_scores smallint[],
  add column away_line_scores smallint[];

comment on column games.home_line_scores is
  'Home points per period from CFBD /games (homeLineScores): Q1..Q4 then any overtime periods. NULL until played or when the provider sends none.';
comment on column games.away_line_scores is
  'Away points per period from CFBD /games (awayLineScores). See home_line_scores.';

create view v_game_period_scores
with (security_invoker = true)
as
select
  g.id as game_id,
  g.sport,
  g.season,
  g.week,
  g.home_line_scores[1]                         as home_q1,
  g.away_line_scores[1]                         as away_q1,
  g.home_line_scores[1] + g.home_line_scores[2] as home_h1,
  g.away_line_scores[1] + g.away_line_scores[2] as away_h1,
  g.home_points,
  g.away_points,
  cardinality(g.home_line_scores) > 4           as overtime,
  (select sum(x) from unnest(g.home_line_scores) x) = g.home_points
    and (select sum(x) from unnest(g.away_line_scores) x) = g.away_points
                                                as line_scores_consistent
from games g
where g.completed
  and cardinality(g.home_line_scores) >= 4
  and cardinality(g.away_line_scores) >= 4;

comment on view v_game_period_scores is
  'First-quarter and first-half points per completed game, from CFBD line scores. line_scores_consistent is false when the periods do not sum to the final — kept visible, not filtered, so the grader decides.';
