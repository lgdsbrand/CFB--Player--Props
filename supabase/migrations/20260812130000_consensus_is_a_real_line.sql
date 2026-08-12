-- =============================================================================
-- 0038 -- the consensus line must be a line a book actually posted
-- =============================================================================
-- FOUND BY BUILDING THE GAMES VIEW ON TOP OF IT. The board shows the spread as
-- a caption on a player card, where a wrong decimal is easy to miss. Analyze
-- Games puts it in the headline of every game card, and the first screen of the
-- 2026 opening slate read "USC -36.8".
--
-- No sportsbook posts -36.8. Spreads and totals move on half-point ticks.
--
-- `percentile_cont` INTERPOLATES. Given an even number of providers it returns
-- the midpoint of the two middle values, so two books at -36.5 and -37.0 produce
-- -36.75, which renders as -36.8 and exists nowhere. Measured on the 934 priced
-- games in dev: 75 spreads and 64 totals sat off the market tick, all of them
-- from the 170 games carrying an even number of providers. Where three books
-- priced a game the two functions already agreed, which is why this survived
-- review -- the common case is correct.
--
-- `percentile_disc` returns an ACTUAL OBSERVED VALUE: with three providers, the
-- middle book's line; with two, the lower of them. Every number the product
-- prints is then a number somebody could have bet into, and the caption
-- "median across N sportsbooks" becomes literally true rather than approximately
-- true.
--
-- THE COST, STATED RATHER THAN BURIED. With an even number of providers
-- `percentile_disc` takes the LOWER of the two middle values, so on those games
-- the reported spread sits about a quarter point below the interpolated middle,
-- which on the home-perspective scale reads as a marginally bigger home
-- favourite. It is a systematic shift, not a random one.
--
-- Accepted, for two reasons. It is a quarter of a point on a context caption
-- that no model input reads (migration 0030 drew that boundary deliberately),
-- and it applies to the 18% of games priced by an even number of books --
-- where three books priced a game, which is 81% of them, the two functions
-- return the same observed value anyway.
--
-- THE ALTERNATIVE WAS ROUNDING the interpolated value to the nearest half point.
-- That removes the shift and lands on a real tick, but the result can be a line
-- NO book posted: two books at -5.5 and -3.0 round to -4.5, which is neither of
-- them. Choosing an observed value keeps a much stronger property -- every
-- number the product prints is one a reader could have found at a book -- and
-- that property is checkable, which is why `audit_data` asserts it.
--
-- NULL HANDLING IS UNCHANGED. Both functions ignore NULLs, so a provider posting
-- a total but no spread still contributes to one median and not the other, and a
-- game priced by a single book still returns that book's numbers untouched.
--
-- THE CASTS ARE LOAD-BEARING, NOT TIDINESS. The two functions return different
-- types: `percentile_cont` always returns double precision, while
-- `percentile_disc` returns the type it was given -- here `numeric`, because
-- that is what `game_lines.spread` is. `create or replace view` may append
-- columns but may not change one's type, so without the casts this migration
-- fails outright with "cannot change data type of view column". Keeping the
-- columns double precision also keeps every consumer's parsed shape identical:
-- PostgREST serialises numeric as a STRING to preserve precision, so dropping
-- the cast would silently turn `spread` into text in the app.
--
-- With those, `v_board_rows` and `v_slate_games` -- both of which join this --
-- need no change and no redeploy ordering.
-- =============================================================================

create or replace view v_game_line_consensus
with (security_invoker = true)
as
select
  game_id,
  (percentile_disc(0.5) within group (order by spread))::double precision
    as spread,
  (percentile_disc(0.5) within group (order by over_under))::double precision
    as over_under,
  count(*) as providers
from game_lines
group by game_id;

comment on view v_game_line_consensus is
  'Median spread and total per game across whatever providers carried it. percentile_DISC rather than percentile_cont, so the value returned is one a book actually posted: spreads and totals move on half-point ticks, and interpolating between two books straddling a tick produced numbers like -36.8 that exist at no sportsbook (migration 0038). Both ignore NULLs, so a provider posting a total but no spread contributes to one median and not the other, and a game priced by a single book returns that book''s numbers unchanged.';
