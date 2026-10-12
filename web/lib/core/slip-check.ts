/**
 * The slip checker (client, 2026-10-12, after nhlpropking.com/bet-slip-check):
 * choose a book, add any legs it prices, and see what the model makes of each
 * leg and of the slip as a whole.
 *
 * SPORT-AGNOSTIC CORE. Pure: the week's catalogue of priced bets in, labels,
 * probabilities and prices out. The catalogue is read by
 * `lib/data/slip-check.ts`; the legs are the ordinary bet slip's
 * (`lib/core/slip.ts`), kept in the browser.
 *
 * WHICH PROBABILITY, decided with the user 2026-10-12:
 * - Props: the published, calibrated probability (PROP_CALIBRATION_SLOPE) at
 *   the BOOK'S line. At the model's own line it is the stored number; at any
 *   other line it is read off the projection's ladder of calibrated rungs,
 *   interpolated between the two nearest. Outside the ladder there is no
 *   number rather than a guess.
 * - Full-game spreads and totals: the calibrated probability the games table
 *   uses (CALIBRATION_SLOPE in worker/core/game_picks.py). Spreads sit at
 *   about 50% because that is what the backtest supports; the checker says so.
 * - Moneylines: the win chance pulled toward the book's price
 *   (ML_BLEND_WEIGHTS), the one the rule plays show (CLAUDE.md §11).
 * - First half and first quarter: no model number. Those probabilities were
 *   never calibrated.
 *
 * NO GRADE. The checker reports numbers and one plain verdict per leg. The
 * props model has not beaten closing lines, so a letter grade would claim
 * more than the record does.
 */

import { americanToDecimal, decimalToAmerican, hasSameGameLegs, legKey, type SlipLeg } from "./slip.ts";
import { devigFirst, type ModelPeriod, type OddsMarket } from "./game-lines.ts";
import type { LadderRung } from "./types.ts";

// ---------------------------------------------------------------- catalogue

/** One side of a priced bet: the leg it adds and each book's price on it. */
export interface CheckSide {
  leg: SlipLeg;
  /** "Over 62.5", "UGA -3.5", "UGA ML", "Yes". */
  label: string;
  prices: Record<string, number>;
}

/**
 * The model's view of a bet, for its FIRST side (over, home):
 * a probability, the moneyline's raw home win chance (blended per book at
 * evaluation, because the blend needs that book's price), or nothing.
 */
export type CheckModel =
  | { kind: "prob"; first: number }
  | { kind: "h2h"; rawHome: number }
  | null;

/** One bet at one line: a player's market or a game market. */
export interface CheckEntry {
  id: string;
  kind: "prop" | "game";
  gameId: number;
  startDate: string | null;
  /** "Drew Allar", or the matchup for a game line. */
  title: string;
  /** "Pass Yards", "Spread", "1H Total". */
  market: string;
  matchup: string;
  /** Lower-case text the search box matches. */
  search: string;
  /** One side for a one-way market (anytime TD), else two. */
  sides: CheckSide[];
  model: CheckModel;
}

export interface CheckCatalogue {
  sport: string;
  season: number;
  week: number;
  books: { key: string; name: string }[];
  entries: CheckEntry[];
}

// ---------------------------------------------------------------- props

/**
 * P(over `line`) from the model's known points: the stored probability at its
 * own line and the ladder's rungs. Exact where a point sits on the line,
 * interpolated between the nearest points either side, null outside them.
 */
export function propProbAtLine(
  point: { line: number; probOver: number } | null,
  rungs: LadderRung[],
  line: number,
): number | null {
  const points = [...rungs.map((r) => ({ line: r.line, probOver: r.probOver }))];
  if (point) points.push(point);
  points.sort((a, b) => a.line - b.line);
  const exact = point && Math.abs(point.line - line) < 1e-6 ? point : points.find((p) => Math.abs(p.line - line) < 1e-6);
  if (exact) return exact.probOver;
  for (let i = 0; i + 1 < points.length; i += 1) {
    const lo = points[i];
    const hi = points[i + 1];
    if (lo.line < line && line < hi.line) {
      const t = (line - lo.line) / (hi.line - lo.line);
      return lo.probOver + t * (hi.probOver - lo.probOver);
    }
  }
  return null;
}

