-- =============================================================================
-- 0084 — Head coaches and their season-by-season records
-- =============================================================================
-- THE CLIENT'S ASK (2026-10-06): coach records on the game page. Item 4a of
-- that list, built within the current scope at the user's decision.
--
-- FROM CFBD (the sport-specific adapter, `ingest_coaches`), weekly:
--   `/coaches?year=S`       every head coach of the season, one call
--   `/coaches/seasons?id=C` each coach's every season, one call per coach
-- About 139 calls a week against a 30,000-a-month allowance.
--
-- STORED SEASON BY SEASON AND SUMMED ON THE SITE, NOT TAKEN AS A RUNNING
-- TOTAL. CFBD's profile and tenure endpoints carry career and at-school
-- totals, but whether those include the season in progress depends on when
-- CFBD last updated it (on 2026-10-07 they did not: Troy's coach showed 0
-- games for 2026 with four played). The page states records BEFORE this
-- season, summed from completed seasons, so the answer never depends on that
-- timing and a game is never counted twice beside the season-to-date record
-- the page already takes from `games`.
--
-- SPLITS ARE PER SEASON in CFBD's `recordSplits` (conference, home, away,
-- neutral, postseason) and NULL for a season it has not attributed — the
-- current one, and some older interim stints. NULL is "not recorded", not 0.
--
-- `team_id` is NULL for a season at a school outside our teams table (a
-- coach's Division II years, say); the school name is kept so the season still
-- counts toward a career and can be named.
-- =============================================================================

create table coaches (
  id            integer primary key,        -- CFBD's coach id
  first_name    text not null,
  last_name     text not null,
  updated_at    timestamptz not null default now()
);

comment on table coaches is
  'Head coaches, keyed by CFBD coach id. Written by ingest_coaches (migration 0084).';

create table coach_seasons (
  coach_id        integer not null references coaches(id) on delete cascade,
  season          integer not null,
  school          text not null,
  team_id         bigint references teams(id),
  games           smallint not null,
  wins            smallint not null,
  losses          smallint not null,
  ties            smallint not null default 0,
  conf_wins       smallint,
  conf_losses     smallint,
  home_wins       smallint,
  home_losses     smallint,
  away_wins       smallint,
  away_losses     smallint,
  neutral_wins    smallint,
  neutral_losses  smallint,
  post_wins       smallint,
  post_losses     smallint,
  updated_at      timestamptz not null default now(),
  primary key (coach_id, season, school)
);

comment on table coach_seasons is
  'One head-coaching season per row, as CFBD records it, with its record and the conference/home/away/neutral/postseason splits where CFBD has attributed them (NULL otherwise). The site sums COMPLETED seasons for a coach''s record before this season (migration 0084).';

create index coach_seasons_team_idx on coach_seasons (team_id, season);

create table team_coaches (
  team_id     bigint not null references teams(id) on delete cascade,
  season      integer not null,
  coach_id    integer not null references coaches(id) on delete cascade,
  hire_date   date,
  updated_at  timestamptz not null default now(),
  primary key (team_id, season, coach_id)
);

comment on table team_coaches is
  'Who coaches each team in a season, from CFBD /coaches?year=S. Usually one row per team-season; a mid-season change gives two, and the page shows the most recent hire.';

alter table coaches enable row level security;
alter table coach_seasons enable row level security;
alter table team_coaches enable row level security;

create policy coaches_public_read on coaches
  for select to anon, authenticated using (true);
create policy coach_seasons_public_read on coach_seasons
  for select to anon, authenticated using (true);
create policy team_coaches_public_read on team_coaches
  for select to anon, authenticated using (true);
