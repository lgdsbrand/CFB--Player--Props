-- =============================================================================
-- 0032 — AP / Coaches / CFP poll rankings, and the Top 25 filter
-- =============================================================================
-- Client request: a Top 25 filter beside the conference filter, so the board can
-- be narrowed to games and players from ranked teams.
--
-- COSTS NO ODDS API CREDITS. CFBD serves /rankings on the tier already paid for,
-- one call per season.
--
-- -----------------------------------------------------------------------------
-- THE WEEK AXIS IS ALREADY POINT-IN-TIME, AND THAT WAS PROVEN, NOT ASSUMED
-- -----------------------------------------------------------------------------
-- CLAUDE.md §4 calls applying end-of-season information to earlier weeks a
-- silent, disqualifying bug, and a poll is the most tempting place to make it:
-- "Alabama were a top-10 team in 2025" is true of the season and false of most
-- weeks in it.
--
-- CFBD's week N poll is the poll available ENTERING week N — it reflects games
-- through week N-1. Verified against 2025 rather than inferred from the docs:
-- every ranked team that LOST in week 1 still holds its high ranking in the
-- week 1 poll and drops in the week 2 poll.
--
--     Texas         week 1 poll #1   ->  week 2 poll #7
--     Clemson       week 1 poll #4   ->  week 2 poll #8
--     Alabama       week 1 poll #8   ->  week 2 poll #21
--     Kansas State  week 1 poll #17  ->  week 2 poll unranked
--
-- So `week` here needs NO offset to be a knowledge cutoff: the row for week N is
-- what a reader would have seen on the Sunday before week N's games. The week 1
-- poll is the preseason poll, which is why it exists at all before any football
-- has been played.
--
-- This is the same discipline as team_rating_snapshots, and the reason that
-- table splits point_in_time from season_final with a CHECK constraint. No such
-- split is needed here because CFBD publishes no "final poll applied backwards"
-- variant — every row is already dated.
--
-- -----------------------------------------------------------------------------
-- FBS POLLS ONLY
-- -----------------------------------------------------------------------------
-- /rankings returns five polls per week and three of them are for other
-- divisions: "FCS Coaches Poll", "AFCA Division II Coaches Poll", "AFCA
-- Division III Coaches Poll". Storing those would put a Division III school's
-- #1 ranking beside an FBS team's, and the ingest filters them out. The filter
-- lives in the adapter rather than in a CHECK so an unrecognised FBS poll is a
-- log line rather than a failed run.
-- =============================================================================

create table team_poll_rankings (
  id                  bigint generated always as identity primary key,
  team_id             bigint not null references teams(id) on delete cascade,
  season              smallint not null,

  -- On the same monotone season axis as games.week, so a bowl cannot compare
  -- equal to a September game. See migration 0020.
  week                smallint not null,
  poll                text not null,

  rank                smallint not null,
  first_place_votes   smallint,
  points              integer,

  ingested_at         timestamptz not null default now(),
  unique (season, week, poll, team_id),
  constraint team_poll_rankings_rank_positive check (rank >= 1),
  constraint team_poll_rankings_week_positive check (week >= 1)
);

comment on table team_poll_rankings is
  'AP, Coaches and CFP committee rankings, one row per team per poll per week. POINT-IN-TIME BY CONSTRUCTION: the row for week N is the poll published entering week N, reflecting games through week N-1 — verified against 2025, where every ranked team that lost in week 1 still holds its ranking in the week 1 poll and falls in the week 2 poll. A query joining on week is therefore already free of lookahead and needs no as-of offset.';

comment on column team_poll_rankings.week is
  'Knowledge cutoff. The poll as it stood BEFORE week N was played. Week 1 is the preseason poll.';

comment on column team_poll_rankings.poll is
  'FBS polls only: "AP Top 25", "Coaches Poll", "Playoff Committee Rankings". The FCS and Division II/III polls CFBD returns alongside them are dropped at ingest.';

create index team_poll_rankings_lookup_idx
  on team_poll_rankings (season, week, poll, rank);

alter table team_poll_rankings enable row level security;

create policy team_poll_rankings_public_read
  on team_poll_rankings for select
  to anon, authenticated
  using (true);
