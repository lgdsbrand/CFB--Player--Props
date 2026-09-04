-- =============================================================================
-- 0048 -- v_no_vig_rows: the book's price with its margin taken out
-- =============================================================================
-- The client asked for a no-vig page instead of the arbitrage and PrizePicks
-- features he scratched. This is the read behind it.
--
-- ---------------------------------------------------------------------------
-- WHY THIS VIEW TOUCHES NEITHER projections NOR picks
-- ---------------------------------------------------------------------------
-- Every other surface in this product states what the MODEL thinks. This one
-- states what the BOOK is charging, and that difference is the whole point of
-- the page: it carries none of the model's unproven-profitability risk (see
-- docs and the grading record -- the model is calibrated, not proven
-- profitable). A no-vig number is arithmetic on a posted price. It is true
-- whether or not our projections are any good.
--
-- Joining picks would also silently halve the page. `v_board_rows` picks ONE
-- pick per projection through a sportsbook-priority lateral, so a player-market
-- priced by five books surfaces once there. Line shopping is exactly the thing
-- that dedup throws away, so this view stays at book grain: one row per
-- (game, player, market, book).
--
-- ---------------------------------------------------------------------------
-- ONE-SIDED PRICES ARE ABSENT BY CONSTRUCTION, NOT BY OVERSIGHT
-- ---------------------------------------------------------------------------
-- `devig_two_way` returns NULL for a one-sided quote, because a single price
-- carries no information about where the book thinks the other side sits.
-- anytime_td is quoted Yes-only at every book we ingest -- measured 2026-09-04:
-- 3,898 anytime-TD quotes in week 1, ZERO with both prices, against 97-100%
-- two-way on every other market -- so it cannot appear on this page at all.
-- That is honest rather than tidy, and the PAGE has to say so; a market simply
-- missing from a list reads as a data fault.
--
-- The same NULL covers an incoherent pair (implied total below 1). Filtering on
-- `fair_prob_over is not null` therefore drops both cases in one predicate.
--
-- ---------------------------------------------------------------------------
-- WHY EVERY COMPARISON IS GROUPED BY LINE, NOT BY PLAYER-MARKET
-- ---------------------------------------------------------------------------
-- "Best price" and "consensus" are only meaningful between books offering the
-- SAME number. Two books at 77.5 and 78.5 are not competing quotes on one
-- question, and calling the better price on a different line "best" would tell
-- a reader to take a worse bet. So the window partitions on the line as well,
-- and `lines_on_market` exposes the disagreement instead of hiding it: three
-- books at three different numbers shows as books_at_line = 1 with
-- lines_on_market = 3, which is a signal, not a gap.
--
-- ---------------------------------------------------------------------------
-- WHY THIS DOES ITS OWN DISTINCT ON INSTEAD OF READING v_latest_prop_lines
-- ---------------------------------------------------------------------------
-- That view answers the same "latest quote per book" question and was the
-- obvious base to build on. It was, and the first version of this view timed
-- out on a SINGLE WEEK because of it.
--
-- `v_latest_prop_lines` keys its DISTINCT ON on
-- (game_id, player_id, market_key, sportsbook_id). Season and week are selected
-- but are not in that key, so `where season = ... and week = ...` cannot be
-- pushed below the sort-and-unique: Postgres has to de-duplicate every prop
-- line ever stored before it can discard the ones this page is not asking for.
-- On dev that is tens of thousands of rows for a page showing a few hundred.
--
-- Repeating the DISTINCT ON here with season and week LEADING the key is
-- semantically identical -- a game determines its own season and week -- and it
-- makes the week filter pushable, at which point
-- `player_prop_lines_week_idx (season, week, market_key)` does the work. Same
-- trick as the GROUP BY lists below, one level lower.
--
-- The de-duplication also happens BEFORE the two-way filter, deliberately. A
-- book that has pulled one side of a market should drop off this page, not fall
-- back to whatever it was quoting an hour ago: presenting a stale two-way quote
-- as the current price is exactly the failure this page exists to avoid.
--
-- ---------------------------------------------------------------------------
-- THE COMPARISON COLUMNS ARE WINDOWS OVER ONE SCAN, AND THE CONSENSUS IS A MEAN
-- ---------------------------------------------------------------------------
-- The obvious shape -- a CTE of priced quotes, then GROUP BY CTEs for the
-- per-line and per-market aggregates, joined back -- was written first and
-- measured at 8.1 SECONDS for a single week on dev. EXPLAIN said why, and it is
-- worth recording because the shape looks completely reasonable:
--
--   * a CTE referenced MORE THAN ONCE is materialised (PG12+ only inlines
--     single-reference CTEs), and a materialised CTE is an optimisation fence.
--     The season/week filter could not reach the index scan, so all 21,951
--     stored lines were de-duplicated on every request and 18,153 of them
--     thrown away afterwards, three times over;
--   * with no statistics on a materialised CTE the planner chose nested loops
--     over hash joins and discarded 1,865,268 rows on the join filter alone;
--   * the per-market GroupAggregate ran with loops=1919 -- once per output row.
--
-- Referencing the CTE exactly ONCE and computing every comparison as a window
-- over it removes all three at a stroke: one index scan of the week, one sort,
-- no join back. A window's PARTITION BY also carries season and week, which is
-- what lets the caller's filter push down to the index -- the same discipline
-- that `v_slate_weeks` lacks, which is why that view times out on the free tier
-- at twelve concurrent readers.
--
-- The cost of that shape is the consensus statistic. `percentile_cont` is an
-- ordered-set aggregate and Postgres rejects it outright with an OVER clause,
-- so a median consensus is not available in a single pass at all. The mean is,
-- and with at most five books quoting a line the two barely differ -- but a
-- mean is the less robust of the two, so the spread it was chosen over is
-- published beside it: line_prob_over_min and line_prob_over_max say exactly
-- how far apart the books are, which is what a reader actually wants from a
-- "consensus" and what a single number of either kind hides.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- The inverse of american_to_implied_probability
-- -----------------------------------------------------------------------------
-- A fair probability is the honest number, but the client's audience reads
-- prices, and "the book charges -125 for something worth -110" lands where
-- "0.5238 vs 0.5000" does not.
create or replace function implied_probability_to_american(p numeric)
returns integer
language sql
immutable
strict
as $$
  -- 0 and 1 have no price: a certainty cannot be quoted, and rounding a
  -- near-certainty produces a number like -2000000 that reads as a data error.
  -- NULL says "no fair price exists for this" and every caller already handles
  -- a null price.
  select case
    when p <= 0 or p >= 1 then null
    when p >= 0.5 then -round(100 * p / (1 - p))::integer
    else round(100 * (1 - p) / p)::integer
  end;
