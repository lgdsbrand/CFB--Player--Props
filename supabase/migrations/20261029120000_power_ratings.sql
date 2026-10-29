-- Power ratings and game score (client, 2026-10-29). Display only.
--
-- team_power_ratings: points per game better than an average team on a
-- neutral field, POINT IN TIME: the row for as_of_week = N is fitted on games
-- before week N only (CLAUDE.md §4), the postseason row on the whole regular
-- season (as_of_week = last regular week + 1). power_rank is among the
-- sport's ranked league (FBS in college); a team outside it is rated, so its
-- games count, and has no rank. Built by worker/core/power.py.
--
-- game_power: each game's two ranks and ratings at its week, the home side's
-- chance from those ratings and home field, and the game score (0-100: 60%
-- how good both teams are by rank, 40% how close the game is). Nothing here
-- feeds the game model, the props model or a pick.

create table team_power_ratings (
  team_id         bigint not null references teams(id) on delete cascade,
  season          smallint not null,
  as_of_week      smallint not null,
  rating          numeric(6, 2) not null,
  power_rank      smallint,
  games_included  smallint not null,
  made_at         timestamptz not null default now(),

  primary key (team_id, season, as_of_week),
  constraint team_power_week_positive check (as_of_week >= 1),
  constraint team_power_rank_positive check (power_rank is null or power_rank >= 1)
);

create index team_power_ratings_season_week on team_power_ratings (season, as_of_week);

create table game_power (
  game_id      bigint primary key references games(id) on delete cascade,
  as_of_week   smallint not null,
  home_rank    smallint,
  away_rank    smallint,
  home_rating  numeric(6, 2),
  away_rating  numeric(6, 2),
  p_home_win   numeric(6, 5) not null,
  game_score   smallint not null,
  made_at      timestamptz not null default now(),

  constraint game_power_prob_range check (p_home_win > 0 and p_home_win < 1),
  constraint game_power_score_range check (game_score between 0 and 100)
);

alter table team_power_ratings enable row level security;
alter table game_power enable row level security;

create policy team_power_ratings_public_read
  on team_power_ratings for select
  to anon, authenticated
  using (true);

create policy game_power_public_read
  on game_power for select
  to anon, authenticated
  using (true);
