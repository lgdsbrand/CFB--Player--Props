-- =============================================================================
-- 0002 — Reference / dimension tables
-- =============================================================================
-- Conferences, teams, season-scoped team membership, venues, games, players,
-- season-scoped roster membership.
--
-- Two structural decisions here matter for correctness downstream:
--   1. Conference membership is season-scoped (team_seasons), not a column on
--      teams. Realignment means a team's conference in 2022 is not its
--      conference in 2026, and a historical board must filter on the conference
--      that was true at the time.
--   2. Roster membership is season-scoped (player_team_seasons), not a column on
--      players. Transfer portal movement is the norm in college football, and
--      CLAUDE.md §6 requires knowing *which school* prior-year production
--      happened at in order to down-weight it correctly.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- conferences
-- -----------------------------------------------------------------------------
create table conferences (
  id                bigint generated always as identity primary key,
  cfbd_id           integer unique,
  name              text not null unique,
  abbreviation      text,
  short_name        text,
  classification    team_classification,
  is_displayed      boolean not null default false,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

comment on table conferences is
  'Conference reference data. Membership by season lives in team_seasons.';

comment on column conferences.is_displayed is
  'DISPLAY FILTER ONLY (CLAUDE.md §4). Controls which conferences appear in the UI conference filter. Ingest always covers every FBS team regardless of this flag — cross-conference games cannot be opponent-adjusted if one side is missing. Never reference this column in an ingest or feature query.';

create trigger conferences_set_updated_at
  before update on conferences
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- teams
-- -----------------------------------------------------------------------------
create table teams (
  id                bigint generated always as identity primary key,
  cfbd_id           integer not null unique,
  school            text not null,
  mascot            text,
  abbreviation      text,
  alt_name          text,
  color             text,
  alt_color         text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

comment on table teams is
  'Stable team identity. Conference, division and classification are season-scoped and live in team_seasons.';

comment on column teams.color is
  'Team hex color driving the placeholder team chip (abbreviation on team colors). No official logo or wordmark assets are stored or referenced anywhere in this schema — they are trademarked and get added later at the client''s direction (CLAUDE.md §7, §10).';

create trigger teams_set_updated_at
  before update on teams
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- team_seasons
-- -----------------------------------------------------------------------------
create table team_seasons (
  id                bigint generated always as identity primary key,
  team_id           bigint not null references teams(id) on delete cascade,
  season            smallint not null,
  conference_id     bigint references conferences(id),
  division          text,
  classification    team_classification,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (team_id, season)
);

comment on table team_seasons is
  'Team attributes that change year to year. Realignment makes conference a season-scoped fact; the Pac-12 remnant is handled naturally here rather than by assuming a fixed set of conferences (CLAUDE.md §4).';

create index team_seasons_season_conference_idx
  on team_seasons (season, conference_id);

create trigger team_seasons_set_updated_at
  before update on team_seasons
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- venues
-- -----------------------------------------------------------------------------
create table venues (
  id                bigint generated always as identity primary key,
  cfbd_id           integer unique,
  name              text not null,
  city              text,
  state             text,
  zip               text,
  country_code      text,
  latitude          numeric(9, 6),
  longitude         numeric(9, 6),
  elevation_m       numeric,
  capacity          integer,
  is_dome           boolean not null default false,
  has_grass         boolean,
  timezone          text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

comment on column venues.latitude is
  'Used for the Open-Meteo weather fallback on outdoor venues when CFBD has no weather for the game (CLAUDE.md §4).';

comment on column venues.is_dome is
  'Short-circuits weather lookups and zeroes weather adjustments in the model.';

create trigger venues_set_updated_at
  before update on venues
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- games
-- -----------------------------------------------------------------------------
create table games (
  id                bigint generated always as identity primary key,
  cfbd_id           bigint not null unique,
  season            smallint not null,
  week              smallint not null,
  season_type       season_type not null default 'regular',
  start_date        timestamptz,
  start_time_tbd    boolean not null default false,
  neutral_site      boolean not null default false,
  conference_game   boolean,
  venue_id          bigint references venues(id),
  home_team_id      bigint not null references teams(id),
  away_team_id      bigint not null references teams(id),
  home_points       smallint,
  away_points       smallint,
  completed         boolean not null default false,
  attendance        integer,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint games_teams_differ check (home_team_id <> away_team_id),
  constraint games_week_positive check (week >= 1)
);

comment on column games.week is
  'The week ordinal used as the time axis for every point-in-time cutoff in this schema. A feature computed with as_of_week = N may only read games with week < N in the same season.';

comment on column games.neutral_site is
  'Neutral sites are common in college football, which is why home/away splits are a secondary control here rather than a first-class filter (CLAUDE.md §7).';

create index games_season_week_idx on games (season, week);
create index games_home_team_idx on games (home_team_id, season);
create index games_away_team_idx on games (away_team_id, season);
create index games_start_date_idx on games (start_date);

create trigger games_set_updated_at
  before update on games
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- players
-- -----------------------------------------------------------------------------
create table players (
  id                bigint generated always as identity primary key,
  cfbd_athlete_id   bigint unique,
  name              text not null,
  first_name        text,
  last_name         text,
  position_group    position_group not null default 'OTHER',
  position_raw      text,
  height_inches     smallint,
  weight_lbs        smallint,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

comment on table players is
  'Stable player identity across schools. Team membership is season-scoped in player_team_seasons.';

comment on column players.position_group is
  'Most recent known position. The authoritative position for any given season is on player_team_seasons, and the position at the time of a play is denormalized onto play_player_stats.';

comment on column players.position_raw is
  'Unmapped source position string (CFBD uses FB, ATH, etc.). Kept so the normalization to position_group can be revised without re-ingesting.';

create index players_name_idx on players (lower(name));

create trigger players_set_updated_at
  before update on players
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- player_team_seasons
-- -----------------------------------------------------------------------------
create table player_team_seasons (
  id                bigint generated always as identity primary key,
  player_id         bigint not null references players(id) on delete cascade,
  team_id           bigint not null references teams(id) on delete cascade,
  season            smallint not null,
  position_group    position_group not null,
  position_raw      text,
  jersey            smallint,
  class_year        text,
  height_inches     smallint,
  weight_lbs        smallint,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (player_id, team_id, season)
);

comment on table player_team_seasons is
  'Roster membership, scoped to (player, team, season). Transfer portal and NIL movement mean prior-season production frequently happened at another school; the college weighting rules in CLAUDE.md §6 need this to down-weight correctly. The unique key intentionally permits one player on two teams within a season (mid-year transfer).';

create index player_team_seasons_team_season_idx
  on player_team_seasons (team_id, season, position_group);

create trigger player_team_seasons_set_updated_at
  before update on player_team_seasons
  for each row execute function set_updated_at();