$$;

comment on function implied_probability_to_american(numeric) is
  'American odds for a probability, the inverse of american_to_implied_probability. Applied to a de-vigged probability this is the FAIR price -- what the bet would cost if the book took no margin. NULL at 0 and 1, which have no quotable price.';

-- -----------------------------------------------------------------------------
-- The view
-- -----------------------------------------------------------------------------
-- First creation, so the column ORDER is still free. From here on it is not:
-- `create or replace view` can only APPEND columns, and trying to insert one in
-- the middle fails with "cannot change name of view column X to Y". Any later
-- change to this view adds columns at the end, or drops and recreates it in a
-- migration that also updates every explicit select list that reads it.
-- The dependent aggregate goes first, or the drop below fails.
drop view if exists v_no_vig_markets;
drop view if exists v_no_vig_rows;

create view v_no_vig_rows
with (security_invoker = true)
as
with latest as (
  select distinct on (
      l.season, l.week, l.game_id, l.player_id, l.market_key, l.sportsbook_id
    )
    l.id            as line_id,
    l.season,
    l.week,
    l.game_id,
    l.player_id,
    l.market_key,
    l.line,
    l.sportsbook_id,
    b.key           as sportsbook_key,
    b.display_name  as sportsbook_name,
    l.over_price,
    l.under_price,
    l.captured_at,
    l.is_closing
  from player_prop_lines l
  join sportsbooks b on b.id = l.sportsbook_id
  order by
    l.season, l.week, l.game_id, l.player_id, l.market_key, l.sportsbook_id,
    l.captured_at desc
),
priced as (
  select
    l.*,
    devig_two_way(l.over_price, l.under_price) as fair_prob_over,
    american_to_implied_probability(l.over_price)
      + american_to_implied_probability(l.under_price) - 1 as hold
  from latest l
  where l.over_price is not null
    and l.under_price is not null
    -- NULL here is a one-sided quote or an incoherent pair (implied total below
    -- 1). Both are "no fair probability exists", and dropping them is what
    -- keeps anytime_td off this page entirely.
    and devig_two_way(l.over_price, l.under_price) is not null
),
compared as (
  select
    p.*,
    count(*)                    over w_line   as books_at_line,
    avg(p.fair_prob_over)       over w_line   as consensus_prob_over,
    min(p.fair_prob_over)       over w_line   as line_prob_over_min,
    max(p.fair_prob_over)       over w_line   as line_prob_over_max,
    -- Best price is decided on IMPLIED PROBABILITY, never on the American
    -- number itself: -105 beats -110 but +105 beats both, and a naive max()
    -- over the integer would rank them backwards.
    min(american_to_implied_probability(p.over_price))  over w_line as best_over_implied,
    min(american_to_implied_probability(p.under_price)) over w_line as best_under_implied,
    count(*)                    over w_market as books_on_market,
    -- COUNT(DISTINCT ...) is not implemented as a window function in Postgres.
    -- Ranking the line from both ends and adding gives the number of distinct
    -- values in the partition, which is the same answer in one pass.
    dense_rank() over (w_market order by p.line)
      + dense_rank() over (w_market order by p.line desc) - 1 as lines_on_market
  from priced p
  window
    -- season and week lead both partitions so the caller's filter reaches the
    -- index scan. They are redundant with game_id and deliberately kept.
    w_line as (
      partition by p.season, p.week, p.game_id, p.player_id, p.market_key, p.line
    ),
    w_market as (
      partition by p.season, p.week, p.game_id, p.player_id, p.market_key
    )
)
select
  c.line_id,
  c.season,
  c.week,
  c.game_id,
  g.start_date,
  g.neutral_site,
  g.sport,

  c.player_id,
  pl.name                 as player_name,
  pts.position_group,

  t.id                    as team_id,
  t.school                as team_school,
  t.abbreviation          as team_abbreviation,
  t.color                 as team_color,
  t.alt_color             as team_alt_color,
  (g.home_team_id = t.id) as is_home,

  o.id                    as opponent_team_id,
  o.school                as opponent_school,
  o.abbreviation          as opponent_abbreviation,

  cf.name                 as conference_name,
  cf.is_displayed         as conference_is_displayed,

  c.market_key,
  m.display_name          as market_name,
  m.short_label           as market_label,
  m.emoji                 as market_emoji,
  m.is_binary,

  c.line,
  c.sportsbook_key,
  c.sportsbook_name,
  c.over_price,
  c.under_price,

  round(c.hold, 6)                                    as hold,
  round(c.fair_prob_over, 6)                          as fair_prob_over,
  round(1 - c.fair_prob_over, 6)                      as fair_prob_under,
  implied_probability_to_american(c.fair_prob_over)   as fair_price_over,
  implied_probability_to_american(1 - c.fair_prob_over) as fair_price_under,

  c.books_at_line,
  c.books_on_market,
  c.lines_on_market,
  round(c.consensus_prob_over, 6)                     as consensus_prob_over,
  round(c.line_prob_over_min, 6)                      as line_prob_over_min,
  round(c.line_prob_over_max, 6)                      as line_prob_over_max,
  -- Signed and unsigned distance from the other books at this line. NULL when
  -- there are no other books, which is NOT zero: zero says this book agrees
  -- with the market, null says there is no market to agree with. The unsigned
  -- one exists so "which book is furthest out of line" can be an ORDER BY --
  -- PostgREST can only sort on columns, and sorting that in the page would sort
  -- whatever survived the 1,000-row cap rather than the slate.
  case when c.books_at_line > 1
       then round(c.fair_prob_over - c.consensus_prob_over, 6) end
                                                      as consensus_delta,
  case when c.books_at_line > 1
       then round(abs(c.fair_prob_over - c.consensus_prob_over), 6) end
                                                      as consensus_delta_abs,
  (american_to_implied_probability(c.over_price)  = c.best_over_implied)  as is_best_over,
  (american_to_implied_probability(c.under_price) = c.best_under_implied) as is_best_under,

  c.captured_at,
  c.is_closing
