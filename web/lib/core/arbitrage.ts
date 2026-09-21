/**
 * Arbitrage arithmetic on two posted prices.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). It reads no rows, no projections and no
 * odds feed; it is a function of the two numbers the reader types in.
 *
 * WHY THIS AND NOT AN ARB FINDER. A finder scans live prices across books and
 * was scratched by the client in August on the cadence argument, which still
 * holds: arbs last minutes and the odds budget affords a refresh every six
 * hours, so a list of "current" arbs here would be a list of arbs that closed
 * hours ago. That is worse than no feature — it invites a reader to stake money
 * on a stale price. A calculator makes no claim about what is available now.
 *
 * WHERE THIS SITS AGAINST CLAUDE.md §10, which puts "betting, bankroll, or
 * automated wagering" out of scope: this is arithmetic the reader drives, with
 * no wager placed, nothing stored and no recommendation made. It is the same
 * kind of object as the no-vig page — a statement about prices. The stake split
 * is the part nearest the line, and it is here because the client asked for a
 * calculator by name; it is one page and one module, and removing it is a
 * delete rather than an unpicking.
 *
 * THE VIG AND THE ARB ARE THE SAME NUMBER SEEN FROM TWO SIDES, which is why
 * this module and the no-vig page speak the same language. Two prices whose
 * implied probabilities sum to more than 1 carry a hold: the book's margin.
 * Sum to less than 1 and the margin is negative, which is an arb. The no-vig
 * page reports the first case across every quote on the slate; this reports the
 * second for a pair the reader has found.
 */

/** Decimal (European) odds from American. */
export function americanToDecimal(price: number): number {
  if (price < 0) return 1 + 100 / -price;
  return 1 + price / 100;
}

/**
 * Raw, vig-inclusive implied probability from American odds.
 *
 * DELIBERATELY THE VIGGED NUMBER. De-vigging asks what the market believes;
 * an arb asks what the prices PAY, and that is the raw figure. Stripping the
 * vig here would make every pair sum to exactly 1 and no arb could ever be
 * detected — the margin is the whole signal.
 *
 * Mirrors `american_to_implied_probability` in `worker/core/probability.py`.
 * Two implementations of four lines, in two languages, verified against each
 * other by the tests rather than shared.
 */
export function americanToImpliedProbability(price: number): number {
  if (price < 0) return -price / (-price + 100);
  return 100 / (price + 100);
}

/** American odds from a decimal price, for showing the break-even quote. */
export function decimalToAmerican(decimal: number): number {
  if (decimal >= 2) return Math.round((decimal - 1) * 100);
  return Math.round(-100 / (decimal - 1));
}

export type ArbitrageInput = {
  /** American price on one side, at one book. */
  priceA: number;
  /** American price on the other side, usually at a different book. */
  priceB: number;
  /** Total amount to put across both sides. */
  stake: number;
};

export type ArbitrageResult = {
  decimalA: number;
  decimalB: number;
  /** The two raw implied probabilities summed. Below 1 is an arb. */
  impliedSum: number;
  /**
   * Signed margin. POSITIVE is the book's hold (no arb); NEGATIVE is the
   * reader's edge. Same quantity the no-vig page prints as "Hold".
   */
  margin: number;
  isArbitrage: boolean;
  /** Guaranteed return on the whole stake, when this is an arb. */
  returnOnStake: number;
  /** The ideal, unrounded split. */
  exactStakeA: number;
  exactStakeB: number;
  /**
   * The split rounded to whole cents, which is what a reader can actually
   * place, and the two payouts THAT split produces.
   *
   * WHY ROUNDING GETS ITS OWN FIELDS. An arb is a guarantee only at the exact
   * split; rounding moves each payout a little, and on a thin arb it can move
   * one of them below the stake. `worstPayout` is the number that decides
   * whether the guarantee survived contact with two-decimal-place money, and no
   * calculator that prints only the ideal split can tell the reader that.
   */
  stakeA: number;
  stakeB: number;
  payoutA: number;
  payoutB: number;
  /** The smaller of the two payouts. Below `stake`, the rounded split loses. */
  worstPayout: number;
  /** Profit in the worse of the two outcomes. Negative means it can lose. */
  worstProfit: number;
};

/** Round to whole cents, away from zero, the way money rounds. */
function toCents(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * The full arithmetic for one pair of prices.
 *
 * NO GUARD ON `isArbitrage` — the numbers are returned either way, because the
 * common case is a reader checking a pair that turns out NOT to be an arb, and
 * "how far off was it" is the useful answer. The page says which case it is
 * rather than blanking the result.
 */
export function arbitrage({
  priceA,
  priceB,
  stake,
}: ArbitrageInput): ArbitrageResult {
  const decimalA = americanToDecimal(priceA);
  const decimalB = americanToDecimal(priceB);

  // From the DECIMAL prices, not by summing `americanToImpliedProbability`.
  // The two agree to floating-point noise, and 1/decimal is the form the stake
  // split below is derived from, so using one source keeps the split and the
  // verdict from ever disagreeing at the boundary.
  const invA = 1 / decimalA;
  const invB = 1 / decimalB;
  const impliedSum = invA + invB;

  const exactStakeA = (stake * invA) / impliedSum;
  const exactStakeB = (stake * invB) / impliedSum;

  const stakeA = toCents(exactStakeA);
  const stakeB = toCents(exactStakeB);

  const payoutA = toCents(stakeA * decimalA);
  const payoutB = toCents(stakeB * decimalB);
  const worstPayout = Math.min(payoutA, payoutB);

  return {
    decimalA,
    decimalB,
    impliedSum,
    margin: impliedSum - 1,
    isArbitrage: impliedSum < 1,
    returnOnStake: 1 / impliedSum - 1,
    exactStakeA,
    exactStakeB,
    stakeA,
    stakeB,
    payoutA,
    payoutB,
    worstPayout,
    worstProfit: worstPayout - stake,
  };
}

/**
 * The price the other side must beat for a pair to become an arb.
 *
 * Given one price, the break-even decimal on the other side is
 * `1 / (1 - 1/decimalKnown)`; anything longer than that is an arb. This is what
 * turns "not an arb" into an actionable number — the reader learns what to wait
 * for rather than only that today's pair does not work.
 *
 * THE ANSWER CAN BE AN ABSURD PRICE, AND THAT IS A REAL ANSWER. A heavy
 * favourite at -2000 needs better than +2000 on the other side, which no book
 * posts; the number is still correct and the PAGE decides how to say "not
 * realistically". Clamping here would bury a true result under a policy
 * judgement that belongs in the view.
 *
 * NULL IS ONLY FOR A NON-FINITE RESULT — a NaN input, or an implied probability
 * of exactly 1, which American odds approach but never reach. It is a guard,
 * not a case a reader can produce.
 */
export function breakEvenOtherSide(price: number): number | null {
  const inv = 1 / americanToDecimal(price);
  if (!(inv < 1)) return null;
  const decimal = 1 / (1 - inv);
  if (!Number.isFinite(decimal)) return null;
  return decimalToAmerican(decimal);
}
