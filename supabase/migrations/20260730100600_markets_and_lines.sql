-- =============================================================================
-- 0006 — Markets, sportsbooks, prop lines, de-vig math
-- =============================================================================
-- Every market — including anytime touchdown — is expressed as "probability that
-- the outcome exceeds a line". Anytime TD is not a special case with its own
-- code path: it is the market whose outcome column is offensive_tds and whose
-- line is fixed at 0.5, so "over 0.5" means "scored". That is what makes
-- CLAUDE.md §1's "every market speaks the same language: a probability per pick"
-- true in the schema rather than only in the UI.
--
-- The de-vig helpers live here because CLAUDE.md §6 requires ONE definition of
-- edge shared by the worker and the web read layer:
--     edge = model probability − de-vigged book implied probability
-- Defining it in SQL means it cannot be reimplemented differently in TypeScript.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- markets
-- -----------------------------------------------------------------------------
create table markets (
  key                   text primary key,
  display_name          text not null,
  short_label           text,
  emoji                 text,
  stat_column           text not null,
  distribution_family   distribution_family not null,
  is_binary             boolean not null default false,
  default_line          numeric(6, 2),
  unit                  text,
  sort_order            smallint not null default 0,
  is_active             boolean not null default true,
  created_at            timestamptz not null default now()
);

comment on table markets is
  'Market catalogue. Adding a market is a row, not a code branch — which is also what keeps the modelling core sport-agnostic for the NFL build (CLAUDE.md §3).';

comment on column markets.stat_column is
  'Name of the player_game_stats column this market grades against. Lets grading, hit rates and backtests stay generic across markets.';

comment on column markets.default_line is
  'Line to model against when no book line exists yet. College books post props late — often Thursday or Friday for Saturday games — and CLAUDE.md §7 requires the model to run on all projected starters regardless. anytime_td is 0.5 (over 0.5 offensive TDs = scored); continuous markets have no meaningful default and stay NULL, showing a projected range until a line appears.';

comment on column markets.is_binary is
  'Display hint: render as a single probability rather than a line-and-price pair. The underlying maths is identical.';

comment on column markets.emoji is
  'Section marker used on the market sub-card, matching the client''s pitcher-props card language (CLAUDE.md §7).';

-- -----------------------------------------------------------------------------
-- market_positions — which positions each market applies to
-- -----------------------------------------------------------------------------
create table market_positions (
  market_key      text not null references markets(key) on delete cascade,
  position_group  position_group not null,
  sort_order      smallint not null default 0,
  primary key (market_key, position_group)
);

comment on table market_positions is
  'Drives the position tabs and the per-position stat selector dropdown directly (CLAUDE.md §7), so the UI cannot offer a market that the model does not produce for that position.';

-- -----------------------------------------------------------------------------
-- sportsbooks
-- -----------------------------------------------------------------------------
create table sportsbooks (
  id            bigint generated always as identity primary key,
  key           text not null unique,
  display_name  text not null,
  is_active     boolean not null default true,
  priority      smallint not null default 100,
  created_at    timestamptz not null default now()
);

comment on column sportsbooks.priority is
  'Lower sorts first when choosing which book to headline on a card. Per-book odds are still shown.';

-- -----------------------------------------------------------------------------
-- player_prop_lines — append-only line history
-- -----------------------------------------------------------------------------
create table player_prop_lines (
  id              bigint generated always as identity primary key,
  game_id         bigint not null references games(id) on delete cascade,
  player_id       bigint not null references players(id) on delete cascade,
  market_key      text not null references markets(key),
  sportsbook_id   bigint not null references sportsbooks(id),
  season          smallint not null,
  week            smallint not null,
  line            numeric(6, 2) not null,
  over_price      integer,
  under_price     integer,
  captured_at     timestamptz not null default now(),
  is_closing      boolean not null default false,
  source_adapter  text not null,
  source_payload  jsonb,
  created_at      timestamptz not null default now(),
  unique (game_id, player_id, market_key, sportsbook_id, captured_at),
  constraint player_prop_lines_has_a_price
    check (over_price is not null or under_price is not null)
);

comment on table player_prop_lines is
  'Append-only history of posted lines. Never updated in place: a line that moved is a new row, which is what makes the closing-line hit-rate basis in CLAUDE.md §9.2 possible later without a re-backfill.';

comment on column player_prop_lines.line is
  'For anytime_td this is 0.5 (Yes = over 0.5 offensive TDs), so every market shares one representation.';

comment on column player_prop_lines.source_adapter is
  'Which odds adapter produced the row. The odds source is unconfirmed (CLAUDE.md §9.1), so ingestion is pluggable and rows stay attributable if the provider changes.';

comment on column player_prop_lines.is_closing is
  'Marks the final line before kickoff. Set by a job after the game starts, not at capture time.';

create index player_prop_lines_lookup_idx
  on player_prop_lines (game_id, player_id, market_key, captured_at desc);
create index player_prop_lines_week_idx
  on player_prop_lines (season, week, market_key);
create index player_prop_lines_closing_idx
  on player_prop_lines (player_id, market_key, season, week)
  where is_closing;

-- -----------------------------------------------------------------------------
-- Odds math — the single definition of implied probability and de-vig
-- -----------------------------------------------------------------------------
create or replace function american_to_implied_probability(price integer)
returns numeric
language sql
immutable
strict
as $$
  select case
    when price < 0 then (-price)::numeric / ((-price)::numeric + 100)
    else 100::numeric / (price::numeric + 100)
  end;
$$;

comment on function american_to_implied_probability(integer) is
  'Raw (vigged) implied probability from American odds. Not comparable to a model probability on its own — strip the vig with devig_two_way first.';

create or replace function devig_two_way(over_price integer, under_price integer)
returns numeric
language sql
immutable
as $$
  select case
    when over_price is null or under_price is null then null
    else american_to_implied_probability(over_price)
       / (american_to_implied_probability(over_price)
          + american_to_implied_probability(under_price))
  end;
$$;

comment on function devig_two_way(integer, integer) is
  'FAIR probability of the OVER, vig removed by the proportional (multiplicative) method: each side''s raw implied probability divided by the two-way total. Returns NULL when only one side is priced, because a one-sided price cannot be de-vigged — callers must treat that as "no book probability", not as zero edge. CLAUDE.md §6 requires this to match the client''s existing MLB pitcher model exactly; proportional is the common convention but the METHOD IS UNCONFIRMED — see app_config.devig_method and confirm against their implementation before Phase 5.';

create or replace function edge_on_side(
  model_prob_over numeric,
  book_prob_over  numeric,
  side            bet_side
)
returns numeric
language sql
immutable
as $$
  select case
    when model_prob_over is null or book_prob_over is null then null
    when side = 'over' then model_prob_over - book_prob_over
    else (1 - model_prob_over) - (1 - book_prob_over)
  end;
$$;

comment on function edge_on_side(numeric, numeric, bet_side) is
  'THE edge definition (CLAUDE.md §6): model probability minus de-vigged book implied probability, evaluated on the side actually being taken. Explicitly NOT (projection - line) / line. Matches the client''s pitcher-props model so the two boards report comparable numbers.';
