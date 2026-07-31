-- =============================================================================
-- 0015 — Distribution family varies by POSITION, not only by market
-- =============================================================================
-- Migration 0009 set one family per market and said outright that those were
-- starting points to be validated against the data in Phase 3. They have now
-- been measured, on 2024-25 box scores restricted to players with enough usage
-- to be projectable (>= 6 games in a season at meaningful volume).
--
-- Four of the nine were wrong, and two of them were wrong because a single
-- family per market cannot be right: the same stat has a different shape
-- depending on who produces it.
--
-- CONTINUOUS MARKETS — AIC with a free location parameter on every candidate,
-- so all three explain the same raw numbers over the same support. (The first
-- pass shifted the data before fitting gamma/lognormal, which silently made
-- their AIC incomparable to the normal's. These do not.)
--
--   market            n      normal   gamma    lognormal   chosen      margin
--   pass_yards   QB   3243   38646    38648    38657       normal          2
--   rush_yards   QB   3192   31799    31224    47468       gamma         575
--   rush_yards   RB   4556   47726    46562    63040       gamma        1163
--   rec_yards    RB   3118   26925    25353    25139       lognormal     214
--   rec_yards    WR   8612   87334    84552    115097      gamma        2782
--   rec_yards    TE   1674   16038    15400    15415       gamma          15
--
-- pass_yards is a genuine tie (margin 2, KS 0.0174 vs 0.0181) and keeps normal
-- as the simpler description. Everything else is decisive.
--
-- rush_yards is the clearest case for per-position families. 26% of QB rushing
-- games are NEGATIVE, because NCAA charges a sack as a rushing loss; RB rushing
-- is negative 0.5% of the time. 0009 chose normal for the market because of the
-- QB case and thereby forced the wrong family onto RBs, whose distribution is
-- strongly right-skewed. A gamma with a free location parameter fits both, and
-- beats normal on each.
--
-- COUNT MARKETS — median within-player variance/mean. Pooling across players
-- with different means inflates variance, so dispersion is only meaningful
-- within a player.
--
--   pass_attempts     2.74   over-dispersed   -> negative_binomial (unchanged)
--   pass_completions  2.11   over-dispersed   -> negative_binomial (unchanged)
--   rush_attempts     2.22   over-dispersed   -> negative_binomial (unchanged)
--   pass_tds          0.93   ~Poisson         -> poisson           (unchanged)
--   receptions RB     0.53   UNDER-dispersed  -> beta_binomial     (was negbinom)
--   receptions TE     0.84   UNDER-dispersed  -> beta_binomial     (was negbinom)
--   receptions WR     0.95   UNDER-dispersed  -> beta_binomial     (was negbinom)
--
-- See migration 0014 for why under-dispersion rules negative binomial out
-- structurally rather than merely making it a poor fit.
-- =============================================================================

alter table market_positions
  add column if not exists distribution_family distribution_family;

comment on column market_positions.distribution_family is
  'Per-position override of markets.distribution_family. NULL means "use the market default". Exists because the same stat has a different shape depending on who produces it — most starkly rush_yards, where 26% of QB games are negative (NCAA charges sacks as rushing losses) while RB rushing is right-skewed and almost never negative. Measured in Phase 3d; see migration 0015 for the numbers.';

-- Rushing yards: gamma for both, with a free location parameter absorbing the
-- negative tail. The market default stays normal so that any position added
-- later without measurement gets the family that at least admits negatives.
update market_positions set distribution_family = 'gamma'
 where market_key = 'rush_yards' and position_group in ('QB', 'RB');

-- Receiving yards: RB prefers lognormal, WR and TE gamma.
update market_positions set distribution_family = 'lognormal'
 where market_key = 'rec_yards' and position_group = 'RB';
update market_positions set distribution_family = 'gamma'
 where market_key = 'rec_yards' and position_group in ('WR', 'TE');

-- Receptions: under-dispersed everywhere it was measured.
update market_positions set distribution_family = 'beta_binomial'
 where market_key = 'receptions';

-- Market defaults that measurement confirmed, restated so the seed comment in
-- 0009 is no longer the only record of the choice.
update markets set distribution_family = 'gamma'
 where key = 'rec_yards';

comment on column markets.distribution_family is
  'Default outcome distribution for this market. VALIDATED against 2024-25 data in Phase 3d, not a guess — see migration 0015. market_positions.distribution_family overrides it where the shape differs by position.';

-- -----------------------------------------------------------------------------
-- Resolution helper
-- -----------------------------------------------------------------------------
-- One definition of "which family applies here", so the worker and any future
-- read-layer consumer cannot disagree about it.
create or replace function resolve_distribution_family(
  p_market_key     text,
  p_position_group position_group
)
returns distribution_family
language sql
stable
as $$
  select coalesce(
           (select mp.distribution_family
              from market_positions mp
             where mp.market_key = p_market_key
               and mp.position_group = p_position_group),
           (select m.distribution_family
              from markets m
             where m.key = p_market_key)
         );
$$;

comment on function resolve_distribution_family(text, position_group) is
  'The distribution family for a market/position pair: the per-position override where one exists, otherwise the market default.';
