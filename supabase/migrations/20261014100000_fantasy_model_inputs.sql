-- 0093: inputs of the client's NFL "fantasy" game model (2026-10-14).
--
-- The client's Google Sheet projects each NFL game's score from the CBS Sports
-- fantasy projections of five starters a side (QB, RB, WR1, WR2, TE) plus a
-- handful of team numbers. Nobody has the code that loaded it onto his site,
-- so it is rebuilt here, with every input automatic (the user's instruction:
-- nothing for the client to type). This migration holds the INPUTS (phase F1);
-- the formula and its output come in F2.
--
-- Three tables, none with a sport column (CLAUDE.md §3: sport comes through
-- team_id). All three are point-in-time (CLAUDE.md §4):
--
--   fantasy_projections  CBS's weekly PPR projection per player, as captured.
--                        CBS serves only the current week (a past week's URL
--                        silently returns this week's table, measured
--                        2026-10-14), so these rows are the only history there
--                        will ever be. Rewritten until the team's game kicks
--                        off, frozen after, like depth_charts (core/team_weeks.py).
--   fantasy_lineups      The five starters the model uses per team per week,
--                        chosen automatically from the depth chart, the injury
--                        report and the CBS numbers. Frozen at kickoff the same
--                        way, so a past game shows who the model actually used.
--   fantasy_team_inputs  The team numbers, season to date, as of before week N:
--                        3rd-down and red-zone rates both ways, plays per game,
--                        EPA, and our own Massey-style and SRS ratings in place
--                        of the Massey and ESPN FPI figures the sheet pasted.

create table fantasy_projections (
  season          smallint not null,
  week            smallint not null,
  team_id         bigint not null references teams(id) on delete cascade,
  source          text not null,
  position        text not null check (position in ('QB', 'RB', 'WR', 'TE')),
  player_name     text not null,
  player_id       bigint references players(id) on delete set null,
  fantasy_points  numeric(5, 1) not null,
  source_as_of    timestamptz not null,
  written_at      timestamptz not null default now(),
  primary key (season, week, team_id, source, position, player_name)
);

comment on table fantasy_projections is
  'Weekly fantasy-point projections per player (migration 0093), the main input of the client''s fantasy game model. source ''cbs'' = CBS Sports PPR projections, fantasy points per game for that week. Captured daily for each team''s next game and frozen at kickoff: CBS keeps no history, so this table is it.';
comment on column fantasy_projections.player_name is
  'The source''s full name. Part of the key because player_id is nullable: a projection whose player does not resolve to ours is kept by name, never dropped.';
comment on column fantasy_projections.source_as_of is
  'When the page was read. CBS revises a week''s projections until kickoff; the last capture before the game is the one that stands.';

create index fantasy_projections_team_week_idx on fantasy_projections (team_id, season, week);

create table fantasy_lineups (
  season          smallint not null,
  week            smallint not null,
  team_id         bigint not null references teams(id) on delete cascade,
  slot            text not null check (slot in ('QB', 'RB', 'WR1', 'WR2', 'TE')),
  player_id       bigint references players(id) on delete set null,
  player_name     text,
  fantasy_points  numeric(5, 1) not null,
  points_source   text not null check (points_source in ('cbs', 'default')),
  picked_by       text not null check (picked_by in ('depth_chart', 'projection', 'none')),
  skipped         text,
  source_as_of    timestamptz not null,
  written_at      timestamptz not null default now(),
  primary key (season, week, team_id, slot)
);

comment on table fantasy_lineups is
  'The five starters the fantasy game model uses per team per week (migration 0093), chosen automatically: in every slot, the available players at the position with the highest CBS projection (QB, RB, the top two WRs, TE). The depth chart supplies the QB only when CBS lists no available QB for the team. Available = no Out, Doubtful, IR, PUP, suspension, NA or DNR designation. Frozen at kickoff.';