from compared c
join games g               on g.id = c.game_id
join players pl            on pl.id = c.player_id
join markets m             on m.key = c.market_key
-- A prop line names a player, never a side of the fixture, so the team has to
-- come from the roster. Constraining it to the two teams IN THIS GAME is what
-- makes that safe: player_team_seasons is keyed (player, team, season), so a
-- player who appears for two teams in one season would otherwise multiply every
-- one of his quotes into two rows -- silently, and only for transfers.
join player_team_seasons pts
       on pts.player_id = c.player_id
      and pts.season    = c.season
      and pts.team_id in (g.home_team_id, g.away_team_id)
join teams t               on t.id = pts.team_id
join teams o               on o.id = case when g.home_team_id = t.id
                                          then g.away_team_id else g.home_team_id end
left join team_seasons ts  on ts.team_id = t.id and ts.season = c.season
left join conferences cf   on cf.id = ts.conference_id;

comment on view v_no_vig_rows is
  'One row per (game, player, market, BOOK) for every two-way prop quote, carrying the book''s hold, the de-vigged fair probability and fair price on each side, and how that book compares with the others posting the SAME line. Book grain on purpose -- v_board_rows keeps one pick per projection and would hide the line shopping this exists to show. Reads no projection and no pick: it describes the market, not the model. ALWAYS filter on season and week, which are in both window partitions so the filter pushes down.';

