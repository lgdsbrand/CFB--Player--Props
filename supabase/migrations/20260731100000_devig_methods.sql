-- =============================================================================
-- 0013 — Selectable de-vig methods
-- =============================================================================
-- Migration 0006 defined ONE de-vig (proportional) and marked it UNCONFIRMED,
-- because CLAUDE.md §6 required matching the client's existing MLB pitcher
-- model. On 2026-07-31 the client released that requirement: this is their own
-- model, so the method is chosen on merit.
--
-- Three methods are now available and `app_config.devig_method` selects one.
-- The default becomes 'shin'.
--
-- WHY IT MATTERS AT ALL. On a two-way market priced near -110/-110 every method
-- agrees to well under a percentage point, so for yardage and reception props
-- the choice is immaterial. It is decisive for anytime touchdown, which is
-- priced at wildly lopsided numbers. On +600/-1100:
--
--     proportional -> 13.48%      shin/additive -> 11.31%
--
-- a 2.2 point gap on a 13% probability, which on its own can move a pick across
-- the 5% edge threshold in app_config.edge_threshold.
--
-- THE IDENTITY. For a TWO-outcome market Shin's method and the additive method
-- are the same number. Shin requires
--
--     sqrt(z^2 + a*pi1^2) + sqrt(z^2 + a*pi2^2) = 2,   a = 4(1-z)/PI
--
-- Multiplying the difference of those roots by their sum (which is 2) gives
--     2D = a(pi1^2 - pi2^2) = a(pi1 - pi2)PI = 4(1-z)(pi1 - pi2)
-- so D = 2(1-z)(pi1 - pi2), and dividing by the 2(1-z) denominator leaves
-- p1 - p2 = pi1 - pi2: both sides shifted by the same amount, which is exactly
-- what the additive method does.
--
-- That is why the Shin implementation below is a closed form rather than the
-- bisection its Python twin uses. Both land on the same double; the closed form
-- is exact and cheap enough to sit inside a view. The Python side keeps the
-- general solver because it also reports z, and because it stays correct if a
-- three-way market is ever added — that is the only case where Shin and
-- additive diverge.
--
-- Consequence for how the default is justified: the published results showing
-- Shin better calibrated than plain normalization come from THREE-outcome
-- markets. Every market we model is two-way, so the real decision here is
-- proportional vs not-proportional. Shin/additive is chosen because
-- proportional's known failure is under-correcting the favourite-longshot bias,
-- and that bias sits precisely where our most lopsided market sits.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Shared guard: what counts as a coherent two-way market
-- -----------------------------------------------------------------------------
-- Two real prices on opposite sides of the same line always imply MORE than 1.0
-- in total; that surplus is the book's margin. A total below 1 is free money,
-- which does not exist at scale, so in practice it means the pair is not what it
-- claims — a stale price, a mispull, or two different lines crossed upstream.
-- De-vigging it anyway would produce a confident fair probability from the least
-- trustworthy input we ever see, and the edge computed from it would look
-- enormous. NULL is the honest answer, and it routes to the same "no book
-- probability" handling as a one-sided quote. Exactly 1.0 is accepted: a
-- zero-vig market needs no correction.

create or replace function devig_two_way_proportional(
  over_price integer, under_price integer
)
returns numeric
language plpgsql
immutable
as $$
declare
  o     double precision;
  u     double precision;
  total double precision;
begin
  if over_price is null or under_price is null then
    return null;
  end if;
  o := american_to_implied_probability(over_price)::double precision;
  u := american_to_implied_probability(under_price)::double precision;
  total := o + u;
  if total < 1.0 - 1e-9 or total <= 0 then
    return null;
  end if;
  return (o / total)::numeric;
end;
$$;

comment on function devig_two_way_proportional(integer, integer) is
  'Multiplicative de-vig: each side''s raw implied probability divided by the two-way total. The common convention. Under-corrects the favourite-longshot bias, which is why it is no longer the default — see migration 0013.';

create or replace function devig_two_way_additive(
  over_price integer, under_price integer
)
returns numeric
language plpgsql
immutable
as $$
declare
  o          double precision;
  u          double precision;
  total      double precision;
  half_over  double precision;
  fair_over  double precision;
  fair_under double precision;
begin
  if over_price is null or under_price is null then
    return null;
  end if;
  o := american_to_implied_probability(over_price)::double precision;
  u := american_to_implied_probability(under_price)::double precision;
  total := o + u;
  if total < 1.0 - 1e-9 or total <= 0 then
    return null;
  end if;

  half_over  := (total - 1.0) / 2.0;
  fair_over  := o - half_over;
  fair_under := u - half_over;

  -- Defensive only. For a two-way market this cannot trigger: it would need
  -- o < u - 1, and a raw implied probability from American odds is always
  -- strictly below 1.
  if fair_over <= 0 or fair_over >= 1 or fair_under <= 0 or fair_under >= 1 then
    return null;
  end if;
  return fair_over::numeric;
end;
$$;

comment on function devig_two_way_additive(integer, integer) is
  'Balanced de-vig: subtract half the overround from each side. Provably identical to Shin for a two-way market (see migration 0013 header).';

