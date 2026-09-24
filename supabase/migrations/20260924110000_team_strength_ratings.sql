-- =============================================================================
-- 0077 — Team strength, point in time, for the game model
-- =============================================================================
-- G2 of the game model (CLAUDE.md §11). One row per team per week: how good
-- its offense and defense were, OPPONENT-ADJUSTED, using only games played
-- BEFORE that week. The game model's features are read from here and nowhere
-- else, so the lookahead rule of CLAUDE.md §4 is a property of this table
-- rather than something each model has to remember.
--
-- -----------------------------------------------------------------------------
-- WHAT A ROW MEANS
-- -----------------------------------------------------------------------------
-- `as_of_week = N` is fitted on completed games with week < N in the same
-- season. Every figure is on its natural scale and ADJUSTED: `off_*` is what
-- this offense would produce against an average defense, `def_*` what this
-- defense would allow to an average offense. Higher `def_*` is a WORSE
-- defense, the same orientation as `defense_position_ratings`.
--
-- The fit is the splits engine's additive model (core/splits.py
-- `_fit_additive`): value = league mean + offense effect + defense effect,
-- solved by alternating means over every game-side in the cutoff.
--
-- NOT SHRUNK. `games_included` is on every row, and pulling early-season
-- numbers toward last season or the league mean is a MODELLING decision the
-- game model makes and the backtest measures (G3). Baking one shrinkage into
-- the stored features would hide it from the thing that is supposed to test it.
--
-- -----------------------------------------------------------------------------
-- WHAT GOES IN
-- -----------------------------------------------------------------------------
--   ppa        CFBD's predicted points added per scrimmage play (EPA-like)
--   success    share of scrimmage plays gaining 50% / 70% / 100% of the
--              distance on 1st / 2nd / 3rd-4th down
--   points     points scored per game (the final score, all phases)
--   plays      scrimmage plays per game — pace
--
-- Scrimmage plays only: rushes, passes, sacks, turnovers and their
-- touchdowns. Kickoffs, punts, field goals, penalties and period markers are
-- out. Efficiency (ppa, success) also drops GARBAGE TIME — score margin over
-- 38 in the 2nd quarter, 28 in the 3rd, 22 in the 4th — because a starter
-- resting at 45-10 says nothing about next week. Points and plays keep every
-- snap: they are the totals a book prices.
--
-- `plays` has no `sport` column (it inherits sport through `games`), and 2025
-- onwards it holds NFL plays too. The build joins `games.sport` on every read.
-- =============================================================================

create table team_strength_ratings (
  id              bigint generated always as identity primary key,
  team_id         bigint not null references teams(id) on delete cascade,
  season          smallint not null,
  as_of_week      smallint not null,
  games_included  smallint not null,

  off_ppa         numeric(7, 4),
  def_ppa         numeric(7, 4),
  off_success     numeric(6, 4),
  def_success     numeric(6, 4),
  off_points_pg   numeric(6, 2),
  def_points_pg   numeric(6, 2),
  off_plays_pg    numeric(6, 2),
  def_plays_pg    numeric(6, 2),

  -- The league means the effects were fitted around, repeated per row so a
  -- reader can turn any figure back into "above or below average".
  league_ppa      numeric(7, 4),
  league_success  numeric(6, 4),
  league_points   numeric(6, 2),
  league_plays    numeric(6, 2),

  created_at      timestamptz not null default now(),

  unique (team_id, season, as_of_week),
  constraint team_strength_week_positive check (as_of_week >= 1),
  constraint team_strength_games_nonneg check (games_included >= 0)
);

comment on table team_strength_ratings is
  'Opponent-adjusted team offense and defense per week, POINT IN TIME: as_of_week = N uses only completed games with week < N in the same season (CLAUDE.md §4). off_* = against an average defense; def_* = allowed to an average offense (higher is worse). Unshrunk: games_included is on every row and shrinkage is the game model''s decision (CLAUDE.md §11).';

comment on column team_strength_ratings.as_of_week is
  'The week these numbers are FOR. Fitted on games with week < as_of_week only. A game in week N must be predicted from the as_of_week = N row, never a later one.';

create index team_strength_lookup_idx
  on team_strength_ratings (season, as_of_week, team_id);

alter table team_strength_ratings enable row level security;

create policy team_strength_ratings_public_read
  on team_strength_ratings for select
  to anon, authenticated
  using (true);
