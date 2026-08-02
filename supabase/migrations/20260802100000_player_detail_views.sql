-- =============================================================================
-- 0019 — Reads the player detail view needs
-- =============================================================================
-- Two changes, both driven by CLAUDE.md §7's player detail requirements.
--
-- (A) v_player_game_log gains opponent_team_id. The view named the opponent but
--     not its id, which is enough for a game log and not enough for the
--     "vs-rank" hit-rate split — that has to join each past game's opponent to
--     defense_position_ratings, which is keyed by defense_team_id. Matching on
--     school name instead would be a join on a display string.
--
--     Appended at the END of the select list, which is the only shape
--     CREATE OR REPLACE VIEW permits: existing columns may not be renamed,
--     reordered or dropped. So this cannot break the 4b read layer.
--
-- (B) v_defense_position_game_log exposes defense_position_game_splits one game
--     at a time, with the offense named. The defense detail panel is a headline
--     feature, not a nice-to-have (§7), and it asks a specific question: what
--     has THIS WEEK'S OPPONENT allowed to THIS PLAYER'S POSITION, game by game.
--
--     RAW AND OPPONENT-UNADJUSTED, DELIBERATELY. Ranks and anything feeding a
--     projection use the adjusted figures in defense_position_ratings, because
--     unbalanced schedules make raw defensive numbers misleading (§5). But a
--     game-by-game panel is a list of things that happened, and an adjusted
--     yardage total is not a number the reader can look up anywhere. Both are
--     available and each is labelled for what it is.
--
--     NO CUTOFF PARAMETER, unlike defense_position_splits_through. That function
--     aggregates, and an aggregate without a cutoff is exactly how lookahead
--     enters. These rows are atomic observations carrying their own week, so the
--     caller filters and the filter is visible in the query.
-- =============================================================================

create or replace view v_player_game_log
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
  s.offensive_tds,
  s.opponent_team_id
from player_game_stats s
join games g on g.id = s.game_id
join teams o on o.id = s.opponent_team_id;

comment on view v_player_game_log is
  'Per-game actuals for the player detail view''s game log and last-N hit-rate chart (CLAUDE.md §7). Hit-rate calculation stays in the app/worker because it depends on app_config.hit_rate_basis. opponent_team_id is what lets the vs-rank split join each past opponent to defense_position_ratings at that game''s own as_of_week.';

create view v_defense_position_game_log
with (security_invoker = true)
as
select
  s.id                            as split_id,
  s.game_id,
  s.defense_team_id,
  s.offense_team_id,
  o.school                        as offense_school,
  o.abbreviation                  as offense_abbreviation,
  s.season,
  s.week,
  s.position_group,
  g.start_date,
  g.neutral_site,
  (g.home_team_id = s.defense_team_id) as defense_is_home,
  s.plays,
  s.rush_attempts,
  s.rush_yards_allowed,
  s.rush_tds_allowed,
  s.targets,
  s.receptions_allowed,
  s.rec_yards_allowed,
  s.rec_tds_allowed
from defense_position_game_splits s
join games g on g.id = s.game_id
join teams o on o.id = s.offense_team_id;

comment on view v_defense_position_game_log is
  'One defense, one position, one game — what was actually allowed, with the offence named. Backs the defense detail panel in CLAUDE.md §7. RAW and opponent-UNADJUSTED on purpose: the reader is being shown events, not the model''s view of them. The adjusted equivalents and the rank live in defense_position_ratings and must be labelled as such wherever the two appear together.';