create or replace function devig_two_way_shin(
  over_price integer, under_price integer
)
returns numeric
language plpgsql
immutable
as $$
begin
  -- Closed form for two outcomes; see the identity proved in the header.
  return devig_two_way_additive(over_price, under_price);
end;
$$;

comment on function devig_two_way_shin(integer, integer) is
  'Shin (1993) de-vig, which models the overround as protection against informed money and shades longshots down relative to proportional. For a TWO-outcome market this is algebraically identical to the additive method, so it is implemented as that closed form rather than by the bisection its Python twin uses. The two agree to ~1e-13.';

-- -----------------------------------------------------------------------------
-- devig_shin_z — the diagnostic
-- -----------------------------------------------------------------------------
create or replace function devig_shin_z(
  over_price integer, under_price integer
)
returns numeric
language plpgsql
immutable
as $$
declare
  o          double precision;
  u          double precision;
  total      double precision;
  fair_over  double precision;
  fair_under double precision;
begin
  fair_over := devig_two_way_shin(over_price, under_price)::double precision;
  if fair_over is null then
    return null;
  end if;
  fair_under := 1.0 - fair_over;
  if fair_over <= 0 or fair_under <= 0 then
    return null;
  end if;

  o     := american_to_implied_probability(over_price)::double precision;
  u     := american_to_implied_probability(under_price)::double precision;
  total := o + u;

  -- From pi_i^2/PI = p_i^2 + z*p_i*(1-p_i), with p1 + p2 = 1 so that
  -- p1*(1-p1) = p1*p2.
  return (((o * o / total) - (fair_over * fair_over))
          / (fair_over * fair_under))::numeric;
end;
$$;

comment on function devig_shin_z(integer, integer) is
  'Shin''s z: the share of market volume the model attributes to informed money. Not used in pricing — it is a market-quality diagnostic. An implausibly high z usually means a stale or mispulled price rather than a genuinely toxic market.';

-- -----------------------------------------------------------------------------
-- Dispatchers
-- -----------------------------------------------------------------------------
create or replace function devig_two_way(
  over_price integer, under_price integer, method text
)
returns numeric
language plpgsql
immutable
as $$
begin
  case lower(method)
    when 'proportional' then
      return devig_two_way_proportional(over_price, under_price);
    when 'additive' then
      return devig_two_way_additive(over_price, under_price);
    when 'shin' then
      return devig_two_way_shin(over_price, under_price);
    else
      -- Never silently fall back. A typo in app_config would otherwise change
      -- every edge on the board with nothing to show for it.
      raise exception
        'Unknown de-vig method %. Expected one of: proportional, additive, shin.',
        method;
  end case;
end;
$$;

comment on function devig_two_way(integer, integer, text) is
  'FAIR probability of the OVER by an explicitly named method. Returns NULL when only one side is priced, because a one-sided price cannot be de-vigged — callers must treat that as "no book probability", not as zero edge. Also NULL when the two prices do not form a coherent market (implied total below 1). Mirrors worker.core.probability.devig_two_way; the test suite pins both to the same vectors.';

-- The two-argument form keeps every existing call site working and now follows
-- configuration. Reading app_config makes it STABLE rather than IMMUTABLE,
-- which is fine: its only consumer is the v_latest_prop_lines view. Nothing
-- indexed or generated depends on it — picks.edge is generated from
-- edge_on_side(), which takes the already-computed probability as an argument.
create or replace function devig_two_way(over_price integer, under_price integer)
returns numeric
language sql
stable
as $$
  select devig_two_way(
    over_price,
    under_price,
    coalesce(
      (select value #>> '{}' from app_config where key = 'devig_method'),
      'shin'
    )
  );
$$;

comment on function devig_two_way(integer, integer) is
  'FAIR probability of the OVER using the method in app_config.devig_method (default shin). STABLE, not IMMUTABLE, because it reads configuration — use the three-argument form where an immutable expression is required.';

-- -----------------------------------------------------------------------------
-- Configuration
-- -----------------------------------------------------------------------------
update app_config
   set value = '"shin"'::jsonb,
       description =
         'Method used to strip vig before comparing to a model probability. '
         'CHOSEN 2026-07-31 on merit — the client released the requirement to '
         'match their MLB pitcher model. One of: proportional, additive, shin. '
         'For a two-way market shin and additive are algebraically identical '
         '(see migration 0013), so the live choice is proportional vs not. '
         'Immaterial near -110/-110; decisive on anytime touchdown, where '
         'proportional gives a +600 longshot 13.5% against shin''s 11.3%.'
 where key = 'devig_method';

-- Records which method produced a stored book probability. Without this, a
-- later change to devig_method would silently reinterpret history: rows written
-- under one method would sit alongside rows written under another with nothing
-- to tell them apart.
alter table picks
  add column if not exists devig_method text;

comment on column picks.devig_method is
  'The de-vig method that produced book_prob_over on this row. NULL when no book price was attached. Stored per row because app_config.devig_method can change and edges computed under different methods are not comparable.';

alter table backtest_predictions
  add column if not exists devig_method text;

comment on column backtest_predictions.devig_method is
  'De-vig method used when grading this prediction. Backtests run under different methods are not comparable, so the method travels with the row.';
