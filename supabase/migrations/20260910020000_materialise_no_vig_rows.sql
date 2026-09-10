-- =============================================================================
-- 0062 -- materialise the no-vig rows: the page's last concurrency ceiling
-- =============================================================================
-- `/no-vig` still failed under load after 0057 (an index), 0060 (three scans
-- folded to two) and the render-side paging. Measured on the LIVE site
-- 2026-09-09 with the NFL slate loaded: SIX of six concurrent requests 500d --
-- worse than the 11-of-12 the day before, because that morning's capture had
-- just made the view heavier. Serially it is fine (3.3-4.3 s). That gap is the
-- signature of a CPU ceiling, not a broken query.
--
-- The free levers are spent. This is the one paid for with storage.
--
-- ---------------------------------------------------------------------------
-- WHY THE VIEW CANNOT BE MADE FAST IN PLACE -- a rows=1 estimate, then loops
-- ---------------------------------------------------------------------------
-- EXPLAIN (ANALYZE, BUFFERS) on production, for the page's exact filter
-- (sport=nfl, season=2026, week=1, conference_is_displayed):
--
--     Nested Loop  (cost=63.87..33416.67 rows=1) (actual rows=3103)
--       Buffers: shared hit=173041
--       ...
--           Join Filter: (pts.team_id = ts.team_id)
--           Rows Removed by Join Filter: 2218645
--           ->  Materialize (actual rows=3103 loops=716)
--
-- **173,041 buffer hits to return 3,103 rows**, and 2.2 MILLION rows discarded
-- on a single join filter. The cause is the `rows=1` estimate: Postgres cannot
-- see through the DISTINCT ON + double WindowAgg chain, guesses one row, and
-- therefore picks nested loops -- so `team_seasons` (716 rows for 2026) drives
-- a loop that re-materialises the whole window output 716 times.
--
-- **No index fixes a bad cardinality estimate.** Migration 0057 already proved
-- that on this same view: it changed the plan not at all. A materialised view
-- fixes it by construction -- the rows become real, ANALYZE gives them real
-- statistics, and the page reads a plain heap through a plain index instead of
-- re-deriving the window chain on every request.
--
-- ---------------------------------------------------------------------------
-- THE REFRESH HAS AN OBVIOUS OWNER, AND THAT IS WHY THIS IS SAFE
-- ---------------------------------------------------------------------------
-- Everything under this view comes from `player_prop_lines`, whose ONLY writer
-- is `ingest_odds`, running every six hours. So the refresh is not a guess
-- about staleness: it goes in the job that produces the data, in the same run,
-- straight after the write. If the refresh fails the job fails, and
-- `monitor_pipeline` already watches `ingest_odds` per sport.
--
-- **A stale price shown as a current one is the single failure this page must
-- not have** -- its own DISTINCT ON deliberately drops a book that has pulled
-- one side rather than fall back to an older quote. Tying the refresh to the
-- writer is what preserves that guarantee.
--
-- REFRESH ... CONCURRENTLY requires a UNIQUE index, which is what
-- `mv_no_vig_rows_line_id_idx` is for. Concurrently matters because the plain
-- form takes an ACCESS EXCLUSIVE lock: every reader would block for the length
-- of the rebuild, turning a 6-hourly job into a 6-hourly outage.
--
-- ---------------------------------------------------------------------------
-- RLS: NOTHING NEW IS EXPOSED, AND THAT WAS CHECKED, NOT ASSUMED
-- ---------------------------------------------------------------------------
-- A materialised view does NOT apply the base tables' row-level security, so
-- this would be a real disclosure risk if any of them were restricted. They are
-- not. Every table read here carries a `..._public_read` policy with
-- `qual = true` for `{anon,authenticated}` -- verified on production through
-- pg_policies before this was written: player_prop_lines, games, players,
-- markets, player_team_seasons, teams, team_seasons, conferences, sportsbooks.
-- The MV therefore holds exactly what `anon` could already select.
-- **If any base table ever gains a restrictive policy, revisit this.**
--
-- ---------------------------------------------------------------------------
-- WHY `v_no_vig_rows` SURVIVES AS A THIN VIEW
-- ---------------------------------------------------------------------------
-- Three callers read it: the page (`buildNoVigQuery`), the `no_vig_summary`
-- function (0060), and `check-schema`. Repointing it at the MV keeps all three
-- working with no code change and NO REMOVAL -- migration 0055 is the reason a
-- removal never rides along with an addition. The column list is unchanged,
-- which is exactly what lets `create or replace view` accept this.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- FIRST, A TRAP THAT ONLY MATERIALISED VIEWS FALL INTO: search_path
-- ---------------------------------------------------------------------------
-- The first attempt at this migration failed with:
--
--     UndefinedTable: relation "app_config" does not exist
--     QUERY: select devig_two_way(...)
--     CONTEXT: SQL function "devig_two_way" during inlining
--
-- `app_config` exists, in `public`, and the session's search_path contained
-- `public`. The reason it was still invisible is that **CREATE and REFRESH
-- MATERIALIZED VIEW execute their query under a RESTRICTED search_path**
-- (essentially pg_catalog only). That is a deliberate Postgres defence: a
-- refresh can be triggered by another role, so an unqualified name inside it
-- must not resolve through whatever schema that role happens to have first.
--
-- **The same expression in a plain view works.** `v_no_vig_rows` has been
-- calling `devig_two_way` for weeks without complaint, because a plain view is
-- expanded in the CALLER's search_path. So this failure mode is invisible right
-- up until the day something is materialised.
--
-- Pinning search_path on the functions is the fix, and it is the right fix
-- regardless: it is exactly what Supabase's own linter asks for on any
-- non-trivial function. `public, pg_temp` (pg_temp LAST) is the standard form.
-- This is a MODIFICATION, not a removal, and it changes no result -- public was
-- already the effective schema for every existing caller.
alter function devig_two_way(integer, integer)
  set search_path = public, pg_temp;
