-- The game simulator in production (phase S2, 2026-10-20).
--
-- The client asked for a game simulation like the ones on social media, with
-- each quarter's and half's score beside the odds (2026-10-20). Phase S1 built
-- the engine (worker/core/play_sim.py) and backtested it against the live
-- model (docs/nfl-sim-backtest.html): it tied the live model on the full game,
-- so the live model keeps the full-game win %, margin and total. These tables
-- hold what the simulator supplies instead: one simulated game for the field
-- animation, each quarter's and half's points, and the whole distribution of
-- each so S3 can price any book line. No price is read (CLAUDE.md §11).
--
--   sim_engines  The fitted engine, saved once a week by `fit_sim_engine`
--                (fitting takes two minutes; the live poller runs every two),
--                one row per sport. Worker-only: no public policy.
--   game_sims    One row per game per stage, written by `run_game_sims`:
--                'pregame' before kickoff (FROZEN AT KICKOFF by trigger, as
--                game_picks is, so a quarter call graded later is the one
--                made before the game), and 'live', overwritten every minute
--                while the game is on.
--
-- No sport column on game_sims (CLAUDE.md §3: sport comes through game_id).

create table sim_engines (
  sport       text primary key check (sport in ('nfl', 'cfb')),
  version     text not null,
  meta        jsonb not null,
  payload     bytea not null,
  fitted_at   timestamptz not null default now()
);

comment on table sim_engines is
  'The fitted game simulator per sport (migration 0099): a compressed pickle of worker/core/sim_runtime.Engine, written weekly by fit_sim_engine and loaded by run_game_sims. Worker-only.';
comment on column sim_engines.meta is
  'What it was fitted on: seasons, kickoff pool, quarter adjustments, strength curve, sigma, library versions.';

alter table sim_engines enable row level security;

create table game_sims (
  game_id         bigint not null references games(id) on delete cascade,
  stage           text not null check (stage in ('pregame', 'live')),
  sims            integer not null check (sims > 0),
  period          smallint,
  clock           text,
  home_points     smallint not null default 0,
  away_points     smallint not null default 0,
  prior_home      numeric(5, 2) not null,
  prior_away      numeric(5, 2) not null,
  summary         jsonb not null,
  replay          jsonb not null,
  engine_version  text not null,
  made_at         timestamptz not null default now(),
  primary key (game_id, stage)
);

comment on table game_sims is
  'The game simulator per game (migration 0099): ''pregame'' (frozen at kickoff) and ''live'' (overwritten each poll). summary holds each quarter, half and the game: mean points per side, 80% ranges, the chance the home side leads and of a tie, and the whole total and margin distributions as counts per value ({"lo": n, "n": [...]}) so any line can be priced. replay is one simulated game play by play for the field animation. No full-game win %: the live model owns that (docs/nfl-sim-backtest.html).';
comment on column game_sims.prior_home is
  'The projected points the team strengths were set from (NFL: the client''s fantasy model, the shown version); prior_away likewise.';
comment on column game_sims.period is
  'For a live row, the scoreboard moment it started from (period, clock, score); NULL period on a pregame row.';

create or replace function game_sims_pregame_freeze()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  kickoff timestamptz;
  target  bigint := coalesce(new.game_id, old.game_id);
  st      text := coalesce(new.stage, old.stage);
begin
  if st <> 'pregame' then
    if tg_op = 'DELETE' then
      return old;
    end if;
    new.made_at := now();
    return new;
  end if;

  select start_date into kickoff from games where id = target;
  if kickoff is null then
    raise exception 'game_sims: game % has no start_date; a pregame run cannot be shown to precede it', target;
  end if;
  if now() >= kickoff then
    raise exception 'game_sims: game % kicked off at %; its pregame run is frozen', target, kickoff;
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  if tg_op = 'UPDATE' and (new.game_id <> old.game_id or new.stage <> old.stage) then
    raise exception 'game_sims: a run cannot be moved to another game or stage';
  end if;
  new.made_at := now();
  return new;
end;
$$;

comment on function game_sims_pregame_freeze() is
  'Refuses any write to a pregame game_sims row at or after kickoff, and stamps made_at with the server clock. Live rows are written freely.';

create trigger game_sims_pregame_frozen_at_kickoff
  before insert or update or delete on game_sims
  for each row execute function game_sims_pregame_freeze();

alter table game_sims enable row level security;

create policy game_sims_public_read on game_sims
  for select to anon, authenticated using (true);
