-- =============================================================================
-- 0071 — Usage share: what fraction of his own offence a player is
-- =============================================================================
-- The client's fifth ask: "Also was thinking about usage % or target and run
-- share %". It belongs to the same reading he described for the first-quarter
-- board -- book line, recent hit rate, what the defence gives up -- and answers
-- the question those three leave open: is this player the offence, or a piece
-- of it. A 9-target night means one thing at a 30% target share and another at
-- 12%.
--
-- PER GAME, NOT PER SEASON, and stored rather than aggregated on read. The
-- board already fetches every visible player's game log to build the L5/L10
-- row, so a per-game column arrives on a query that is already being run: no
-- new join, no new round trip, and no aggregate over a table whose read ceiling
-- is CPU (see `board-ceiling-is-cpu-not-query-shape`). A season-to-date figure
-- would instead need either a window function the player_id filter cannot be
-- pushed below, or a fourth as-of-keyed snapshot table. The window the reader
-- sees is then chosen in one place, `lib/core/usage-view.ts`, from the same
-- games the hit rate already uses.
--
-- THREE DENOMINATORS, EACH NAMED
-- ------------------------------
--   target_share  player targets        / all targets by his team that game
--   rush_share    player rush attempts  / all rush attempts by his team
--   snap_share    the provider's own `offense_pct` -- see below
--
-- Team totals are summed over `player_game_stats` itself, the same table the
-- numerator comes from, so numerator and denominator can never be drawn from
-- two sources that disagree.
--
-- WHY target_share IS WITHHELD ON AN INCOMPLETE BOX SCORE
-- ------------------------------------------------------
-- `targets` is not served by CFBD box scores at all; it is attributed play by
-- play during ingest (see the column comment in migration 0004), and that
-- attribution is not complete. Team targets as a fraction of the same team's
-- pass attempts, measured on production 2026-09-17:
--
--   sport / season        median   5th pct   team-games under 0.80
--   nfl  2025              0.963     0.871      1 of 570
--   nfl  2026              0.959     0.870      0 of 32
--   cfb  2025 (FBS only)   0.913     0.645    243 of 1,742
--   cfb  2026 (FBS only)   0.906     0.228     27 of 285
--
-- AND THE SEASON BEFORE IS WORSE, which is the reason a college player page can
-- show no target share at all for last year: cfb 2024 sits at a 0.667 median
-- and only 418 of its 1,797 team-games clear the threshold, while cfb 2023 was
-- ingested before target attribution existed and has none. College target share
-- is a 2025-onwards figure, and the guard is what says so rather than
-- publishing 2024 as though it were comparable.
--
-- The gap is NOT missing at random, so it does not cancel out of a ratio. It is
-- whole roster rows absent -- college box scores carry about 10 players per
-- team-game against the NFL's 35 -- so the tail of one- and two-target
-- receivers is dropped and everyone who survives absorbs their share. Paired
-- within player over cfb 2025 FBS, a player's mean target share in team-games
-- under the 0.80 ratio against his own complete games: WR +2.1pp, RB +2.2pp,
-- TE +0.9pp. All three inflate, which is the direction a shrinking denominator
-- predicts.
--
-- So the rule below is a measurement, not a taste: under 0.80, at least a fifth
-- of the team's pass plays produced no attributed target and the share is
-- withheld. Above it the residual inflation is inside a point or two, which a
-- figure displayed to the nearest percent can carry.
--
-- THE GUARD CHECKS COMPLETENESS, NOT VOLUME, and there is deliberately no
-- minimum-targets floor beside it. A team-game with 8 attributed targets out of
-- 8 pass attempts is complete, and a receiver with 6 of them really did see 75%
-- of the throws -- a floor would delete a true figure to tidy away an ugly one.
-- Measured on the rows any surface can actually show (FBS and NFL, QB/RB/WR/TE,
-- 2025 onwards): the NFL has NO team-game under 10 targets, and college has 57
-- rows of 13,927 (0.4%), worst share 75%. The 100%-share rows that do exist sit
-- on FCS teams at positions the board does not carry.
--
-- rush_share NEEDS NO SUCH GUARD, and that was measured too rather than
-- assumed: every rush has exactly one carrier and both providers report rushers
-- in the box score. Among FBS team-games, 2 of 1,742 (2025) and 0 of 285 (2026)
-- had an implausible team rush total. The 80-odd college team-games that do are
-- FCS opponents, whose box scores CFBD serves in fragments -- one or two stored
-- rows against ninety plays -- and no FCS player is displayed anywhere, because
-- conference selection is a display filter over FBS.
--
-- snap_share IS NFL-ONLY AND IS THE PROVIDER'S NUMBER
-- ---------------------------------------------------
-- College has 0 of 3,686 rows with a snap count in 2026 and 0 in every prior
-- season; there is no source. For the NFL it is `offense_pct` straight from
-- nflverse's snap_counts file, written by the same adapter that writes `snaps`,
-- because they arrive on one row and a column's writer should be the thing
-- holding the data.
--
-- IT IS NOT DERIVED HERE, and the reason is a trap worth recording: team
-- offensive snaps cannot be recovered from this table. A snap row exists for
-- every player on the field including the offensive line, but a box-score row
-- exists only for players with stats, so the largest snap count we store is a
-- skill player's -- always below the team's true total, always inflating a
-- share computed from it. The provider divides by the real denominator.
--
-- NOT A MODEL INPUT. Nothing here feeds a projection; these columns are read by
-- the board and the player page and by nothing else. That keeps them clear of
-- the point-in-time rules in CLAUDE.md §4 in the only way that counts: a share
-- describes the game it sits on, computed from that game alone, so there is no
-- as-of week for it to be wrong about. The surfaces still apply their own week
-- cut, because a game log shown beside a week-10 projection already stops at
-- week 9.
-- =============================================================================

