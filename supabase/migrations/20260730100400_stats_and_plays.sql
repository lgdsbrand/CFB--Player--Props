-- =============================================================================
-- 0004 — Player box scores, play-by-play, and play-level attribution
-- =============================================================================
-- player_game_stats is the single home for ACTUALS. Every grade, hit rate and
-- backtest outcome resolves against a column in this table and nowhere else.
--
-- plays + play_player_stats are the substrate for the position-split engine
-- (CLAUDE.md §5): no provider serves "what this defense allows to RBs", so it is
-- attributed from play level here and aggregated in 0005.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- player_game_stats — actuals, one row per player per game
-- -----------------------------------------------------------------------------
create table player_game_stats (
  id                  bigint generated always as identity primary key,
  player_id           bigint not null references players(id) on delete cascade,
  game_id             bigint not null references games(id) on delete cascade,
  team_id             bigint not null references teams(id),
  opponent_team_id    bigint not null references teams(id),
  season              smallint not null,
  week                smallint not null,
  position_group      position_group not null,
  is_home             boolean not null,

  -- passing
  pass_attempts       smallint,
  pass_completions    smallint,
  pass_yards          smallint,
  pass_tds            smallint,
  interceptions       smallint,

  -- rushing
  rush_attempts       smallint,
  rush_yards          smallint,
  rush_tds            smallint,

  -- receiving
  targets             smallint,
  receptions          smallint,
  rec_yards           smallint,
  rec_tds             smallint,

  -- misc
  return_tds          smallint,
  fumbles_lost        smallint,
  snaps               smallint,
  started             boolean,

  -- Derived outcome for the anytime-TD market. Generated so the label can never
  -- drift from the box score it is computed from.
  offensive_tds       smallint generated always as (
                        coalesce(rush_tds, 0) + coalesce(rec_tds, 0)
                      ) stored,

  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (player_id, game_id),
  constraint player_game_stats_teams_differ check (team_id <> opponent_team_id)
);

comment on table player_game_stats is
  'Actual player production. The ONLY table holding realised outcomes — features never read actuals for week >= their as_of_week, so keeping actuals in exactly one place makes that rule auditable.';

comment on column player_game_stats.offensive_tds is
  'Rushing + receiving touchdowns. This is the outcome the anytime_td market grades against (over 0.5 = scored). Passing TDs are excluded because a QB throwing a TD is not an anytime scorer. Return TDs are excluded from this column and tracked separately in return_tds: the model in CLAUDE.md §6 is built from offensive goal-line usage, so returns are not modelled — reconcile against a specific book''s rules before grading if that book pays returns.';

comment on column player_game_stats.targets is
  'Not reliably served by CFBD box scores; populated by aggregating play_player_stats during ingest.';

comment on column player_game_stats.season is
  'Denormalized from games for aggregation without a join. Ingest must keep it consistent with games.season.';

create index player_game_stats_player_season_idx
  on player_game_stats (player_id, season, week);
create index player_game_stats_game_idx
  on player_game_stats (game_id);
create index player_game_stats_opponent_idx
  on player_game_stats (opponent_team_id, season, week, position_group);

create trigger player_game_stats_set_updated_at
  before update on player_game_stats
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- plays
-- -----------------------------------------------------------------------------
create table plays (
  id                  bigint generated always as identity primary key,
  cfbd_id             bigint unique,
  game_id             bigint not null references games(id) on delete cascade,
  season              smallint not null,
  week                smallint not null,
  offense_team_id     bigint references teams(id),
  defense_team_id     bigint references teams(id),
  period              smallint,
  clock_seconds       integer,
  down                smallint,
  distance            smallint,
  yards_to_goal       smallint,
  yards_gained        smallint,
  play_type           text,
  play_text           text,
  scoring             boolean not null default false,
  ppa                 numeric,
  offense_score       smallint,
  defense_score       smallint,
  created_at          timestamptz not null default now()
);

comment on table plays is
  'Play-by-play, trimmed to the fields the position-split engine and the goal-line usage component of the anytime-TD model actually consume.';

comment on column plays.yards_to_goal is
  'Distance to the opponent end zone. Drives the goal-line usage term of the anytime-TD model (CLAUDE.md §6): reaching scoring situations is estimated from carries and targets inside a yards_to_goal threshold.';

create index plays_game_idx on plays (game_id);
create index plays_defense_idx on plays (defense_team_id, season, week);
create index plays_season_week_idx on plays (season, week);
create index plays_goalline_idx on plays (offense_team_id, season, week)
  where yards_to_goal <= 10;

-- -----------------------------------------------------------------------------
-- play_player_stats — per-play attribution to a player
-- -----------------------------------------------------------------------------
create table play_player_stats (
  id                  bigint generated always as identity primary key,
  play_id             bigint not null references plays(id) on delete cascade,
  game_id             bigint not null references games(id) on delete cascade,
  player_id           bigint not null references players(id) on delete cascade,
  team_id             bigint not null references teams(id),
  opponent_team_id    bigint not null references teams(id),
  season              smallint not null,
  week                smallint not null,
  position_group      position_group not null,
  stat_type           text not null,
  stat_value          numeric,
  created_at          timestamptz not null default now(),
  unique (play_id, player_id, stat_type)
);

comment on table play_player_stats is
  'Attribution of each play to the players involved — the raw material for the position-split engine (CLAUDE.md §5). High volume: roughly 1M rows per season across all FBS, so the backfill season count is a real storage decision, not a free parameter.';

comment on column play_player_stats.position_group is
  'Denormalized from player_team_seasons at ingest time, for two reasons. (1) The split aggregation would otherwise join a multi-million row table on every run. (2) It pins the position as of the season of the play, so a player who changes position later does not retroactively rewrite historical defensive splits.';

comment on column play_player_stats.opponent_team_id is
  'The DEFENSE this player faced on this play. Position splits group by this column — it is what makes "rush yards allowed to RBs" a single-pass aggregation.';

create index play_player_stats_defense_idx
  on play_player_stats (opponent_team_id, season, week, position_group);
create index play_player_stats_player_idx
  on play_player_stats (player_id, season, week);
create index play_player_stats_game_idx
  on play_player_stats (game_id);
