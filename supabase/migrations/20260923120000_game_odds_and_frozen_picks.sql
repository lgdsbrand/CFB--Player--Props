-- =============================================================================
-- 0074 — Game odds history, and picks that cannot move after kickoff
-- =============================================================================
-- The first migration of the game model (CLAUDE.md §11, phase G1). Two tables,
-- neither read by any model:
--
--   game_odds   what every book priced each game at, as it moved
--   game_picks  what the model said, stamped before kickoff and then frozen
--
-- -----------------------------------------------------------------------------
-- WHY game_odds IS NOT game_lines
-- -----------------------------------------------------------------------------
-- `game_lines` (0030) is CFBD's view: three providers, one row per game per
-- provider, overwritten in place, with an "open" column. It has no Pinnacle, no
-- exchanges and no history between open and now. This table is the Odds API's
-- bulk endpoint across the `us`, `us_ex` and `eu` regions — 27 books on a 71-game
-- slate when measured 2026-09-23 — kept as HISTORY, because line movement and
-- the closing price are the two things the client's screen and any honest
-- grading need, and neither survives an overwrite.
--
-- APPEND-ONLY, BUT ONLY ON CHANGE. The job compares each quote against the
-- book's latest row for the same (game, book, period, market) and writes only
-- when the line or a price moved. A pull where nothing moved writes nothing.
-- That is what keeps ~7 pulls a week across 27 books from costing storage the
-- way a snapshot-per-pull would, and it loses nothing: an unchanged price has
-- the same meaning at every capture between two changes.
--
-- SO `captured_at` MEANS "FIRST SEEN AT THIS PRICE", and the first row per key
-- is the first price WE saw, not the book's opening line. The screen labels it
-- that way. CFBD's `game_lines.spread_open` is the real open where it exists.
--
-- -----------------------------------------------------------------------------
-- HOME AND AWAY ARE OURS, NEVER THE PROVIDER'S
-- -----------------------------------------------------------------------------
-- The event is matched to a game on the TEAM PAIR (ingest_odds.match_event_to_game),
-- which is deliberately indifferent to which side the provider calls home. At a
-- neutral site the two sources routinely disagree. So every price here is
-- assigned by resolving the OUTCOME's team name against `games.home_team_id`,
-- and the spread is stored from OUR home team's perspective, the same
-- convention `game_lines.spread` verified 228 of 228 against CFBD. Getting this
-- backwards is invisible — every number still renders, the favourite is just
-- silently the wrong team.
--
-- -----------------------------------------------------------------------------
-- PERIODS
-- -----------------------------------------------------------------------------
-- `full` is all the bulk endpoint serves. `h1` and `q1` are here so 1H/1Q
-- markets (per-event, billed like player props — CLAUDE.md §11) land in the same
-- table when the paid key is topped up, rather than forcing a second migration
-- that every reader then has to union.
-- =============================================================================

create table game_odds (
  id               bigint generated always as identity primary key,
  game_id          bigint not null references games(id) on delete cascade,
  sportsbook_id    bigint not null references sportsbooks(id),
  period           text not null,
  market           text not null,

  -- spreads: OUR home team's handicap (negative = home favoured).
  -- totals:  the total. h2h: NULL.
  line             numeric(5, 1),

  home_price       integer,
  away_price       integer,
  over_price       integer,
  under_price      integer,

  -- The book's own timestamp for this market, when the provider sends one.
  -- Distinct from captured_at: a book can have moved an hour before we looked.
  book_updated_at  timestamptz,
  captured_at      timestamptz not null default now(),
  source_adapter   text not null,

  unique (game_id, sportsbook_id, period, market, captured_at),

  constraint game_odds_period_known check (period in ('full', 'h1', 'q1')),
  constraint game_odds_market_known check (market in ('h2h', 'spreads', 'totals')),
  -- Each market carries exactly the columns it means. A total with a home
  -- price, or a moneyline with a line, is a parsing bug and must not insert.
  constraint game_odds_shape check (
    case market
      when 'h2h' then line is null
        and over_price is null and under_price is null
        and (home_price is not null or away_price is not null)
      when 'spreads' then line is not null
        and over_price is null and under_price is null
        and (home_price is not null or away_price is not null)
      when 'totals' then line is not null
        and home_price is null and away_price is null
        and (over_price is not null or under_price is not null)
    end
  )
);

comment on table game_odds is
  'Game-level odds per book, as they moved: a row is written only when a book''s line or price changed since its previous row for the same (game, book, period, market). The first row per key is the first price WE saw, not the book''s opening line. DISPLAY AND GRADING ONLY — nothing in any model reads this table (CLAUDE.md §11).';

comment on column game_odds.line is
  'spreads: from OUR home team''s perspective (games.home_team_id), negative = home favoured — assigned from the outcome''s team name, never from the provider''s home/away label, which disagrees with ours at neutral sites. totals: the total. h2h: NULL.';

comment on column game_odds.captured_at is
  'When we first saw THIS price. A later capture at an unchanged price writes nothing, so the row stands for every capture until the next change.';

create index game_odds_latest_idx
  on game_odds (game_id, sportsbook_id, period, market, captured_at desc);

alter table game_odds enable row level security;

create policy game_odds_public_read
  on game_odds for select
  to anon, authenticated
  using (true);

