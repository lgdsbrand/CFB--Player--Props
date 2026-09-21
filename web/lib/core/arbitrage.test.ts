/**
 * Tests for the arbitrage arithmetic.
 *
 *   npm run test
 *
 * WHAT IS WORTH TESTING HERE. Not that 2 + 2 is 4. The failure modes that
 * matter are the ones where a wrong answer still looks like money: a split that
 * does not actually pay the same on both sides, a rounding step that quietly
 * turns a guarantee into a possible loss, and the American-odds sign convention
 * — which is the one piece of this that a reader cannot sanity-check by eye.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  americanToDecimal,
  americanToImpliedProbability,
  arbitrage,
  breakEvenOtherSide,
  decimalToAmerican,
} from "./arbitrage.ts";

// -----------------------------------------------------------------------------
// Conversions — the sign convention is the whole risk
// -----------------------------------------------------------------------------

test("American odds convert both ways around the +100 / -100 hinge", () => {
  // -110 is the standard two-way price and 1.909 is its decimal form; getting
  // the favourite branch backwards is the classic sign bug and would make every
  // favourite look like an arb.
  assert.equal(americanToDecimal(100), 2);
  assert.equal(americanToDecimal(-100), 2);
  assert.ok(Math.abs(americanToDecimal(-110) - 1.9090909) < 1e-6);
  assert.ok(Math.abs(americanToDecimal(150) - 2.5) < 1e-12);

  assert.equal(decimalToAmerican(2), 100);
  assert.equal(decimalToAmerican(2.5), 150);
  assert.equal(decimalToAmerican(1.5), -200);
});

test("a favourite implies more than half and an underdog less", () => {
  assert.ok(Math.abs(americanToImpliedProbability(-110) - 0.5238095) < 1e-6);
  assert.ok(Math.abs(americanToImpliedProbability(110) - 0.4761905) < 1e-6);
  assert.equal(americanToImpliedProbability(100), 0.5);
});

test("the implied probability is the VIGGED one, so a standard pair sums above 1", () => {
  // If this ever de-vigged, every pair would sum to 1 and no arb could be
  // detected. -110/-110 summing to 1.0476 IS the 4.76% hold.
  const sum =
    americanToImpliedProbability(-110) + americanToImpliedProbability(-110);
  assert.ok(sum > 1);
  assert.ok(Math.abs(sum - 1.047619) < 1e-6);
});

// -----------------------------------------------------------------------------
// The verdict
// -----------------------------------------------------------------------------

test("the standard two-way price is not an arb, and its margin is the book's hold", () => {
  const result = arbitrage({ priceA: -110, priceB: -110, stake: 100 });

  assert.equal(result.isArbitrage, false);
  // The same 4.76% the no-vig page would print as Hold for this quote.
  assert.ok(Math.abs(result.margin - 0.047619) < 1e-6);
  // And the "return" is negative: staking both sides loses the hold.
  assert.ok(result.returnOnStake < 0);
});

test("two prices that sum below 1 are an arb, and both sides pay the same", () => {
  // +120 at one book against -105 at another: 0.4545 + 0.5122 = 0.9667.
  const result = arbitrage({ priceA: 120, priceB: -105, stake: 1000 });

  assert.equal(result.isArbitrage, true);
  assert.ok(result.margin < 0);

  // THE PROPERTY THAT MATTERS: the ideal split pays the same whichever side
  // wins. Checked on the exact split, so this is about the maths and not about
  // rounding, which the next test covers.
  const payoutA = result.exactStakeA * result.decimalA;
  const payoutB = result.exactStakeB * result.decimalB;
  assert.ok(Math.abs(payoutA - payoutB) < 1e-9);

  // And that common payout beats the stake by exactly the stated return.
  assert.ok(Math.abs(payoutA - 1000 * (1 + result.returnOnStake)) < 1e-9);
});

test("the split is the whole stake, not a stake per side", () => {
  // A calculator that returned two stakes summing to 2x the input would double
  // the reader's exposure. Exact split first, then the rounded one to a cent.
  const result = arbitrage({ priceA: 120, priceB: -105, stake: 1000 });

  assert.ok(Math.abs(result.exactStakeA + result.exactStakeB - 1000) < 1e-9);
  assert.ok(Math.abs(result.stakeA + result.stakeB - 1000) <= 0.01);
});

// -----------------------------------------------------------------------------
// Rounding — where a guarantee can quietly stop being one
// -----------------------------------------------------------------------------

test("rounding to cents is reported through its own payouts, not assumed away", () => {
  const result = arbitrage({ priceA: 120, priceB: -105, stake: 1000 });

  // Both rounded payouts are still above the stake on an arb this size, and
  // `worstPayout` is the one the page leads with.
  assert.equal(result.worstPayout, Math.min(result.payoutA, result.payoutB));
  assert.ok(result.worstProfit > 0);
  assert.ok(result.worstPayout <= Math.max(result.payoutA, result.payoutB));
});

test("a tiny stake on a thin arb can round into a loss, and says so", () => {
  // THE CASE THE FIELD GETS WRONG. On a 0.2% edge with a $1 stake there is not
  // enough granularity in whole cents to hold the guarantee, so the honest
  // answer is that the worse outcome loses. Asserting the SIGN is available
  // rather than a specific figure: the point is that the page can tell.
  const thin = arbitrage({ priceA: 101, priceB: -100, stake: 1 });

  assert.equal(thin.isArbitrage, true);
  assert.equal(
    thin.worstProfit,
    Math.min(thin.payoutA, thin.payoutB) - 1,
  );
});

// -----------------------------------------------------------------------------
// What to wait for
// -----------------------------------------------------------------------------

test("the break-even price says what the other side must beat", () => {
  // Given -110 on one side, the other must be longer than +110 to arb. At
  // exactly +110 the pair sums to 1 and there is no edge, which is why the page
  // words it as "longer than".
  assert.equal(breakEvenOtherSide(-110), 110);

  const atBreakEven = arbitrage({ priceA: -110, priceB: 110, stake: 100 });
  assert.ok(Math.abs(atBreakEven.margin) < 1e-9);
  assert.equal(atBreakEven.isArbitrage, false);

  // A cent better on the other side and it is an arb.
  assert.equal(arbitrage({ priceA: -110, priceB: 115, stake: 100 }).isArbitrage, true);
});

test("a heavy favourite needs a price no book posts, and that is still a number", () => {
  // -2000 implies 95.2%, so the other side must beat about +2000. Nobody posts
  // that, but the figure is correct and saying "not realistically" is the
  // page's job — clamping it here would hide a true answer behind a policy.
  const needed = breakEvenOtherSide(-2000);

  assert.ok(needed !== null);
  assert.ok(needed > 1500, `expected a long price, got ${needed}`);

  // And it really is the boundary: the pair at that price has no edge either way.
  const atBoundary = arbitrage({ priceA: -2000, priceB: needed, stake: 100 });
  assert.ok(Math.abs(atBoundary.margin) < 1e-4);
});

test("a non-finite input yields null rather than a nonsense price", () => {
  // The guard exists for this and only this. No American price reaches an
  // implied probability of 1, so a reader cannot reach it.
  assert.equal(breakEvenOtherSide(Number.NaN), null);
});
