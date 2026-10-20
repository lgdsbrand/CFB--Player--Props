-- The live model's projections (live model, phase L2, 2026-10-20).
--
-- The client asked for "a mid game model reran without pulling more odds
-- credits" (2026-10-19). Every two minutes while a game is on, the poller
-- (`poll_live_scores`) turns the scoreboard in `live_scores` and the game's
-- pregame projection into a projected final score, a win % and 80% ranges
-- (worker/core/live_model.py, backtested in docs/live-model-backtest.html).
-- No price is read anywhere (CLAUDE.md §11).
--
-- Two tables, no sport column (CLAUDE.md §3: sport comes through game_id):
--
--   live_projections         ONE ROW PER GAME, overwritten each poll: what
--                            the game page and cards show now. Its last
--                            write after the final carries the final score.
--   live_projection_history  One row per distinct moment (clock and score),
--                            append-only: the win % chart over the game, and
--                            the record of what the model said when, for
--                            grading. A poll while the clock is stopped
--                            writes nothing new.
--
-- live_scores now holds NFL games too (ESPN's scoreboard,
-- adapters/espn/scoreboard.py), in the same shape as college.

create table live_projections (
  game_id         bigint primary key references games(id) on delete cascade,
  status          text not null check (status in ('in_progress', 'completed')),
  period          smallint,
  clock           text,
  elapsed         integer not null check (elapsed >= 0),
  home_points     smallint not null,
  away_points     smallint not null,
  possession      text check (possession in ('home', 'away')),
  down            smallint check (down between 1 and 4),
  yards_to_goal   smallint check (yards_to_goal between 1 and 99),
  p_home_win      numeric(6, 5) not null check (p_home_win between 0 and 1),
  margin_mean     numeric(5, 1) not null,
  margin_lo       numeric(5, 1) not null,
  margin_hi       numeric(5, 1) not null,
  total_mean      numeric(5, 1) not null,
  total_lo        numeric(5, 1) not null,
  total_hi        numeric(5, 1) not null,
  prior_margin    numeric(5, 1) not null,
  prior_total     numeric(5, 1) not null,
  prior_source    text not null check (prior_source in ('game_model', 'fantasy')),
  model           text not null,
  updated_at      timestamptz not null default now()
);

comment on table live_projections is
  'The live model per game in progress (migration 0098), overwritten every poll: projected FINAL margin and total (home-oriented) with 80% ranges and the home win %. From the scoreboard (live_scores) and our own pregame projection; no odds. Backtested in docs/live-model-backtest.html.';
comment on column live_projections.elapsed is
  'Regulation seconds elapsed (0-3600) when projected; 3600 and over is overtime or final, where the model makes no claim beyond the score.';
comment on column live_projections.margin_lo is
  'Lower end of the 80% range for the final home margin (a Student t fitted per moment of the game; held 77-82% of finals in the backtest). margin_hi the upper end; total_lo/total_hi likewise for the total.';
comment on column live_projections.prior_source is
  '''game_model'' = the college game model''s pregame projection (game_projections); ''fantasy'' = the client''s NFL fantasy model (fantasy_game_projections, the shown version).';
comment on column live_projections.model is
  'Which fitted model made it: the candidate and the seasons it was fitted on, e.g. ''A 2023-2026'' (worker/core/live_params/<sport>.json).';

create table live_projection_history (
  id              bigint generated always as identity primary key,
  game_id         bigint not null references games(id) on delete cascade,
  elapsed         integer not null,
  period          smallint,
  clock           text,
  home_points     smallint not null,
  away_points     smallint not null,
  p_home_win      numeric(6, 5) not null,
  margin_mean     numeric(5, 1) not null,
  total_mean      numeric(5, 1) not null,
  made_at         timestamptz not null default now(),
  unique (game_id, elapsed, home_points, away_points)
);

comment on table live_projection_history is
  'Every distinct moment the live model projected (migration 0098), append-only: one row per game per (clock, score). For the win % chart and for grading what the model said when.';

create index live_projection_history_game_idx on live_projection_history (game_id, elapsed);

alter table live_projections enable row level security;
alter table live_projection_history enable row level security;

create policy live_projections_public_read on live_projections
  for select to anon, authenticated using (true);
create policy live_projection_history_public_read on live_projection_history
  for select to anon, authenticated using (true);

comment on table live_scores is
  'What the scoreboard says about each game in or near its window, overwritten every poll (migration 0088): college from CFBD''s scoreboard, NFL from ESPN''s (since migration 0098). Not the score of record: `games` keeps that, from the daily results ingest.';
