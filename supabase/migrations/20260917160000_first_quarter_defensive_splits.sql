-- =============================================================================
-- 0069 — What a defense concedes to each position IN THE FIRST QUARTER
-- =============================================================================
-- The client, 2026-09-17: "what the defense might give up in 1st quarter (if
-- those stats are available)". They are: `plays.period` is populated for both
-- sports and `play_player_stats` joins it by `play_id`, so the first-quarter
-- slice of the position-split engine (CLAUDE.md §5) is one FILTER on the same
-- aggregate that already runs. No new ingest, no new provider.
--
-- AN OBSERVATION, NOT AN INFERENCE, and that is why these columns land HERE and
-- not on `defense_position_ratings`. This table records what one defense
-- allowed to one position in one game -- true forever, needing no knowledge
-- cutoff. The adjusted, fitted, ranked figure is the other table's job, is only
-- meaningful relative to an `as_of_week`, and is a separate decision that has
-- to be measured before it is published: a quarter is a quarter of the sample
-- at roughly twice the noise relative to its mean, and an opponent adjustment
-- fitted on that may be reporting a confident spurious gap.
--
-- So this migration makes the RAW first-quarter numbers available, which is
-- exactly what the defense-detail panel shows and what the client described
-- reading. Whether a first-quarter RANK is worth publishing is answered against
-- these rows once they exist, not before.
--
-- WHICH COLUMNS, AND WHY NOT ALL OF THEM
-- --------------------------------------
-- The seven the panel and the markets actually use, plus the play count as
-- their denominator. Deliberately absent:
--
--   first_downs_allowed        no first-quarter market or panel column
--   explosive_plays_allowed    same
--   goal_line_*                feeds the anytime-TD model, which is whole-game
--   ppa_allowed               `numeric`, feeds the adjustment, and the
--                              adjustment is not being extended here
--
-- Adding a column nothing reads is storage plus a second thing to keep correct.
--
-- BOTH SPORTS, NOT NFL ONLY. `plays.period` is populated for college too, the
-- aggregate is identical, and the split engine is sport-agnostic core
-- (CLAUDE.md §3) -- branching it on a sport name here would put a league into
-- the one module whose docstring exists to say it holds none. College has no
-- first-quarter MARKET, because no college book posts one; that is a fact about
-- books, not a reason for the engine to compute a different thing.
--
-- NULLABLE, like every other column here. NULL means "not derived yet" for a
-- row written before this migration; 0 means "nothing conceded in the quarter".
-- A backfill is `build_splits` re-run for the season, which rewrites the row.
-- =============================================================================

alter table defense_position_game_splits
  add column if not exists q1_plays                smallint,
  add column if not exists q1_rush_attempts        smallint,
  add column if not exists q1_rush_yards_allowed   smallint,
  add column if not exists q1_rush_tds_allowed     smallint,
  add column if not exists q1_targets              smallint,
  add column if not exists q1_receptions_allowed   smallint,
  add column if not exists q1_rec_yards_allowed    smallint,
  add column if not exists q1_rec_tds_allowed      smallint;

comment on column defense_position_game_splits.q1_plays is
  'Plays in period 1 attributed to this position against this defense. The '
  'denominator for the other q1_ columns; NULL means the row predates the '
  'first-quarter aggregate, 0 means the position saw no first-quarter action.';

-- =============================================================================
-- The defense-detail view carries them too
-- =============================================================================
-- `v_defense_position_game_log` is what the player page's DEFENSE DETAIL panel
-- reads -- a headline feature by CLAUDE.md §7, and the one place the core
-- signal becomes something a reader can check against games they watched. A
-- first-quarter market needs the first-quarter row of that same table, or the
-- panel answers a different question from the one the rest of the page is
-- asking.
--
-- Appended after the existing columns: create-or-replace may only ADD at the
-- end, and renaming the columns a deployed reader selects would break it.
-- =============================================================================

create or replace view v_defense_position_game_log as
select s.id          as split_id,
       s.game_id,
       s.defense_team_id,
       s.offense_team_id,
       o.school       as offense_school,
       o.abbreviation as offense_abbreviation,
       s.season,
       s.week,
       s.position_group,
       g.start_date,
       g.neutral_site,
       g.home_team_id = s.defense_team_id as defense_is_home,
       s.plays,
       s.rush_attempts,
       s.rush_yards_allowed,
       s.rush_tds_allowed,
       s.targets,
       s.receptions_allowed,
       s.rec_yards_allowed,
       s.rec_tds_allowed,
       s.q1_plays,
       s.q1_rush_attempts,
       s.q1_rush_yards_allowed,
       s.q1_rush_tds_allowed,
       s.q1_targets,
       s.q1_receptions_allowed,
       s.q1_rec_yards_allowed,
       s.q1_rec_tds_allowed
  from defense_position_game_splits s
  join games g on g.id = s.game_id
  join teams o on o.id = s.offense_team_id;
