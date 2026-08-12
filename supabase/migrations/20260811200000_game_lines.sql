-- =============================================================================
-- 0030 — game spreads and totals
-- =============================================================================
-- Client request: show the game's spread and over/under on the player card, as
-- context for the prop. A 62-point total says more about a rushing prop than
-- most of what the card carries today.
--
-- THESE COST NOTHING. CFBD serves /lines on the tier already paid for, so this
-- spends ZERO Odds API credits — a distinction worth keeping straight, because
-- the odds budget is the project's tightest constraint and PLAYER props come
-- from a different, metered provider. Nothing here touches that quota.
--
-- MEASURED ON 2025 WEEK 8 BEFORE THE SCHEMA WAS WRITTEN: 108 of 108 games
-- carried a line, 228 line rows across three providers (ESPN Bet on all 108,
-- DraftKings and Bovada on 60 each). Coverage is far better than player props,
-- which carry on 63-68% of games.
--
-- -----------------------------------------------------------------------------
-- THE SPREAD IS FROM THE HOME TEAM'S PERSPECTIVE, AND THAT WAS VERIFIED
-- -----------------------------------------------------------------------------
-- A negative spread means the HOME team is favoured. Checked against CFBD's own
-- `formattedSpread` string ("Notre Dame -10.5") across the whole week-8 slate:
-- 228 of 228 rows agreed, 0 disagreed. This is recorded because getting it
-- backwards is invisible — every number still renders, the favourite is just
-- silently the wrong team — and because this repo has been bitten by exactly
-- that class of CFBD convention before (see the string-vs-int athlete ids and
-- the 2,000-row truncation in Phase 2).
--
-- `formatted_spread` is stored ALONGSIDE the number rather than discarded, so
-- the check above stays runnable against live data instead of being a claim in
-- a comment.
--
-- -----------------------------------------------------------------------------
-- WHY A CONSENSUS VIEW RATHER THAN A PREFERRED BOOK
-- -----------------------------------------------------------------------------
-- Providers disagree (Bovada had this game at -11 where the other two had
-- -10.5, and its OPEN was -8 against DraftKings' -10.5). Picking one book means
-- either a priority table to maintain or a hardcoded name that silently becomes
-- the only source when that book is absent — and coverage is uneven: two of the
-- three providers carried only 60 of 108 games. A median needs no priority, is
-- unmoved by one book being missing, and degrades to "the one line there is"
-- when only one exists.
-- =============================================================================

create table game_lines (
  id                bigint generated always as identity primary key,
  game_id           bigint not null references games(id) on delete cascade,
  provider          text not null,

  -- Home-team perspective. Negative = home favoured. See the header.
  spread            numeric(5, 1),
  spread_open       numeric(5, 1),
  formatted_spread  text,

  over_under        numeric(5, 1),
  over_under_open   numeric(5, 1),

  home_moneyline    integer,
  away_moneyline    integer,

  ingested_at       timestamptz not null default now(),
  unique (game_id, provider)
);

comment on table game_lines is
  'Game-level spreads, totals and moneylines from CFBD /lines, one row per game per provider. DISPLAY CONTEXT ONLY: nothing in the model reads this table, and it must stay that way without a deliberate decision — a game line is a market opinion about the same game the model is predicting, so feeding it into a projection would launder the market''s view into ours and make the edge partly a comparison of the book against itself. Costs no Odds API credits; CFBD serves it on the existing tier.';

comment on column game_lines.spread is
  'FROM THE HOME TEAM''S PERSPECTIVE: negative means the home team is favoured. Verified against CFBD''s formattedSpread across the full 2025 week-8 slate, 228 of 228 rows agreeing. v_board_rows flips it to the player''s own team before display.';

comment on column game_lines.formatted_spread is
  'CFBD''s human string, e.g. "Notre Dame -10.5". Kept so the home-perspective assumption above stays checkable against live data rather than being only a claim in a comment.';

create index game_lines_game_idx on game_lines (game_id);

alter table game_lines enable row level security;

create policy game_lines_public_read
  on game_lines for select
  to anon, authenticated
  using (true);

-- -----------------------------------------------------------------------------
-- Consensus across providers
-- -----------------------------------------------------------------------------
create view v_game_line_consensus
with (security_invoker = true)
as
select
  game_id,
  percentile_cont(0.5) within group (order by spread)     as spread,
  percentile_cont(0.5) within group (order by over_under) as over_under,
  count(*)                                                as providers
from game_lines
group by game_id;

comment on view v_game_line_consensus is
  'Median spread and total per game across whatever providers carried it. percentile_cont ignores NULLs, so a provider posting a total but no spread contributes to one median and not the other, and a game priced by a single book returns that book''s numbers unchanged.';