-- -----------------------------------------------------------------------------
-- Latest and first-seen price per book
-- -----------------------------------------------------------------------------
-- One row per (game, book, period, market), carrying the current price and the
-- first one we saw, so the screen's "moved since" is one read, not two. The
-- ingest job reads the same view to decide whether a quote changed.
create view v_game_odds_current
with (security_invoker = true)
as
-- ONE pass over game_odds, not a self-join. A CTE referenced twice is an
-- optimisation fence in Postgres: the game_id filter would stop pushing
-- down and every read would window the whole table.
with ranked as (
  select
    o.*,
    row_number() over w_desc as newest,
    first_value(line)        over w_asc as first_line,
    first_value(home_price)  over w_asc as first_home_price,
    first_value(away_price)  over w_asc as first_away_price,
    first_value(over_price)  over w_asc as first_over_price,
    first_value(under_price) over w_asc as first_under_price,
    first_value(captured_at) over w_asc as first_captured_at
  from game_odds o
  window
    w_desc as (partition by game_id, sportsbook_id, period, market
               order by captured_at desc),
    w_asc  as (partition by game_id, sportsbook_id, period, market
               order by captured_at asc)
)
select
  r.game_id,
  r.sportsbook_id,
  sb.key          as sportsbook_key,
  sb.display_name as sportsbook_name,
  r.period,
  r.market,
  r.line,
  r.home_price,
  r.away_price,
  r.over_price,
  r.under_price,
  r.book_updated_at,
  r.captured_at,
  r.first_line,
  r.first_home_price,
  r.first_away_price,
  r.first_over_price,
  r.first_under_price,
  r.first_captured_at
from ranked r
join sportsbooks sb on sb.id = r.sportsbook_id
where r.newest = 1;

comment on view v_game_odds_current is
  'Per (game, book, period, market): the newest price and the first one we saw. first_* is first SEEN, not the book''s opening line. Filter by game_id — the window functions partition on it, so the filter pushes down.';

-- =============================================================================
-- game_picks — what the model said, frozen at kickoff
-- =============================================================================
-- A grading page built on picks that can be revised after the result is known
-- is not grading (CLAUDE.md §11). The rule is enforced HERE, by the database,
-- rather than promised by the job that writes picks: a trigger refuses any
-- insert, update or delete once the game has kicked off, and stamps `made_at`
-- from the server clock so a writer cannot backdate a pick.
--
-- Empty until the model exists (G3/G4). It ships now so the rule is in force
-- from the first pick ever written, not retrofitted onto a table that already
-- holds rows nobody can vouch for.
--
-- Picks are append-only BEFORE kickoff too: a revised pick is a new row, and
-- the grade uses the newest row made before kickoff. History of revisions is
-- itself worth seeing.
-- =============================================================================

create table game_picks (
  id              bigint generated always as identity primary key,
  game_id         bigint not null references games(id) on delete restrict,
  period          text not null,
  market          text not null,
  side            text not null,

  -- The number and price the pick was made AGAINST, and where they came from.
  line            numeric(5, 1),
  price           integer,
  sportsbook_id   bigint references sportsbooks(id),

  model_prob      numeric(6, 5) not null,
  model_version   text not null,
  made_at         timestamptz not null default now(),

  constraint game_picks_period_known check (period in ('full', 'h1', 'q1')),
  constraint game_picks_market_known check (market in ('h2h', 'spreads', 'totals')),
  constraint game_picks_side_fits_market check (
    case market
      when 'totals' then side in ('over', 'under')
      else side in ('home', 'away')
    end
  ),
  constraint game_picks_prob_range check (model_prob > 0 and model_prob < 1)
);

comment on table game_picks is
  'Model picks on games. FROZEN AT KICKOFF by trigger: no insert, update or delete once games.start_date has passed, and made_at is always the server clock. A revision before kickoff is a new row; grading uses the newest row made before kickoff.';

create index game_picks_game_idx on game_picks (game_id, period, market, made_at desc);

create or replace function game_picks_freeze()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  kickoff timestamptz;
  target  bigint := coalesce(new.game_id, old.game_id);
begin
  select start_date into kickoff from games where id = target;

  -- No kickoff time means no way to prove the pick came before the game.
  if kickoff is null then
    raise exception 'game_picks: game % has no start_date; a pick cannot be shown to precede it', target;
  end if;

  if now() >= kickoff then
    raise exception 'game_picks: game % kicked off at %; picks are frozen', target, kickoff;
  end if;

  -- A delete that passed the kickoff check needs nothing more.
  if tg_op = 'DELETE' then
    return old;
  end if;

  -- An update must not move a pick onto another game that is still open.
  if tg_op = 'UPDATE' and new.game_id <> old.game_id then
    raise exception 'game_picks: a pick cannot be moved to another game';
  end if;

  new.made_at := now();
  return new;
end;
$$;

comment on function game_picks_freeze() is
  'Refuses any write to game_picks at or after the game''s kickoff, and overwrites made_at with the server clock. The whole grading claim rests on this; do not relax it to make a backfill convenient — a backfilled pick is a backtest, and belongs in the backtest report, not this table.';

create trigger game_picks_frozen_at_kickoff
  before insert or update or delete on game_picks
  for each row execute function game_picks_freeze();

alter table game_picks enable row level security;

create policy game_picks_public_read
  on game_picks for select
  to anon, authenticated
  using (true);