// ---------------------------------------------------------------- games

/** Copied from worker/core/game_picks.py; change both together. */
export const GAME_CALIBRATION_SLOPE: Record<"spreads" | "totals", number> = {
  spreads: 0.0565,
  totals: 0.256,
};
/** Copied from worker/core/game_picks.py (model, book); change both together. */
export const ML_BLEND_WEIGHTS: readonly [number, number] = [0.073, 1.045];
const PROB_FLOOR = 1e-6;

const logit = (p: number) => {
  const q = Math.min(Math.max(p, PROB_FLOOR), 1 - PROB_FLOOR);
  return Math.log(q / (1 - q));
};
const sigmoid = (z: number) => 1 / (1 + Math.exp(-z));

/** The worker's `calibrate`: shrink in logit space, so 50% stays 50%. */
export function calibrateGame(pRaw: number, market: "spreads" | "totals"): number {
  return sigmoid(GAME_CALIBRATION_SLOPE[market] * logit(pRaw));
}

/** The worker's `blend_win_prob`: the home win chance pulled toward the book. */
export function blendWinProb(rawHome: number, bookHome: number): number {
  const [wModel, wBook] = ML_BLEND_WEIGHTS;
  return sigmoid(wModel * logit(rawHome) + wBook * logit(bookHome));
}

/** Standard normal CDF (Abramowitz & Stegun 7.1.26, error < 1.5e-7). */
export function normalCdf(z: number): number {
  const x = Math.abs(z) / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * x);
  const erf =
    1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return z >= 0 ? 0.5 * (1 + erf) : 0.5 * (1 - erf);
}

const Z90 = 1.2815515655;

/**
 * Calibrated probability of the FIRST side (home cover, over) of a full-game
 * spread or total at `line`. At the line the model priced it is the stored
 * number; elsewhere the projection's mean and 80% range give a normal, whose
 * raw probability is calibrated the same way. The worker simulates rather
 * than assuming a normal, but after a spread's 0.0565 shrink the difference
 * is a fraction of a point.
 *
 * `line` is the HOME spread (negative = home favoured) or the total.
 */
export function gameProbAtLine(
  market: "spreads" | "totals",
  range: { mean: number; p10: number; p90: number },
  line: number,
  priced: { line: number; modelFirstProb: number } | null,
): number {
  if (priced && Math.abs(priced.line - line) < 1e-6) return priced.modelFirstProb;
  const sd = Math.max((range.p90 - range.p10) / (2 * Z90), 1);
  // Home covers when margin + line > 0; over when total > line.
  const threshold = market === "spreads" ? -line : line;
  const raw = 1 - normalCdf((threshold - range.mean) / sd);
  return calibrateGame(raw, market);
}

// ---------------------------------------------------------------- evaluation

/** How far the model must sit from the book to "agree" or "disagree" (V2's bar). */
export const AGREE_EDGE = 0.02;

export type Verdict = "agrees" | "disagrees" | "close" | "no-model" | "not-priced" | "not-found";

export const VERDICT_LABEL: Record<Verdict, string> = {
  agrees: "Model agrees",
  disagrees: "Model disagrees",
  close: "Close to the book",
  "no-model": "No model number",
  "not-priced": "Not priced at this book",
  "not-found": "Not on this week's board",
};

export interface LegCheck {
  key: string;
  leg: SlipLeg;
  entry: CheckEntry | null;
  /** This book's price on the leg, or null. */
  price: number | null;
  /** The model's probability of this leg winning. */
  modelProb: number | null;
  /** The book's vig-free probability; null for a one-way price. */
  bookProb: number | null;
  /** Model minus book (CLAUDE.md §6); null without both. */
  edge: number | null;
  /** Expected return per unit at this price: modelProb x decimal - 1. */
  value: number | null;
  /** The price the model's probability is worth. */
  fairPrice: number | null;
  verdict: Verdict;
}

