/**
 * The client's NFL fantasy game model on the game page (phase F3).
 *
 * SPORT-AGNOSTIC CORE. The model's stored projection (`fantasy_game_projections`,
 * migration 0094) and the game's current book prices in; what the panel shows
 * out. Nothing here re-runs the formula: the worker owns it
 * (worker/core/fantasy_model.py).
 *
 * HIS NUMBERS, HIS TERMS (user, 2026-10-14). The win % is his raw formula,
 * 1 / (1 + e^(-0.23 x margin)), not calibrated. Spread and total edges are in
 * POINTS, model minus the book's line, the way his sheet's "Edge" column reads,
 * and a play is his own rule: spread 4+ points, total 6+ (`SPREAD_GAP`,
 * `TOTAL_GAP`, the worker's RULE_SPREAD_GAP / RULE_TOTAL_GAP). The moneyline
 * edge is the house definition (CLAUDE.md §6): his win % minus the book's
 * vig-free probability of the same team.
 *
 * ONE BOOK, THE SAME ONE THE PICKS USE: Pinnacle, then DraftKings, then
 * FanDuel, skipping a quote whose two prices are not a real two-way market
 * (worker `choose_quote` / `OVERROUND_RANGE`), so the panel and the frozen
 * pick are measured against the same book.
 */

import type { BookOdds, OddsMarket } from "./game-lines.ts";
import { devigFirst, impliedProbability } from "./game-lines.ts";

export type FantasyVersion = "fixed" | "sheet";
export const FANTASY_VERSIONS: readonly FantasyVersion[] = ["fixed", "sheet"];
export const DEFAULT_FANTASY_VERSION: FantasyVersion = "fixed";

/** The page is public, so the sheet's version is "Original", not "your sheet". */
export const FANTASY_VERSION_LABEL: Record<FantasyVersion, string> = {
  fixed: "Fixed",
  sheet: "Original",
};

/** His rule plays' gaps, in points. */
export const SPREAD_GAP = 4;
export const TOTAL_GAP = 6;

/** The worker's `PICK_BOOKS` and `OVERROUND_RANGE` (worker/core/game_picks.py). */
export const PICK_BOOKS = ["pinnacle", "draftkings", "fanduel"] as const;
const OVERROUND_RANGE: [number, number] = [0.98, 1.15];

export interface FantasyProjection {
  version: FantasyVersion;
  awayScore: number;
  homeScore: number;
  total: number;
  /** Home minus away, rounded to 0.1 as his sheet rounds it before the win %. */
  homeMargin: number;
  pHomeWin: number;
  awayFantasy: number;
  homeFantasy: number;
  inputsAsOfWeek: number;
  madeAt: string;
}

export type LineupSlot = "QB" | "RB" | "WR1" | "WR2" | "TE";
export const LINEUP_SLOTS: readonly LineupSlot[] = ["QB", "RB", "WR1", "WR2", "TE"];

export interface LineupEntry {
  teamId: number;
  slot: LineupSlot;
  playerName: string | null;
  fantasyPoints: number;
  /** "default": not on CBS's list, so his sheet's fallback points apply. */
  pointsSource: "cbs" | "default";
  pickedBy: "depth_chart" | "projection" | "none";
  /** Players passed over and why, e.g. "J. Smith (Out)". */
  skipped: string | null;
}

/** The two prices of a quote, first side first: home/away, or over/under. */
function twoWay(q: BookOdds): [number, number] | null {
  const [a, b] = q.market === "totals" ? [q.overPrice, q.underPrice] : [q.homePrice, q.awayPrice];
  return a === null || b === null ? null : [a, b];
}

function plausible([a, b]: [number, number]): boolean {
  const sum = impliedProbability(a) + impliedProbability(b);
  return sum >= OVERROUND_RANGE[0] && sum <= OVERROUND_RANGE[1];
}

/** The full-game quote the model is measured against: first usable book in PICK_BOOKS. */
export function chooseBook(odds: BookOdds[], market: OddsMarket): BookOdds | null {
  for (const key of PICK_BOOKS) {
    const q = odds.find(
      (o) =>
        o.period === "full" &&
        o.market === market &&
        o.sportsbookKey === key &&
        (market === "h2h" || o.line !== null),
    );
    const prices = q ? twoWay(q) : null;
    if (q && prices && plausible(prices)) return q;
  }
  return null;
}

export interface PointsEdge {
  /** Our number in the line's terms: the home team's spread, or the total. */
  modelLine: number;
  bookLine: number;
  /** Points between the two, always positive. */
  gap: number;
  /** The side our number points to: home/over is "first". */
  side: "first" | "second";
  price: number;
  /** His rule fires: spread 4+, total 6+. */
  isPlay: boolean;
  bookName: string;
}

const round1 = (x: number) => Math.round(x * 10) / 10;

/** The spread or total edge in points against one quote, or null when unpriced. */
export function pointsEdge(
  projection: FantasyProjection,
  quote: BookOdds | null,
  market: "spreads" | "totals",
): PointsEdge | null {
  const prices = quote ? twoWay(quote) : null;
  if (!quote || quote.line === null || !prices) return null;
  const modelLine =
    market === "spreads" ? round1(-projection.homeMargin) : round1(projection.total);
  const first = market === "spreads" ? modelLine < quote.line : modelLine > quote.line;
  const gap = round1(Math.abs(modelLine - quote.line));
  return {
    modelLine,
    bookLine: quote.line,
    gap,
    side: first ? "first" : "second",
    price: first ? prices[0] : prices[1],
    isPlay: gap >= (market === "spreads" ? SPREAD_GAP : TOTAL_GAP),
    bookName: quote.sportsbookName,
  };
}

export interface MoneylineEdge {
  side: "home" | "away";
  /** His raw win % for that team. */
  modelProb: number;
  /** The book's vig-free probability for the same team. */
  bookProb: number;
  edge: number;
  price: number;
  bookName: string;
}

/** His win % for the team he favours, against the book's vig-free price. */
export function moneylineEdge(
  projection: FantasyProjection,
  quote: BookOdds | null,
): MoneylineEdge | null {
  const prices = quote ? twoWay(quote) : null;
  if (!quote || !prices) return null;
  const bookHome = devigFirst(prices[0], prices[1]);
  const home = projection.pHomeWin >= 0.5;
  const modelProb = home ? projection.pHomeWin : 1 - projection.pHomeWin;
  const bookProb = home ? bookHome : 1 - bookHome;
  return {
    side: home ? "home" : "away",
    modelProb,
    bookProb,
    edge: modelProb - bookProb,
    price: home ? prices[0] : prices[1],
    bookName: quote.sportsbookName,
  };
}

/** A team's five starters in slot order, and their total as his sheet sums it. */
export function teamLineup(
  entries: LineupEntry[],
  teamId: number,
): { slots: LineupEntry[]; total: number } {
  const slots = LINEUP_SLOTS.map((slot) =>
    entries.find((e) => e.teamId === teamId && e.slot === slot),
  ).filter((e): e is LineupEntry => e !== undefined);
  return { slots, total: round1(slots.reduce((sum, e) => sum + e.fantasyPoints, 0)) };
}
