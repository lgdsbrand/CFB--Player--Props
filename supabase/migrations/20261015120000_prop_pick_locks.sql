-- Prop picks locked at kickoff, and their results (2026-10-15).
--
-- The client asked for a record of the player-prop calls. `picks` cannot be
-- that record: `run_projections` deletes and rewrites the whole week every
-- day, games already played included, so a pick graded on Wednesday night is
-- gone by Thursday's run and could come back changed. A record built on it
-- could be revised after the result is known, which is not a record.
--
-- So the board's call is COPIED here before kickoff, by `lock_prop_picks`,
-- chained hourly after each sport's props odds capture: for every game
-- starting inside its window, each player-market's headline row on
-- `v_board_rows` (the book the board shows, by sportsbooks.priority) as the
-- board showed it. A later run before kickoff refreshes the row, so what is
-- kept is the board's last state before the game. From kickoff the trigger
-- below refuses every write, as 0074's does for game_picks.
--
-- WHAT IS LOCKED: picks against a real book's two-way line on markets that
-- publish a call. Not anytime TD (books price only "yes", so there is no
-- de-vigged probability and no edge: see the board's is_binary), not
-- first-quarter markets (publishes_call is false: no call to grade), not the
-- synthetic DEV book.
--
-- No foreign keys to projections or picks: those rows are deleted every day,
-- and this table exists to outlive them.

create table prop_pick_locks (
  id                bigint generated always as identity primary key,
  game_id           bigint not null references games(id) on delete restrict,
  player_id         bigint not null references players(id) on delete restrict,
  team_id           bigint not null references teams(id),
  opponent_team_id  bigint not null references teams(id),
  market_key        text not null references markets(key),
  season            smallint not null,
  week              smallint not null,

  -- The line and two-way price the call was made against, and whose they were.
  sportsbook_id     bigint not null references sportsbooks(id),
  line              numeric(6, 2) not null,
  over_price        integer,
  under_price       integer,

  side              bet_side not null,
  -- The published (calibrated, CLAUDE.md §6 2026-10-08) probability of the
  -- over, the de-vigged book probability of the over, and the edge on the side
  -- taken: picks' own columns, copied, so this table and the board agree.
  model_prob_over   numeric not null,
  book_prob_over    numeric,
  edge              numeric,
  projected_median  numeric,

  made_at           timestamptz not null default now(),

  constraint prop_pick_locks_one_per_player_market unique (game_id, player_id, market_key),
  constraint prop_pick_locks_prob_range
    check (model_prob_over >= 0 and model_prob_over <= 1),
  constraint prop_pick_locks_side_matches_probability check (
    (side = 'over' and model_prob_over >= 0.5)
    or (side = 'under' and model_prob_over < 0.5)
  )
);

comment on table prop_pick_locks is
  'The board''s player-prop calls, copied before kickoff by lock_prop_picks and FROZEN AT KICKOFF by trigger: no insert, update or delete once games.start_date has passed, and made_at is always the server clock. One row per game, player and market: the board''s headline book. Real two-way book lines only; no anytime TD, no first-quarter markets. Graded by v_prop_pick_results. Records from 2026-10-16.';

create index prop_pick_locks_season_week_idx on prop_pick_locks (season, week);

create or replace function prop_pick_locks_freeze()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  kickoff timestamptz;
  target  bigint := coalesce(new.game_id, old.game_id);
begin
  select start_date into kickoff from games where id = target;

  if kickoff is null then
    raise exception 'prop_pick_locks: game % has no start_date; a pick cannot be shown to precede it', target;
  end if;

  if now() >= kickoff then
    raise exception 'prop_pick_locks: game % kicked off at %; picks are frozen', target, kickoff;
  end if;

  if tg_op = 'DELETE' then
    return old;
  end if;

  if tg_op = 'UPDATE' and new.game_id <> old.game_id then
    raise exception 'prop_pick_locks: a pick cannot be moved to another game';
  end if;

  new.made_at := now();
  return new;
end;
$$;

comment on function prop_pick_locks_freeze() is
  'Refuses any write to prop_pick_locks at or after the game''s kickoff, and overwrites made_at with the server clock. The props record rests on this, as the game record rests on game_picks_freeze; do not relax it for a backfill. A backfilled pick is a backtest.';

create trigger prop_pick_locks_frozen_at_kickoff
  before insert or update or delete on prop_pick_locks
  for each row execute function prop_pick_locks_freeze();

-- The calls were already public on the board before kickoff; the copy is too.
alter table prop_pick_locks enable row level security;

create policy prop_pick_locks_public_read
  on prop_pick_locks for select
  to anon, authenticated
  using (true);

-- =============================================================================
-- v_prop_pick_results — each locked pick against the box score
-- =============================================================================
-- Official stats only (player_game_stats through v_player_game_log, loaded
-- each morning), never the live feed, so a result does not change once shown.
--
--   pending  the game is not final, or its box score has not loaded
--   void     the game is final and its box score loaded, but this player has
--            no value for the stat. Books void a player who does not play.
--            A box score also omits a player who played and recorded nothing
--            in that category; those are voided too rather than guessed.
--   push     the stat landed exactly on the line
--   win / loss
--
-- units: to one unit staked at the price of the side taken; NULL when that
-- side carried no price.
-- =============================================================================

create view v_prop_pick_results
with (security_invoker = true)
as
with scored as (
  select l.*,
         g.sport, g.start_date,
         (g.completed and exists (
            select 1 from player_game_stats s where s.game_id = l.game_id
         )) as final,
         case m.stat_column
           when 'pass_yards'       then gl.pass_yards
           when 'pass_tds'         then gl.pass_tds
           when 'pass_attempts'    then gl.pass_attempts
           when 'pass_completions' then gl.pass_completions
           when 'interceptions'    then gl.interceptions
           when 'rush_yards'       then gl.rush_yards
           when 'rush_attempts'    then gl.rush_attempts
           when 'rush_tds'         then gl.rush_tds
           when 'targets'          then gl.targets
           when 'receptions'       then gl.receptions
           when 'rec_yards'        then gl.rec_yards
           when 'rec_tds'          then gl.rec_tds
           when 'offensive_tds'    then gl.offensive_tds
         end::numeric as actual,
         case when l.side = 'over' then l.over_price else l.under_price end as price
    from prop_pick_locks l
    join games g   on g.id = l.game_id
    join markets m on m.key = l.market_key
    left join v_player_game_log gl
      on gl.player_id = l.player_id and gl.game_id = l.game_id
),
graded as (
  select s.*,
         case
           when not s.final then 'pending'
           when s.actual is null then 'void'
           when s.actual = s.line then 'push'
           when (s.actual > s.line) = (s.side = 'over') then 'win'
           else 'loss'
         end as result
    from scored s
)
select gr.id, gr.game_id, gr.player_id, gr.team_id, gr.opponent_team_id,
       gr.market_key, gr.season, gr.week, gr.sport, gr.start_date,
       gr.sportsbook_id, sb.key as sportsbook_key, sb.display_name as sportsbook_name,
       gr.line, gr.side, gr.price, gr.over_price, gr.under_price,
       gr.model_prob_over,
       case when gr.side = 'over' then gr.model_prob_over
            else 1 - gr.model_prob_over end as model_prob,
       gr.book_prob_over, gr.edge, gr.projected_median, gr.made_at,
       gr.actual, gr.result,
       case gr.result
         when 'win' then
           case when gr.price is null then null
                when gr.price > 0 then round(gr.price / 100.0, 4)
                else round(100.0 / (-gr.price), 4) end
         when 'loss' then -1
         when 'push' then 0
         when 'void' then 0
       end as units
  from graded gr
  left join sportsbooks sb on sb.id = gr.sportsbook_id;

comment on view v_prop_pick_results is
  'Each prop_pick_locks row graded against the official box score: pending, void (final, but no value for this player''s stat), push, win, loss; units to one unit at the price of the side taken. The props record.';
