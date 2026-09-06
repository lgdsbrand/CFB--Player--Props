-- =============================================================================
-- 0051 -- the NFL play key
-- =============================================================================
-- N4 loads nflverse play-by-play into `plays` and its attribution into
-- `play_player_stats`. Those two tables are written as one graph across a
-- foreign key, and the second cannot be written until the first has ids, so the
-- loader needs to read back the row it just wrote. On the college side that
-- read-back keys on `plays.cfbd_id`. NFL rows have no cfbd_id, so this
-- migration adds the column that answers the same question for nflverse -- and
-- nothing else.
--
-- ---------------------------------------------------------------------------
-- WHY IT IS SHAPED DIFFERENTLY FROM `cfbd_id`, WHICH IS GLOBALLY UNIQUE
-- ---------------------------------------------------------------------------
-- Measured from the 2025 release on 2026-09-06, not assumed. That season's
-- file holds 48,771 plays across 285 games, and:
--
--     distinct play_id             4,850
--     distinct (game_id, play_id)  48,771
--
-- `play_id` is unique WITHIN a game and restarts every game. It is an ordinal
-- of position in the drive chart rather than a counter -- the first values of a
-- game are 1, 40, 63, 85, 115, 135 -- so it is small, gappy, and reused by
-- every game in the season. CFBD's play id is a global identifier and is
-- therefore declared `bigint unique`; copying that declaration here would
-- reject the second game of every season.
--
-- So the key is `(game_id, nflverse_play_id)`. That is not a workaround for a
-- weaker source, it is the actual identity of an nflverse play: the file itself
-- joins on the pair.
--
-- PARTIAL, because 344,994 college rows have no nflverse id and never will.
-- Postgres treats NULLs as distinct, so a plain unique index would admit them
-- all -- correct, but it would also index a third of a gigabyte of nothing.
-- Excluding them keeps the index proportional to the rows it can serve.
--
-- ---------------------------------------------------------------------------
-- NO CHECK CONSTRAINT, AND THE REASON IS STRUCTURAL RATHER THAN A JUDGEMENT
-- ---------------------------------------------------------------------------
-- 0049 required `teams.nfl_abbr` on NFL rows because the source guarantees it
-- for all 32 forever, and declined to require `players.gsis_id` because the
-- source genuinely omits one. By that rule an NFL play WOULD earn a CHECK:
-- every row in the play-by-play file carries a play_id.
--
-- It cannot have one. `plays` has no `sport` column -- deliberately, since it
-- inherits sport through `game_id` (migration 0035) -- and a CHECK may not
-- contain a subquery, so there is no expression available here that can say
-- "NFL rows must carry this". Adding a `sport` column to `plays` purely to make
-- the CHECK expressible would denormalise a 345k-row table to assert something
-- the loader already guarantees, and would create the second copy of a fact
-- that 0035 was explicit about not creating.
--
-- The assertion lives in `audit_data` instead, where it can join.
-- =============================================================================

alter table plays
  add column nflverse_play_id bigint;

comment on column plays.nflverse_play_id is
  'nflverse play identifier, unique WITHIN a game rather than globally -- see the unique index on (game_id, nflverse_play_id). Null on every college row, which keys on cfbd_id instead. Exists so the attribution loader can read back the play ids it just wrote.';

create unique index plays_nflverse_play_idx
  on plays (game_id, nflverse_play_id)
  where nflverse_play_id is not null;
