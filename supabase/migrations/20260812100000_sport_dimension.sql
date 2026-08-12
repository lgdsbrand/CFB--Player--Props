-- =============================================================================
-- 0035 -- the sport dimension
-- =============================================================================
-- The client chose ONE APP WITH A TOGGLE over two separate products, so NFL rows
-- will eventually live in these tables beside the college ones. This migration
-- adds the dimension that keeps them apart. It runs now, while every table holds
-- exactly one sport, because the same change during a live season means a data
-- migration rather than a default.
--
-- CLAUDE.md §3 asked for a clean sport seam so a second sport could reuse the
-- core. That seam was described there as a repo boundary -- copy the core into
-- an NFL repo. The client's answer makes it a RUNTIME dimension instead. The
-- discipline §3 asks for is unchanged and still worth keeping: the adapter layer
-- is what differs per sport, and nothing in the core should assume college.
--
-- SPLITTING LATER IS THE CHEAP DIRECTION, which is the reassurance the client
-- asked for. The two sports share no rows, so splitting is a filtered copy.
-- Merging two live databases would mean re-keying every primary key in one of
-- them, because both would have minted `id` 1..n independently.
--
-- ---------------------------------------------------------------------------
-- WHAT WAS ACTUALLY IN THE WAY -- measured, and not what was expected
-- ---------------------------------------------------------------------------
-- An audit of every UNIQUE constraint on the root tables suggested only
-- `conferences.name` blocked a second sport, since everything else was unique on
-- a `cfbd_id` that NFL rows would leave NULL, and Postgres permits many NULLs in
-- a unique index.
--
-- That was half right. `teams.cfbd_id` and `games.cfbd_id` are NOT NULL, so an
-- NFL row could not have left them NULL at all -- it would have had to invent a
-- CFBD id for a team CFBD has never heard of. Uniqueness was never the problem
-- on those two; nullability was, and a constraint audit does not show it.
--
-- The fix keeps today's guarantee exactly. Dropping NOT NULL outright would let
-- a college team through with no CFBD id, which is a real ingest bug; a
-- conditional CHECK says the same thing NOT NULL said, scoped to the sport the
-- column belongs to.
--
-- ---------------------------------------------------------------------------
-- WHY `default 'cfb'` IS SAFE HERE
-- ---------------------------------------------------------------------------
-- A default is normally the risky choice for a discriminator column: the NFL
-- adapter forgets to set it, and NFL teams are silently labelled college and
-- appear on the college board. That failure cannot happen here, and the reason
-- is the CHECK constraint above rather than care on anyone's part. An NFL team
-- has no `cfbd_id`, so a row that defaults to 'cfb' and omits it violates
-- `teams_cfb_requires_cfbd_id` and the insert is rejected on the spot. Same for
-- `games`. The default is what makes this an afternoon instead of a rewrite of
-- every upsert in the worker, and the constraint is what makes it safe.
--
-- `players` has no equivalent interlock -- `cfbd_athlete_id` is already nullable,
-- because CFBD genuinely omits it for some players. A mislabelled player is
-- reachable only through a team the constraint above would already have
-- rejected, so the exposure is a second-order one.
--
-- ---------------------------------------------------------------------------
-- WHAT DELIBERATELY DOES NOT GET A `sport` COLUMN
-- ---------------------------------------------------------------------------
-- * Everything downstream (projections, picks, player_game_stats, plays,
--   game_lines, team_poll_rankings, splits, ratings...). Each one reaches a
--   team, a game or a player by foreign key, so it inherits the sport and a
--   column would be a denormalisation that can disagree with its parent. The
--   single exception is `v_board_rows`, a view, where the join has already been
--   paid for and exposing it saves the app a second one.
--
-- * `venues`. A stadium is a physical place, not a sport. SoFi and MetLife host
--   both codes, and neutral-site college games are played in NFL stadiums every
--   season. Scoping venues by sport would duplicate them and split their weather
--   and elevation data in half.
--
-- * `markets`, `market_positions`, `sportsbooks`. Pass yards are pass yards. The
--   NFL market list is the same one, and a `sport` column that reads 'cfb' on
--   every row would be noise pretending to be a decision. Split it only if the
--   two lists actually diverge.
--
-- NFL rows will need their own external identifier, and this migration does not
-- invent one. `cfbd_id` is a CFBD identifier and stays that; the NFL adapter
-- adds its own column when it knows what its provider keys on. Designing a
-- generic `external_ref` now would be guessing at a provider not yet chosen.
--
-- NO NEW INDEXES. Every row is 'cfb' today, so an index on `sport` has no
-- selectivity and the planner would ignore it. When NFL data lands and the
-- column starts discriminating, `games_season_week_idx` and its siblings are the
-- ones to widen.
-- =============================================================================

