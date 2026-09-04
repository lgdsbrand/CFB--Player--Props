/**
 * The no-vig page's own logic.
 *
 * The page answers one question the rest of the product deliberately does not:
 * what is the BOOK charging, independent of whether our model is any good. So
 * nothing in this module reads a projection, a pick, an edge or a confidence,
 * and none of it should start to. The value of the page to the client is that
 * it is arithmetic on a posted price — true whether or not the model is
 * profitable, which is a claim two graded weeks did not support.
 *
 * De-vigging itself happens in SQL (`devig_two_way`, migration 0013, method
 * from `app_config.devig_method`), so there is no second implementation of it
 * here to drift from the first. What lives in this file is presentation logic
 * that has a right and a wrong answer: how to band a hold, how far a book sits
 * from its peers, and how to order a table.
 */

import type { PositionGroup } from "@/lib/core/types";

/** One book's two-way quote on one player-market, vig removed. */
export type NoVigRow = {
  lineId: number;
  season: number;
  week: number;
  gameId: number;
  startDate: string | null;
  playerId: number;
  playerName: string;
  positionGroup: PositionGroup | null;
  teamId: number;
  teamSchool: string;
  teamAbbreviation: string;
  teamColor: string | null;
  teamAltColor: string | null;
  isHome: boolean;
  opponentAbbreviation: string;
  opponentSchool: string;
  conferenceName: string | null;
  marketKey: string;
  marketLabel: string;
  marketEmoji: string | null;
  line: number;
  sportsbookKey: string;
  sportsbookName: string;
  overPrice: number;
  underPrice: number;
  /** The book's margin: both raw implied probabilities summed, minus one. */
  hold: number;
  fairProbOver: number;
  fairProbUnder: number;
  fairPriceOver: number | null;
  fairPriceUnder: number | null;
  /** Books posting THIS line for this prop, including this one. */
  booksAtLine: number;
  /** Books posting any line for this prop. */
  booksOnMarket: number;
  /** Distinct lines across those books. Above one means they disagree. */
  linesOnMarket: number;
  /** Mean fair probability among the books at this same line. */
  consensusProbOver: number;
  /** Lowest and highest fair probability among those same books. With a mean
   *  consensus rather than a median, the spread is what says whether the
   *  average is describing agreement or hiding an outlier. */
  lineProbOverMin: number;
  lineProbOverMax: number;
  isBestOver: boolean;
  isBestUnder: boolean;
  capturedAt: string;
};

/**
 * How expensive this quote is, in bands.
 *
 * The boundaries are measured, not conventional. Across every two-way quote
 * stored for 2026 week 1 the hold ran 4.75% to 7.94% with a 6.59% average, so
 * on this market 5% really is a keen price and 8% really is a dear one. A band
 * table copied from a US major-league article would put all of college football
 * in one bucket and tell the reader nothing.
 *
 * `wide` starts at 9% rather than at the observed maximum: the point of the
 * band is to catch the quote that is worse than anything seen so far, and a
 * ceiling set to today's worst row can never fire.
 */
export type HoldBand = "keen" | "typical" | "dear" | "wide";

export function holdBand(hold: number): HoldBand {
  if (hold < 0.05) return "keen";
  if (hold < 0.07) return "typical";
  if (hold < 0.09) return "dear";
  return "wide";
}

/**
 * How far this book's fair probability sits from its peers', in probability
 * points, signed toward the over.
 *
 * NULL when it is the only book at that line, and that is not the same as zero:
 * zero says the book agrees with the market, null says there is no market to
 * agree with. A page that renders them the same way turns "nobody else has
 * posted this" into "everybody agrees", which is the more confident claim and
 * the false one.
 */
export function consensusDelta(row: NoVigRow): number | null {
  if (row.booksAtLine < 2) return null;
  return row.fairProbOver - row.consensusProbOver;
}

/**
 * Whether the books disagree about the NUMBER rather than about the price.
 *
 * Three books at three different lines is not thin coverage — it is three
 * markets of one book each, and every comparison column is scoped to one line
 * for that reason. The page says which of the two it is looking at.
 */
export function booksDisagreeOnLine(row: NoVigRow): boolean {
  return row.linesOnMarket > 1;
}

/** The orderings the page offers. */
export type NoVigSort = "hold" | "player" | "market" | "consensus";

export const NO_VIG_SORTS: readonly NoVigSort[] = [
  "hold",
  "player",
  "market",
  "consensus",
] as const;

export function parseNoVigSort(value: string | undefined): NoVigSort {
  return NO_VIG_SORTS.includes(value as NoVigSort) ? (value as NoVigSort) : "hold";
}

/**
 * Compare two rows for the chosen ordering.
 *
 * `hold` ascends: the cheapest quote on the slate is the interesting one, and a
 * table that opens on the most expensive would read as a list of bad prices.
 * `consensus` sorts by how far a book sits from its peers, largest gap first,
 * with single-book rows last — they have no gap to sort on and floating them to
 * the top on a null would be the "unranked reads as extreme" trap the weekly
 * targets panel already hit.
 */
export function compareNoVigRows(a: NoVigRow, b: NoVigRow, sort: NoVigSort): number {
  switch (sort) {
    case "hold":
      return a.hold - b.hold || byPlayerThenMarket(a, b);
    case "player":
      return byPlayerThenMarket(a, b);
    case "market":
      return (
        a.marketKey.localeCompare(b.marketKey) ||
        a.hold - b.hold ||
        byPlayerThenMarket(a, b)
      );
    case "consensus": {
      const da = consensusDelta(a);
      const db = consensusDelta(b);
      if (da === null && db === null) return byPlayerThenMarket(a, b);
      if (da === null) return 1;
      if (db === null) return -1;
      return Math.abs(db) - Math.abs(da) || byPlayerThenMarket(a, b);
    }
  }
}

/**
 * The tiebreak chain, ending on a UNIQUE column.
 *
 * `lineId` last is not decoration. `lib/data/no-vig.ts` ends every ORDER BY on
 * `line_id` so a page is reproducible, and if this comparator stops short of a
 * unique key the two disagree the moment two quotes tie: the database picks
 * which rows survive the row cap, this picks the order they are shown in, and
 * the reader sees a page that reshuffles between requests. The board shipped
 * exactly that bug once already — a tie in the game-log sort made a hit rate
 * nondeterministic.
 */
function byPlayerThenMarket(a: NoVigRow, b: NoVigRow): number {
  return (
    a.playerName.localeCompare(b.playerName) ||
    a.marketKey.localeCompare(b.marketKey) ||
    a.line - b.line ||
    a.sportsbookName.localeCompare(b.sportsbookName) ||
    a.lineId - b.lineId
  );
}

/**
 * Summary of what a page of rows contains, for the header line.
 *
 * `medianHold` rather than a mean: one stale quote at 20% moves an average of a
 * few hundred rows and the header would misdescribe the slate it sits above.
 */
export type NoVigSummary = {
  quotes: number;
  playerMarkets: number;
  books: number;
  medianHold: number | null;
  shoppable: number;
};

export function summarise(rows: readonly NoVigRow[]): NoVigSummary {
  const playerMarkets = new Set<string>();
  const books = new Set<string>();
  const holds: number[] = [];
  let shoppable = 0;

  for (const row of rows) {
    playerMarkets.add(`${row.playerId}:${row.marketKey}`);
    books.add(row.sportsbookKey);
    holds.push(row.hold);
    if (row.booksAtLine > 1) shoppable += 1;
  }

  return {
    quotes: rows.length,
    playerMarkets: playerMarkets.size,
    books: books.size,
    medianHold: median(holds),
    shoppable,
  };
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}
