-- =============================================================================
-- 0075 — Which books are sharp, and one summary row per game market
-- =============================================================================
-- G1 of the game model (CLAUDE.md §11). The client asked to see sharp books
-- against retail books. 0074 captures 28 books; this says which is which, and
-- gives the slate table something it can read in one request.
--
-- -----------------------------------------------------------------------------
-- sportsbooks.market_role
-- -----------------------------------------------------------------------------
--   sharp     a market maker that takes large limits and moves on information:
--             Pinnacle. The price the others are measured against.
--   exchange  peer-to-peer: Novig, ProphetX, Kalshi, Betfair, Matchbook,
--             BetOpenly. Near-zero margin, prices set by the bettors.
--   retail    the US regulated books a reader actually bets at: DraftKings,
--             FanDuel, BetMGM, BetRivers, Caesars, Fanatics, ESPN Bet...
--   other     everything else: offshore and European books. Shown per book,
--             but left out of the retail/sharp comparison.
--
-- DATA, NOT CODE, so a book can be reclassified with an UPDATE rather than a
-- deploy. A book the capture creates later lands as `other` until someone
-- decides otherwise — the conservative default, since calling an unknown book
-- sharp would put its number where the reader trusts it most.
-- =============================================================================

alter table sportsbooks
  add column market_role text not null default 'other'
  constraint sportsbooks_market_role_known
    check (market_role in ('sharp', 'exchange', 'retail', 'other'));

comment on column sportsbooks.market_role is
  'sharp (Pinnacle), exchange (Novig, ProphetX, Kalshi, Betfair, Matchbook, BetOpenly), retail (US regulated books), other. Drives the sharp-vs-retail comparison on the game screens (CLAUDE.md §11). New books default to other.';

-- Keys as The Odds API spells them. Rows that do not exist yet are simply not
-- updated; the capture creates them as `other`, and a later UPDATE promotes them.
update sportsbooks set market_role = 'sharp'
 where key in ('pinnacle');

update sportsbooks set market_role = 'exchange'
 where key in ('novig', 'prophetx', 'kalshi', 'betfair_ex_eu', 'betfair_ex_uk',
               'betfair_ex_au', 'matchbook', 'betopenly', 'sporttrade');

update sportsbooks set market_role = 'retail'
 where key in ('draftkings', 'fanduel', 'betmgm', 'betrivers', 'williamhill_us',
               'fanatics', 'espnbet', 'hardrockbet', 'ballybet', 'betparx',
               'fliff', 'windcreek');

-- -----------------------------------------------------------------------------
-- v_game_odds_current gains the role (appended — a replaced view may only add
-- columns at the end).
-- -----------------------------------------------------------------------------
create or replace view v_game_odds_current
with (security_invoker = true)
as
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
  r.first_captured_at,
  sb.market_role
from ranked r
join sportsbooks sb on sb.id = r.sportsbook_id
where r.newest = 1;

-- =============================================================================
-- v_game_odds_summary — one row per (game, period, market)
-- =============================================================================
-- The slate table's only read. A full slate is ~85 games x 28 books x 3
-- markets, about 7,000 current prices: past PostgREST's 1,000-row cap, so the
-- table cannot aggregate per-book rows in the browser without silently
-- dropping books. Here it is ~255 rows.
--
-- `fair` is the vig-free probability of the HOME side (h2h, spreads) or the
-- OVER (totals), by the same devig_two_way every prop number uses. For spreads
-- it is the probability AT THAT BOOK'S LINE, so a median across books with
-- different lines is a rough figure; the per-book panel is the exact one.
--
-- MEDIANS, as `v_game_line_consensus` (0030) chose, for the same reason: no
-- priority table, unmoved by one book being absent or stale.
-- `percentile_cont` returns double precision; round in the reader, not here.
-- =============================================================================
create view v_game_odds_summary
with (security_invoker = true)
as
with priced as (
  select
    c.*,
    case c.market
      when 'totals' then devig_two_way(c.over_price, c.under_price)
      else devig_two_way(c.home_price, c.away_price)
    end as fair,
    case c.market
      when 'totals' then devig_two_way(c.first_over_price, c.first_under_price)
      else devig_two_way(c.first_home_price, c.first_away_price)
    end as first_fair
  from v_game_odds_current c
)
select
  game_id,
  period,
  market,
  count(*)                                                        as books,
  percentile_cont(0.5) within group (order by line)               as consensus_line,
  percentile_cont(0.5) within group (order by first_line)         as consensus_first_line,
  percentile_cont(0.5) within group (order by fair)               as consensus_fair,
  percentile_cont(0.5) within group (order by line)
    filter (where market_role = 'sharp')                          as sharp_line,
  percentile_cont(0.5) within group (order by first_line)
    filter (where market_role = 'sharp')                          as sharp_first_line,
  percentile_cont(0.5) within group (order by fair)
    filter (where market_role = 'sharp')                          as sharp_fair,
  percentile_cont(0.5) within group (order by first_fair)
    filter (where market_role = 'sharp')                          as sharp_first_fair,
  percentile_cont(0.5) within group (order by line)
    filter (where market_role = 'exchange')                       as exchange_line,
  percentile_cont(0.5) within group (order by fair)
    filter (where market_role = 'exchange')                       as exchange_fair,
  percentile_cont(0.5) within group (order by line)
    filter (where market_role = 'retail')                         as retail_line,
  percentile_cont(0.5) within group (order by first_line)
    filter (where market_role = 'retail')                         as retail_first_line,
  percentile_cont(0.5) within group (order by fair)
    filter (where market_role = 'retail')                         as retail_fair,
  count(*) filter (where market_role = 'sharp')                   as sharp_books,
  count(*) filter (where market_role = 'exchange')                as exchange_books,
  count(*) filter (where market_role = 'retail')                  as retail_books,
  min(first_captured_at)                                          as first_seen_at,
  max(captured_at)                                                as last_moved_at
from priced
group by game_id, period, market;

comment on view v_game_odds_summary is
  'One row per (game, period, market) across every captured book: medians overall and per market_role (sharp, exchange, retail). *_line is the spread from OUR home team''s perspective or the total; *_fair is the vig-free probability of home (h2h/spreads) or over (totals). first_* is the first price WE saw, not the opening line. Filter by game_id.';