create type sport as enum ('cfb', 'nfl');

comment on type sport is
  'Which sport a root row belongs to. The client chose one app with a toggle over two products (CLAUDE.md §3), so this is the dimension that keeps the two datasets from mixing on a shared board. Downstream tables inherit it through their foreign keys and deliberately do not carry a copy.';

-- -----------------------------------------------------------------------------
-- the column, on the four tables that are genuinely sport-scoped
-- -----------------------------------------------------------------------------
alter table conferences add column sport sport not null default 'cfb';
alter table teams       add column sport sport not null default 'cfb';
alter table games       add column sport sport not null default 'cfb';
alter table players     add column sport sport not null default 'cfb';

comment on column teams.sport is
  'Safe to leave defaulted on the college path. An NFL row that forgets to set this is rejected by teams_cfb_requires_cfbd_id rather than silently joining the college board -- see the migration header.';

comment on column games.sport is
  'Same interlock as teams.sport: a game defaulted to cfb must carry a cfbd_id, which an NFL row cannot.';

comment on column players.sport is
  'No cfbd_athlete_id interlock here -- that column is legitimately nullable, since CFBD omits it for some players. A mislabelled player is only reachable through a team whose own constraint would already have fired.';

-- -----------------------------------------------------------------------------
-- conferences: the one uniqueness collision
-- -----------------------------------------------------------------------------
-- The NFL's conferences are the AFC and the NFC, which collide with nothing
-- today. The realistic collision is a division name, or simply a second sport's
-- "National Conference" arriving one day. Scoping the key costs nothing now and
-- cannot be done cheaply once both sports are populated.
alter table conferences drop constraint conferences_name_key;

alter table conferences
  add constraint conferences_sport_name_key unique (sport, name);

comment on constraint conferences_sport_name_key on conferences is
  'Replaces the global unique on name. Conference names are only unique within a sport.';

-- -----------------------------------------------------------------------------
-- teams / games: nullability, not uniqueness
-- -----------------------------------------------------------------------------
-- `unique (cfbd_id)` stays as it is on both. It is the right constraint: it is a
-- CFBD identifier, only college rows have one, and many NULLs are permitted in a
-- unique index.
alter table teams alter column cfbd_id drop not null;

alter table teams
  add constraint teams_cfb_requires_cfbd_id
  check (sport <> 'cfb' or cfbd_id is not null);

comment on constraint teams_cfb_requires_cfbd_id on teams is
  'What NOT NULL used to say, scoped to the sport the column belongs to. A college team with no CFBD id is an ingest bug and still fails; an NFL team is simply not identified by CFBD.';

alter table games alter column cfbd_id drop not null;

alter table games
  add constraint games_cfb_requires_cfbd_id
  check (sport <> 'cfb' or cfbd_id is not null);

comment on constraint games_cfb_requires_cfbd_id on games is
  'See teams_cfb_requires_cfbd_id. Also the tripwire that stops an unlabelled NFL game from defaulting onto the college board.';