comment on column v_no_vig_rows.hold is
  'The book''s margin on this quote: the two raw implied probabilities summed, minus 1. Measured across 2026 week 1 it runs 4.75-7.94% with a 6.59% average, so a number outside roughly 3-12% is worth distrusting before it is worth reporting.';

comment on column v_no_vig_rows.fair_prob_over is
  'Probability of the over with the vig removed by app_config.devig_method (default shin). NULL is impossible here -- the view already excludes one-sided and incoherent quotes -- which is also why anytime_td never appears: every book prices it Yes-only.';

comment on column v_no_vig_rows.fair_price_over is
  'What the over would cost at no margin. Compare it with over_price to read the charge in the units the client''s audience thinks in.';

comment on column v_no_vig_rows.consensus_prob_over is
  'MEAN fair over probability among the books at this same line -- a mean and not a median because an ordered-set aggregate cannot be a window function, and the grouped-CTE shape that allowed a median cost 8 seconds a week. Read it beside line_prob_over_min/max, which show the spread a single figure of either kind hides.';

comment on column v_no_vig_rows.books_at_line is
  'How many books post THIS line for this prop. The comparison columns are all scoped to that set, because a better price on a different number is not a better bet.';

comment on column v_no_vig_rows.lines_on_market is
  'Distinct lines across all books for this prop. Above 1 means the books disagree about the number, so books_at_line being small is disagreement rather than thin coverage -- the page should say which.';

comment on column v_no_vig_rows.consensus_delta is
  'How far this book''s fair probability sits from the mean of the books at the same line, signed toward the over. NULL when it is the only book there -- a distinction the page must keep, since rendering that as 0.0 turns "nobody else has posted this" into "every book agrees".';

comment on column v_no_vig_rows.is_best_over is
  'True when no book at this line prices the over better. Decided on implied probability, not on the American integer, so +105 correctly beats -105.';

-- -----------------------------------------------------------------------------
-- The market filter's counts, as ONE read
-- -----------------------------------------------------------------------------
-- The page needs "which markets have two-way prices on this slate, and how
-- many", and the first version asked that as one exact `count` per market in
-- parallel -- eight requests for one control. On the free tier that returned a
-- 500 under an ordinary page load, which is the connection-pool ceiling this
-- product has already met once (three concurrent screenshots took every route
-- down for minutes).
--
-- Counting in the page instead is not the alternative: a week is ~1,250 quotes
-- against PostgREST's silent 1,000-row cap, so the rarest market on the slate
-- could fall off the end and be missing from its own filter.
--
-- So the aggregate is a view, for the same reason `v_slate_weeks` is one:
-- PostgREST refuses aggregate functions for the anon role, and a GROUP BY that
-- must be exact has to live in the database.
--
-- `is_upcoming` IS A COLUMN, NOT A PREDICATE THE CALLER APPLIES. The page hides
-- quotes on games that have kicked, so a count that included them would print a
-- number the table below it contradicts. `now()` is STABLE, so grouping on it
-- is well defined within a statement, and exposing it as a column is what lets
-- PostgREST filter on it at all.
create or replace view v_no_vig_markets
with (security_invoker = true)
as
select
  r.season,
  r.week,
  r.sport,
  r.market_key,
  r.market_label,
  r.market_emoji,
  r.conference_is_displayed,
  (r.start_date is null or r.start_date >= now()) as is_upcoming,
  count(*)                                        as quotes,
  count(*) filter (where r.books_at_line > 1)     as shoppable
from v_no_vig_rows r
group by 1, 2, 3, 4, 5, 6, 7, 8;

comment on view v_no_vig_markets is
  'Per-market quote counts for the no-vig page''s filter, one row per (season, week, sport, market, conference scope, upcoming). Exists because PostgREST refuses aggregates for anon and because counting in the page would count a truncated fetch. ALWAYS filter on season and week -- they lead the GROUP BY, so the filter reaches the index underneath v_no_vig_rows.';
