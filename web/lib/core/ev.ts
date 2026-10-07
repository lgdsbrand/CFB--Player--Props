/**
 * Price-based +EV wagers on game lines (migration 0087).
 *
 * SPORT-AGNOSTIC CORE: rows in, display lists out. The worker computes each
 * wager when it captures the odds (worker/core/ev.py): a book's price beating
 * Pinnacle's power-de-vigged fair price at the same line. Nothing here reads
 * the game model; this is arithmetic on posted prices, true or false
 * whatever the model thinks.
 *
 * EXCHANGES ARE KEPT APART. Kalshi, Novig, ProphetX, Polymarket and the rest
 * post prices before their fees, so their EV is an upper bound, not a figure
 * to set beside a sportsbook's. Measured 2026-10-03, nearly every +EV price
 * over 2% was on one of them.
 */

import {
  spreadSideLabel,
  totalSideLabel,
  type MarketRole,
  type OddsMarket,
} from "./game-lines.ts";

export type EvSide = "home" | "away" | "over" | "under";

export interface EvWager {
  gameId: number;
  market: OddsMarket;
  side: EvSide;
  /** Home-perspective spread or the total; null for a moneyline. */
  line: number | null;
  price: number;
  fairProb: number;
  ev: number;
  bookKey: string;
  bookName: string;
  role: MarketRole;
  capturedAt: string;
}

/** The EV a wager is highlighted at, and the floor for the slate list. */
export const EV_HIGHLIGHT = 0.02;

export function isExchange(wager: Pick<EvWager, "role">): boolean {
  return wager.role === "exchange";
}

/** "TROY -10.0", "Over 51.5", "TROY ML". */
export function wagerLabel(wager: EvWager, home: string, away: string): string {
  if (wager.market === "h2h") {
    return `${wager.side === "home" ? home : away} ML`;
  }
  if (wager.line === null) return wager.side;
  if (wager.market === "totals") {
    return totalSideLabel(wager.line, wager.side === "over" ? "first" : "second");
  }
  return spreadSideLabel(wager.line, wager.side === "home" ? "first" : "second", home, away);
}

/** A fair probability as an American price: 0.6 -> -150, 0.4 -> +150. */
export function fairAmerican(probability: number): number {
  if (probability >= 0.5) return -Math.round((100 * probability) / (1 - probability));
  return Math.round((100 * (1 - probability)) / probability);
}

/** Best first, sportsbooks and exchanges apart; ties go to the better price. */
export function splitWagers(wagers: EvWager[]): { books: EvWager[]; exchanges: EvWager[] } {
  const sorted = [...wagers].sort((a, b) => b.ev - a.ev || b.price - a.price);
  return {
    books: sorted.filter((w) => !isExchange(w)),
    exchanges: sorted.filter(isExchange),
  };
}