/** Index the catalogue by each side's leg key. */
export function indexCatalogue(entries: CheckEntry[]): Map<string, { entry: CheckEntry; index: number }> {
  const out = new Map<string, { entry: CheckEntry; index: number }>();
  for (const entry of entries) {
    entry.sides.forEach((side, index) => out.set(legKey(side.leg), { entry, index }));
  }
  return out;
}

export function fairAmerican(prob: number): number | null {
  if (!(prob > 0 && prob < 1)) return null;
  return decimalToAmerican(1 / prob);
}

export function checkLeg(
  leg: SlipLeg,
  found: { entry: CheckEntry; index: number } | undefined,
  book: string,
): LegCheck {
  const key = legKey(leg);
  if (!found) {
    return {
      key, leg, entry: null, price: null, modelProb: null, bookProb: null,
      edge: null, value: null, fairPrice: null, verdict: "not-found",
    };
  }
  const { entry, index } = found;
  const price = entry.sides[index].prices[book] ?? null;
  const otherSide = entry.sides.length === 2 ? entry.sides[1 - index] : null;
  const otherPrice = otherSide?.prices[book] ?? null;
  const bookProb = price !== null && otherPrice !== null ? devigFirst(price, otherPrice) : null;

  let modelProb: number | null = null;
  if (entry.model?.kind === "prob") {
    modelProb = index === 0 ? entry.model.first : 1 - entry.model.first;
  } else if (entry.model?.kind === "h2h" && bookProb !== null) {
    const bookHome = index === 0 ? bookProb : 1 - bookProb;
    const home = blendWinProb(entry.model.rawHome, bookHome);
    modelProb = index === 0 ? home : 1 - home;
  }

  const edge = modelProb !== null && bookProb !== null ? modelProb - bookProb : null;
  const value = modelProb !== null && price !== null ? modelProb * americanToDecimal(price) - 1 : null;
  let verdict: Verdict;
  if (price === null) verdict = "not-priced";
  else if (modelProb === null) verdict = "no-model";
  else {
    // The §6 edge where the book prices both sides; a one-way price (anytime
    // TD) has no vig-free probability, so the value at its price decides.
    const gap = edge ?? value ?? 0;
    verdict = gap >= AGREE_EDGE ? "agrees" : gap <= -AGREE_EDGE ? "disagrees" : "close";
  }
  return {
    key, leg, entry, price, modelProb, bookProb, edge, value,
    fairPrice: modelProb === null ? null : fairAmerican(modelProb), verdict,
  };
}

export interface SlipCheck {
  legs: LegCheck[];
  /** Why there is no combined number, or null when there is one. */
  blocked: "empty" | "same-game" | "no-model" | null;
  /** Chance every leg wins, as if independent. */
  modelProb: number | null;
  /** The same from the book's vig-free numbers, where every leg has one. */
  bookProb: number | null;
  fairPrice: number | null;
  /** This book's parlay price, every leg multiplied; null if a leg is unpriced or two share a game. */
  bookPrice: number | null;
  /** Expected return per unit at the book's parlay price. */
  value: number | null;
}

export function checkSlip(
  legs: SlipLeg[],
  index: Map<string, { entry: CheckEntry; index: number }>,
  book: string,
): SlipCheck {
  const checks = legs.map((leg) => checkLeg(leg, index.get(legKey(leg)), book));
  // A book prices a same-game parlay itself (often refusing it), so the
  // multiplied price would be a number the book does not offer.
  const priced = checks.every((c) => c.price !== null) && !hasSameGameLegs(legs);
  const bookDecimal = priced ? checks.reduce((acc, c) => acc * americanToDecimal(c.price as number), 1) : null;
  const bookPrice = bookDecimal !== null && checks.length > 1 ? decimalToAmerican(bookDecimal) : null;

  let blocked: SlipCheck["blocked"] = null;
  if (checks.length === 0) blocked = "empty";
  else if (hasSameGameLegs(legs)) blocked = "same-game";
  else if (checks.some((c) => c.modelProb === null)) blocked = "no-model";

  const modelProb = blocked ? null : checks.reduce((acc, c) => acc * (c.modelProb as number), 1);
  const bookProb =
    blocked || checks.some((c) => c.bookProb === null)
      ? null
      : checks.reduce((acc, c) => acc * (c.bookProb as number), 1);
  return {
    legs: checks,
    blocked,
    modelProb,
    bookProb,
    fairPrice: modelProb === null ? null : fairAmerican(modelProb),
    bookPrice,
    value: modelProb !== null && bookDecimal !== null ? modelProb * bookDecimal - 1 : null,
  };
}

