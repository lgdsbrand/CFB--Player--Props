/**
 * Whether a board is showing whole games or one period of them.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). Nothing here knows what a quarter is or
 * which league has one — it splits the catalogue on whether a market is derived
 * from another, and `markets.parent_market_key` is what says so.
 *
 * WHY A SCOPE AND NOT JUST ANOTHER ENTRY IN THE STAT SELECTOR. Two reasons, and
 * the second is the one that matters.
 *
 * The first is arithmetic: with no market filter the board shows every market a
 * player has, so three first-quarter rows would land inside the same card as
 * the full-game ones. A reader scanning REC YDS would find Q1 REC YDS directly
 * beneath it, at a fifth of the value, sorted as though the two were
 * comparable.
 *
 * The second is that they do not make the same claim. A full-game row leads
 * with an OVER/UNDER call and a confidence; a first-quarter row deliberately
 * states nothing (`markets.publishes_call`, migration 0066). Mixing a market we
 * stand behind with one we explicitly do not, in one list, under one heading,
 * is how a reader comes to believe we stand behind both. Separating them is not
 * decoration — it is the difference being visible.
 *
 * THE TWO PROPERTIES ARE INDEPENDENT AND STAY THAT WAY. Today every derived
 * market also publishes no call, so the two tests would give the same answer
 * and it is tempting to use one for both. They are different questions: "is
 * this a segment of another market" decides which list it belongs in, "do we
 * state a number" decides what the row draws. The day a first-quarter model
 * earns its call, the scope must not disappear with it.
 */

import type { Market } from "@/lib/core/types";

/**
 * Which half of the catalogue a board is showing.
 *
 * `full` is every market in its own right — the board as it has always been.
 * `q1` is every market derived from one of those.
 */
export type MarketScope = "full" | "q1";

/** The URL value. `full` is the default and is never written. */
export const FIRST_QUARTER_SCOPE = "q1";

export function resolveMarketScope(
  value: string | string[] | undefined,
): MarketScope {
  const first = Array.isArray(value) ? value[0] : value;
  return first === FIRST_QUARTER_SCOPE ? "q1" : "full";
}

/** A market that is a period of another one, rather than one in its own right. */
export function isDerived(market: Market): boolean {
  return market.parentMarketKey !== null;
}

/** The markets this scope shows, in catalogue order. */
export function marketsInScope(
  markets: Market[],
  scope: MarketScope,
): Market[] {
  return markets.filter((market) =>
    scope === "q1" ? isDerived(market) : !isDerived(market),
  );
}

/**
 * The market keys a scope admits, for the database predicate.
 *
 * FILTERED IN POSTGRES, NEVER AFTER THE FETCH. A week holds thousands of rows
 * and PostgREST caps a response at 1,000 silently, so dropping first-quarter
 * rows in the page would drop them from an arbitrary truncation of the week and
 * quietly show the wrong players — the rule `lib/data/board.ts` is written
 * around, applied to one more filter.
 *
 * Returns the keys rather than "exclude these", so both scopes are expressed
 * the same way and neither is the absence of the other. An empty result means
 * this sport has no markets in that scope, which the caller must read as "show
 * nothing" and not as "no filter" — see `hasScope`.
 */
export function scopeKeys(markets: Market[], scope: MarketScope): string[] {
  return marketsInScope(markets, scope).map((market) => market.key);
}

/**
 * Does this sport have anything in that scope?
 *
 * College has no derived markets at all, because no college book anywhere posts
 * a player quarter or half market (probed 2026-09-09). The control that offers
 * the scope must not appear there, and a hand-edited `?scope=q1` on the college
 * board must fall back rather than render an empty page with a heading claiming
 * first-quarter props exist.
 */
export function hasScope(markets: Market[], scope: MarketScope): boolean {
  return marketsInScope(markets, scope).length > 0;
}

/**
 * What a scope is called on screen, and what it promises.
 *
 * The blurb travels with the list rather than sitting on whatever control led
 * there, for the same reason a preset's caveat does: a shared link arrives at
 * the destination having never shown the control.
 */
export const MARKET_SCOPES: Record<
  MarketScope,
  { label: string; title: string; blurb: string | null }
> = {
  full: {
    label: "Full game",
    title: "Player props",
    blurb: null,
  },
  q1: {
    label: "1st quarter",
    title: "1st-quarter props",
    // THE LAST TWO SENTENCES ARE NOT A DISCLAIMER, THEY ARE THE THING A READER
    // WOULD OTHERWISE GET WRONG. Q1 ALLOWED is narrowed to the first quarter;
    // OPP RK beside it is not, because it is
    // `defense_position_ratings.rank_vs_position`, fitted over whole games.
    // Two columns about the same defense, measuring different slices of the
    // game, one of them ranked and one not — unlabelled, a reader would read
    // them as a pair.
    blurb:
      "The book's line and how this player has done against it. No model call " +
      "on first-quarter markets — a quarter is about a fifth of a game and " +
      "roughly twice as noisy relative to its mean, so the history is the " +
      "honest number here. Q1 ALLOWED is what this opponent has conceded to " +
      "the position per first quarter; OPP RK beside it is a whole-game rank. " +
      "There is no first-quarter ranking on purpose — a defense's " +
      "first-quarter rate barely predicts its own next half-season, so an " +
      "ordering built on it would claim more than the data holds.",
  },
};