alter table player_game_stats
  add column if not exists target_share numeric(5,4),
  add column if not exists rush_share   numeric(5,4),
  add column if not exists snap_share   numeric(5,4);

comment on column player_game_stats.target_share is
  'This player''s targets over every target his team recorded in this game. '
  'NULL where the team''s attributed targets fall below 80% of its pass '
  'attempts -- attribution is incomplete there and the share inflates by a '
  'measured 1-2 points (migration 0071). Written by build_usage_shares.';

comment on column player_game_stats.rush_share is
  'This player''s rush attempts over his team''s rush attempts in this game. '
  'No completeness guard: rushing attribution is complete for every FBS and NFL '
  'team-game measured (migration 0071). Written by build_usage_shares.';

comment on column player_game_stats.snap_share is
  'nflverse offense_pct -- the share of his team''s offensive snaps this player '
  'took. NFL ONLY; college has no snap source. Written by the snap adapter '
  'alongside `snaps`, NOT derived from this table, because team snaps cannot be '
  'recovered from box-score rows (the offensive line has none).';

-- =============================================================================
-- The game log carries them, which is all any surface needs
-- =============================================================================
-- ONE VIEW SERVES BOTH SURFACES. The player page reads this per player and the
-- board reads it in batches of twenty (`getGameLogsByPlayer`), so putting the
-- shares here puts them on the board and on the player page at once, with the
-- board's week cut already applied by the caller.
--
-- Appended last: create-or-replace may only add columns at the end.
-- =============================================================================

create or replace view v_player_game_log as
select s.player_id,
       s.game_id,
       s.season,
       s.week,
       s.position_group,
       s.is_home,
       o.abbreviation as opponent_abbreviation,
       o.school       as opponent_school,
       g.start_date,
       g.neutral_site,
       s.pass_attempts,
       s.pass_completions,
       s.pass_yards,
       s.pass_tds,
       s.interceptions,
       s.rush_attempts,
       s.rush_yards,
       s.rush_tds,
       s.targets,
       s.receptions,
       s.rec_yards,
       s.rec_tds,
       s.offensive_tds,
       s.opponent_team_id,
       s.q1_pass_yards,
       s.q1_rush_attempts,
       s.q1_rush_yards,
       s.q1_rush_tds,
       s.q1_targets,
       s.q1_receptions,
       s.q1_rec_yards,
       s.q1_rec_tds,
       s.q1_offensive_tds,
       s.target_share,
       s.rush_share,
       s.snap_share
  from player_game_stats s
  join games g on g.id = s.game_id
  join teams o on o.id = s.opponent_team_id;