comment on column fantasy_lineups.points_source is
  '''cbs'' = the player''s CBS projection; ''default'' = the player was not on CBS''s list, so the sheet''s fallback applies (QB 17, RB 16, WR 16, TE 12), exactly as the sheet''s XLOOKUP does.';
comment on column fantasy_lineups.picked_by is
  '''projection'' normally; ''depth_chart'' for a QB taken from the depth chart because CBS listed no available QB; ''none'' when no available player exists at the position (player_name null, default points).';
comment on column fantasy_lineups.skipped is
  'Players passed over for this slot and why, e.g. ''J. Smith (Out)'', so the game page can show it. Null when nobody was skipped.';

create table fantasy_team_inputs (
  id                   bigint generated always as identity primary key,
  team_id              bigint not null references teams(id) on delete cascade,
  season               smallint not null,
  as_of_week           smallint not null check (as_of_week >= 1),
  games_included       smallint not null check (games_included >= 0),
  pbp_games_included   smallint not null check (pbp_games_included >= 0),

  third_down_pct       numeric(5, 4),
  opp_third_down_pct   numeric(5, 4),
  red_zone_pct         numeric(5, 4),
  opp_red_zone_pct     numeric(5, 4),
  plays_pg             numeric(5, 2),
  off_epa_play         numeric(6, 4),
  def_epa_play         numeric(6, 4),
  off_epa_total        numeric(7, 2),
  def_epa_total        numeric(7, 2),

  massey_off           numeric(5, 2),
  massey_def           numeric(5, 2),
  home_field           numeric(4, 2),
  srs                  numeric(5, 2),

  created_at           timestamptz not null default now(),
  unique (team_id, season, as_of_week)
);

comment on table fantasy_team_inputs is
  'Team numbers for the fantasy game model (migration 0093), RAW season to date as the client''s sheet uses them (not opponent-adjusted, unlike team_strength_ratings), POINT IN TIME: as_of_week = N uses only regular-season games with week < N. Rates are fractions (0.4615), as in the sheet''s Stats tab.';
comment on column fantasy_team_inputs.games_included is
  'Completed games behind massey_* and srs (final scores, from games).';
comment on column fantasy_team_inputs.pbp_games_included is
  'Games behind the play-by-play figures. Can trail games_included by a few days: nflverse publishes play-by-play after the final score is known.';
comment on column fantasy_team_inputs.red_zone_pct is
  'Touchdowns per red-zone trip (a drive with a snap at or inside the opponent 20), the sheet''s "Red Zone Scoring %". opp_ = allowed.';
comment on column fantasy_team_inputs.def_epa_play is
  'EPA per play ALLOWED (lower is a better defense), the same orientation as the SumerSports defense table the sheet read.';
comment on column fantasy_team_inputs.massey_off is
  'Our Massey-style offense rating: points this team would score against an average defense. Fitted by least squares on final scores as points = off − opponent def (+ home_field at home), with a pull toward average worth three games (core/fantasy_inputs.py), which keeps the spread on the scale of the Massey and FPI figures the sheet was weighted on.';
comment on column fantasy_team_inputs.massey_def is
  'Our Massey-style defense rating, in points: HIGHER is a BETTER defense (opponents score off − def). Averages zero across the league, as Massey''s does.';
comment on column fantasy_team_inputs.home_field is
  'League-wide home-field points from the same fit, repeated on every row. Massey publishes one per team; six-odd games cannot separate a team''s own home edge from noise, so one league value is used.';
comment on column fantasy_team_inputs.srs is
  'Simple Rating System: schedule-adjusted points margin per game against an average team on a neutral field. Stands in for ESPN FPI, which is on the same points scale and has no source we can read automatically or point in time.';

create index fantasy_team_inputs_lookup_idx on fantasy_team_inputs (season, as_of_week);

alter table fantasy_projections enable row level security;
alter table fantasy_lineups enable row level security;
alter table fantasy_team_inputs enable row level security;

create policy fantasy_projections_public_read on fantasy_projections
  for select to anon, authenticated using (true);
create policy fantasy_lineups_public_read on fantasy_lineups
  for select to anon, authenticated using (true);
create policy fantasy_team_inputs_public_read on fantasy_team_inputs
  for select to anon, authenticated using (true);