/** Books for the picker, most priced bets first, with the count. */
export function bookCounts(catalogue: Pick<CheckCatalogue, "books" | "entries">): { key: string; name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const entry of catalogue.entries) {
    const books = new Set(entry.sides.flatMap((s) => Object.keys(s.prices)));
    for (const book of books) counts.set(book, (counts.get(book) ?? 0) + 1);
  }
  return catalogue.books
    .map((b) => ({ ...b, count: counts.get(b.key) ?? 0 }))
    .filter((b) => b.count > 0)
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

/**
 * Entries at one book matching every word typed, games before kickoff only.
 * Props and game lines both; at most `limit`, kickoff order.
 */
export function searchCatalogue(
  entries: CheckEntry[],
  book: string,
  query: string,
  now: Date,
  limit = 40,
): CheckEntry[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const out: CheckEntry[] = [];
  for (const entry of entries) {
    if (entry.startDate && new Date(entry.startDate) <= now) continue;
    if (!entry.sides.some((s) => s.prices[book] !== undefined)) continue;
    if (!words.every((w) => entry.search.includes(w))) continue;
    out.push(entry);
    if (out.length >= limit) break;
  }
  return out;
}

// ---------------------------------------------------------------- sharing

const PERIODS: ModelPeriod[] = ["full", "h1", "q1"];
const MARKETS: OddsMarket[] = ["h2h", "spreads", "totals"];

/**
 * A slip in a URL: each leg's key, joined. The share image and a shared link
 * re-read the legs from the catalogue, so a URL cannot put numbers in our
 * name that the model did not produce.
 */
export function encodeSlipKeys(legs: SlipLeg[]): string {
  return legs.map(legKey).join("~");
}

export type LegRef =
  | { kind: "prop"; gameId: number; playerId: number; marketKey: string; side: "over" | "under"; line: number }
  | { kind: "game"; gameId: number; period: ModelPeriod; market: OddsMarket; side: "home" | "away" | "over" | "under"; line: number | null };

/** The inverse of `encodeSlipKeys`; malformed keys are dropped. */
export function decodeSlipKeys(raw: string | null | undefined, max = 12): string[] {
  if (!raw) return [];
  return raw
    .split("~")
    .filter((key) => parseLegKey(key) !== null)
    .slice(0, max);
}

export function parseLegKey(key: string): LegRef | null {
  const parts = key.split(":");
  if (parts[0] === "p" && parts.length === 6) {
    const [, game, player, marketKey, side, line] = parts;
    if (!/^\d+$/.test(game) || !/^\d+$/.test(player) || !/^[a-z0-9_]+$/.test(marketKey)) return null;
    if (side !== "over" && side !== "under") return null;
    const value = Number(line);
    if (line === "" || !Number.isFinite(value)) return null;
    return { kind: "prop", gameId: Number(game), playerId: Number(player), marketKey, side, line: value };
  }
  if (parts[0] === "g" && parts.length === 6) {
    const [, game, period, market, side, line] = parts;
    if (!/^\d+$/.test(game)) return null;
    if (!PERIODS.includes(period as ModelPeriod) || !MARKETS.includes(market as OddsMarket)) return null;
    if (!["home", "away", "over", "under"].includes(side)) return null;
    const value = line === "" ? null : Number(line);
    if (value !== null && !Number.isFinite(value)) return null;
    return {
      kind: "game", gameId: Number(game), period: period as ModelPeriod, market: market as OddsMarket,
      side: side as "home" | "away" | "over" | "under", line: value,
    };
  }
  return null;
}
