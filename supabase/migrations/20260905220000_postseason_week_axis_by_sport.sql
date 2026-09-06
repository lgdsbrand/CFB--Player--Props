-- =============================================================================
-- 0050 -- the postseason week axis, per sport
-- =============================================================================
-- Migration 0033 (20260802140000) made postseason weeks unrepresentable in a
-- regular-season slot:
--
--     check (season_type <> 'postseason' or week > 20)
--
-- It exists because CFBD RESTARTS postseason numbering at 1. Bowl games were
-- stored as week 1, and `week` is the time axis for every point-in-time cutoff
-- in this schema, so December results leaked into every "through week N"
-- aggregation for the whole season. That is real lookahead, it survived eight
-- audit checks, and the constraint is what stops it coming back through a
-- hand-written INSERT or an adapter that forgets the offset.
--
-- NONE OF THAT REASONING APPLIES TO THE NFL, AND THE CONSTANT IS WHY.
-- nflverse does not restart: its postseason continues the regular season's
-- numbering, so the source is already monotone in time.
--
--     regular      1-18
--     Wild Card      19      <- fails `week > 20`
--     Divisional     20      <- fails `week > 20`
--     Conference     21
--     Super Bowl     22
--
-- So loading any completed NFL season -- 285 games, of which 13 are postseason
-- -- is rejected on the wild-card and divisional rounds. Loudly, which is the
-- constraint working; but rejected for a hazard that does not exist here.
--
-- ---------------------------------------------------------------------------
-- WHAT THE INVARIANT ACTUALLY IS
-- ---------------------------------------------------------------------------
-- Not "week > 20". That is a college artifact: CFB regular seasons run to about
-- week 15-16, so 20 was a safe boundary to offset past. The invariant is
--
--     a postseason game may not occupy a week a regular-season game could also
--     occupy, in the same sport
--
-- and the boundary that expresses it is the last regular-season week of the
-- sport in question: 20 for college (a padded 15-16), 18 for the NFL (exact,
-- and fixed since 2021).
--
-- The threshold therefore becomes sport-aware rather than being relaxed.
-- Relaxing it -- dropping it, or lowering it to 18 for everyone -- would give
-- the college side back exactly the hole that produced the bowl-game bug, and
-- that bug was invisible to every test in the project.
--
-- ---------------------------------------------------------------------------
-- WHY NOT JUST OFFSET NFL WEEKS THE WAY COLLEGE ONES ARE OFFSET
-- ---------------------------------------------------------------------------
-- Adding POSTSEASON_WEEK_OFFSET to an already-monotone source would store the
-- Super Bowl as week 42 with nothing in 23..38. It satisfies the old constraint
-- and costs three things: every NFL week number stops matching the number the
-- source, the broadcasts and the client all use; a `week_for_api` round trip
-- becomes necessary for a provider that never needed one; and the gap makes
-- any "weeks between" arithmetic wrong. Storing what the source says, and
-- checking it against the right boundary, is the smaller change and the more
-- honest one.
--
-- ---------------------------------------------------------------------------
-- SAFE TO REPLACE IN PLACE
-- ---------------------------------------------------------------------------
-- The new predicate is strictly weaker than the old one ONLY for `sport =
-- 'nfl'`, and there is not yet a single NFL postseason row -- migration 0049
-- landed 2026's regular season alone (272 games, weeks 1-18). For `sport =
-- 'cfb'` it is byte-for-byte the same rule. So no existing row can fail the
-- new CHECK, and none can newly pass one it should not.
-- =============================================================================

alter table games drop constraint games_postseason_week_offset;

alter table games
  add constraint games_postseason_week_offset
  check (
    season_type <> 'postseason'
    or week > case when sport = 'nfl' then 18 else 20 end
  );

comment on constraint games_postseason_week_offset on games is
  'A postseason game may not occupy a week a regular-season game of the same sport could occupy. The boundary is that sport''s last regular-season week: 20 for college (padded past a 15-16 week season, because CFBD restarts postseason numbering at 1 and once put bowl games in week 1), 18 for the NFL (exact, and its source numbering already continues past it). week is the time axis for every point-in-time cutoff in this schema, so it must be monotone in time.';

comment on column games.week is
  'The week ordinal used as the time axis for every point-in-time cutoff in this schema. A feature computed with as_of_week = N may only read games with week < N in the same season. MONOTONE IN TIME BY CONSTRUCTION, by whichever route the sport requires: college postseason weeks are stored offset past the regular season (see POSTSEASON_WEEK_OFFSET) because CFBD restarts them at 1; NFL weeks are stored exactly as the source numbers them, because its postseason already continues from 18 to 19-22.';