alter function devig_two_way(integer, integer, text)
  set search_path = public, pg_temp;
alter function devig_two_way_shin(integer, integer)
  set search_path = public, pg_temp;
alter function devig_two_way_additive(integer, integer)
  set search_path = public, pg_temp;
alter function devig_two_way_proportional(integer, integer)
  set search_path = public, pg_temp;
alter function devig_shin_z(integer, integer)
  set search_path = public, pg_temp;
alter function american_to_implied_probability(integer)
  set search_path = public, pg_temp;
alter function implied_probability_to_american(numeric)
  set search_path = public, pg_temp;

-- -----------------------------------------------------------------------------
-- The materialised rows. Body is IDENTICAL to the 0048 view definition; it was
-- copied from it programmatically rather than retyped.
-- -----------------------------------------------------------------------------
create materialized view mv_no_vig_rows as
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


-- REQUIRED by REFRESH ... CONCURRENTLY. `line_id` is the id of the underlying
-- `player_prop_lines` row and the DISTINCT ON emits each at most once, so it is
-- unique by construction rather than by hope.
create unique index mv_no_vig_rows_line_id_idx on mv_no_vig_rows (line_id);

-- The page's filter, in the order every request pins it.
-- `conference_is_displayed` sits last because it is the only nullable one: a
-- player whose team has no `team_seasons` row for the season gets NULL from the
-- left join, and NULL is correctly excluded by the page's `eq(true)`.
create index mv_no_vig_rows_page_idx
  on mv_no_vig_rows (sport, season, week, conference_is_displayed);

-- Supports the "upcoming only" kickoff cutoff and the market pills, which both
-- filter WITHIN a slate week rather than across weeks.
create index mv_no_vig_rows_slate_idx
  on mv_no_vig_rows (season, week, sport, market_key, start_date);

comment on materialized view mv_no_vig_rows is
  'Materialised no-vig quotes. Exists because the equivalent view could not be PLANNED: its DISTINCT ON + double WindowAgg chain estimates rows=1, so Postgres chose nested loops and burned 173,041 buffer hits plus 2.2M discarded join-filter rows to return 3,103 -- tolerable at ~1.4 s serially, but 500ing from about three concurrent readers. REFRESHED BY ingest_odds in the same run that writes player_prop_lines, because that job is the only writer of the data beneath it; a refresh that drifted from that write would present a stale price as a current one, the single failure this page exists to prevent.';

-- anon reads through `v_no_vig_rows`, which is security_invoker, so the invoker
-- needs the privilege on the MV itself. Safe per the RLS note in the header.
grant select on mv_no_vig_rows to anon, authenticated;

-- -----------------------------------------------------------------------------
-- Repoint the view. Same columns, same order, same types -- so this is a
-- `create or replace`, not a drop, and nothing downstream notices.
-- -----------------------------------------------------------------------------
create or replace view v_no_vig_rows
with (security_invoker = true)
as
select * from mv_no_vig_rows;

comment on view v_no_vig_rows is
  'Two-way prop quotes with the vig stripped, one row per book per line. Now a thin passthrough over mv_no_vig_rows (migration 0062): the logic and the column list are unchanged from 0048, only where the rows come from changed. Read THIS, not the MV, so security_invoker keeps applying.';
