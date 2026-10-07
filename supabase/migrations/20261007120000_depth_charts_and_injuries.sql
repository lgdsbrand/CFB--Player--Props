-- 0086: depth charts and injury reports, per team per week (client, 2026-10-06).
--
-- The client asked for starters and injuries on the game page, after the
-- Bettor Odds card. NFL only today: nflverse publishes ESPN's depth charts
-- twice a day, and Sleeper's public API carries each player's current injury
-- status. College has no source for either. The tables carry no sport column
-- (CLAUDE.md §3): sport comes through team_id, like every downstream table.
--
-- ONE SET PER TEAM PER WEEK, REWRITTEN UNTIL THAT TEAM'S GAME KICKS OFF.
-- Both sources are "now" snapshots with no history we can re-read later, so
-- each daily run writes the team's NEXT game's week and replaces it until
-- kickoff. After kickoff the next run is writing the following week, so the
-- old set is never touched again: a past game's page shows the depth chart and
-- injuries as they stood going into it, never today's (CLAUDE.md §4). A team
-- on a bye simply gets its next game's week written early.
--
-- player_id is NULLABLE on both. A source row whose player does not resolve
-- to one of ours is kept with its name rather than dropped: the page can still
-- print "J. Smith, Out", and a dropped row would read as a healthy roster.
-- Measured 2026-10-07: depth 2,282 of 2,288 resolve (every QB/RB/WR/TE in the
-- top three); Sleeper injuries 573 of 581 (188 of 190 QB/RB/WR/TE).

create table depth_charts (
  season          smallint not null,
  week            smallint not null,
  team_id         bigint not null references teams(id) on delete cascade,
  position_group  text not null,
  slot            smallint not null,
  depth           smallint not null check (depth >= 1),
  position        text not null,
  player_id       bigint references players(id) on delete set null,
  player_name     text not null,
  source_as_of    timestamptz not null,
  written_at      timestamptz not null default now(),
  primary key (season, week, team_id, position_group, slot, depth)
);

comment on table depth_charts is
  'Each team''s depth chart going into a week''s game (migration 0086). NFL from nflverse depth_charts_{season} (ESPN), latest snapshot, rewritten daily until the team''s game kicks off and frozen after. One row per (group, slot, depth): a starter is depth 1. WR has three slots in the offense group.';
comment on column depth_charts.position_group is
  'The source''s personnel group: ''3WR 1TE'' (offense), ''Base 3-4 D'' or ''Base 4-3 D'' (defense), ''Special Teams''.';
comment on column depth_charts.slot is
  'The source''s slot within the group (pos_slot). Not a rank: WR occupies slots 1, 2 and 8 in the offense group, each with its own depth order.';
comment on column depth_charts.source_as_of is
  'When the provider took the snapshot these rows came from (nflverse `dt`), not when we wrote them.';

create index depth_charts_team_week_idx on depth_charts (team_id, season, week);

create table player_injuries (
  season             smallint not null,
  week               smallint not null,
  team_id            bigint not null references teams(id) on delete cascade,
  source             text not null,
  source_player_id   text not null,
  player_id          bigint references players(id) on delete set null,
  player_name        text not null,
  position           text,
  status             text not null,
  body_part          text,
  notes              text,
  source_updated_at  timestamptz,
  written_at         timestamptz not null default now(),
  primary key (season, week, team_id, source, source_player_id)
);

comment on table player_injuries is
  'Players carrying an injury designation going into a week''s game (migration 0086). NFL from Sleeper''s public /players/nfl, read once a day as Sleeper asks, rewritten until the team''s game kicks off and frozen after. A player with no designation has no row.';
comment on column player_injuries.status is
  'As the source spells it: Out, Doubtful, Questionable (game designations); IR, PUP, Sus, NA, DNR (longer-term). No CHECK on purpose: a new upstream value must not fail the whole ingest; the site prints an unknown one as is.';
comment on column player_injuries.source_player_id is
  'The source''s own player id (Sleeper player_id). The key, because player_id is nullable and a source row with no match is still kept.';
comment on column player_injuries.source_updated_at is
  'Sleeper''s news_updated for the player, when present: when the source last changed something about him.';

create index player_injuries_team_week_idx on player_injuries (team_id, season, week);

alter table depth_charts enable row level security;
alter table player_injuries enable row level security;

create policy depth_charts_public_read on depth_charts
  for select to anon, authenticated using (true);
create policy player_injuries_public_read on player_injuries
  for select to anon, authenticated using (true);
