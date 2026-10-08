/**
 * The bet builder (client, 2026-10-08): the reader sets the odds they want to
 * end on and how good each pick must be, and a slip is built for them.
 *
 * His reference was nerdytips.com/bet-builder ("person puts in like odds they
 * want to end with, hit rate or confidence range for each pick") and Gambly's
 * filters (odds range per pick, bet types, over/under, sportsbook).
 *
 * SPORT-AGNOSTIC CORE. Pure: candidates and settings in, a slip out. The pool
 * comes from `lib/data/builder.ts`; the slip it builds is handed to the
 * ordinary bet slip, which prices it at every book and links out.
 *
 * WHAT A CANDIDATE IS. One bet on the side the MODEL takes: the board's call
 * for a prop, "Yes" for anytime TD (books price it one way), and the side the
 * games table shows an edge on for a spread or total. The builder never bets
 * against its own model; a reader who wants the streak's side has the cheat
 * sheets.
 *
 * ONE BOOK. A parlay is placed at one book, so every leg is priced at the
 * chosen book and a leg it does not offer at that line is not a candidate.
 *
 * THE "ALL HIT" CHANCE multiplies the legs' model probabilities as if they
 * were independent. Legs from one game are not, which is why one leg per game
 * is the default.
 */

import {
  americanToDecimal,
  decimalToAmerican,
  legKey,
  type SlipLeg,
} from "./slip.ts";

export interface BuilderCandidate {
  leg: SlipLeg;
  /** The prop's player; null for a game line. */
  playerId: number | null;
  /** A prop market key, or "spreads" / "totals" for a game line. */
  marketKey: string;
  /** The model's probability that this leg wins. */
  prob: number;
  /** Model minus de-vigged book probability; null where there is none (anytime TD). */
  edge: number | null;
  /** Games in the hit-rate window that landed on THIS leg's side; null for game lines. */
  hits: number | null;
  /** Games in the window that resolved either way. */
  decided: number | null;
  /** American price at this leg's line and side, by book key. */
  prices: Record<string, number>;
}

export interface BuilderSettings {
  book: string;
  /** American odds to end on; null builds without a target. */
  targetOdds: number | null;
  /** Number of picks, or "auto" (2 to 5). */
  legs: number | "auto";
  /** Floor on each pick's model probability, 0-1. */
  minProb: number;
  /** Floor on each pick's hit rate, 0-1; null for no floor. */
  minHitRate: number | null;
  /** Decided games a hit rate needs before it counts (see `minDecidedFor`). */
  minDecided: number;
  /** American price range each pick must sit in. */
  minPrice: number;
  maxPrice: number;
  /** Market keys to use; empty is every market. */
  markets: string[];
  /** Over or under only (props and totals); null is both. */
  side: "over" | "under" | null;
  /** Only picks whose edge clears `edgeThreshold`. */
  edgesOnly: boolean;
  edgeThreshold: number;
  /** Only these games; empty is every game. */
  gameIds: number[];
  /** Allow two picks from one game. */
  sameGame: boolean;
}

export const AUTO_LEGS: readonly number[] = [2, 3, 4, 5];
/** Without a target, "auto" builds this many. */
export const DEFAULT_LEGS = 3;
export const MAX_BUILDER_LEGS = 8;
/** How close to the target counts as hitting it, as a share of the decimal price. */
export const TARGET_TOLERANCE = 0.1;
/**
 * How far a shuffle may move a pick's probability, either way, half this. On
 * 2026 week 6 the confident picks sit within ~25 points of each other, and a
 * smaller jitter kept handing back the same slip.
 */
const SHUFFLE_JITTER = 0.2;
/** Shuffle seeds tried before accepting a slip that repeats the last one. */
export const SHUFFLE_TRIES = 20;
/** Candidates the search looks at, best first. */
const SEARCH_DEPTH = 200;

/** The leg's hit rate on its own side, or null without enough games. */
export function legHitRate(
  candidate: Pick<BuilderCandidate, "hits" | "decided">,
  minDecided: number,
): number | null {
  if (candidate.hits === null || candidate.decided === null) return null;
  if (candidate.decided < Math.max(1, minDecided)) return null;
  return candidate.hits / candidate.decided;
}

function legSide(leg: SlipLeg): string {
  return leg.side;
}

/** Whether a candidate passes every per-pick setting. */
export function passes(
  candidate: BuilderCandidate,
  settings: BuilderSettings,
): boolean {
  const price = candidate.prices[settings.book];
  if (price === undefined) return false;
  const decimal = americanToDecimal(price);
  if (decimal < americanToDecimal(settings.minPrice) - 1e-9) return false;
  if (decimal > americanToDecimal(settings.maxPrice) + 1e-9) return false;
  if (candidate.prob < settings.minProb) return false;
  if (settings.minHitRate !== null) {
    const rate = legHitRate(candidate, settings.minDecided);
    if (rate === null || rate < settings.minHitRate - 1e-9) return false;
  }
  if (settings.markets.length > 0 && !settings.markets.includes(candidate.marketKey)) {
    return false;
  }
  if (settings.side !== null && legSide(candidate.leg) !== settings.side) return false;
  if (settings.edgesOnly && (candidate.edge === null || candidate.edge < settings.edgeThreshold)) {
    return false;
  }
  if (settings.gameIds.length > 0 && !settings.gameIds.includes(candidate.leg.gameId)) {
    return false;
  }
  return true;
}

/** Every candidate that passes, best first. */
export function eligible(
  candidates: BuilderCandidate[],
  settings: BuilderSettings,
): BuilderCandidate[] {
  return candidates.filter((c) => passes(c, settings)).sort(compareCandidates);
}

/** Model probability first, then hit rate, then edge; the leg key keeps it stable. */
export function compareCandidates(a: BuilderCandidate, b: BuilderCandidate): number {
  if (a.prob !== b.prob) return b.prob - a.prob;
  const rateA = a.decided ? (a.hits ?? 0) / a.decided : -1;
  const rateB = b.decided ? (b.hits ?? 0) / b.decided : -1;
  if (rateA !== rateB) return rateB - rateA;
  const edgeA = a.edge ?? -1;
  const edgeB = b.edge ?? -1;
  if (edgeA !== edgeB) return edgeB - edgeA;
  return legKey(a.leg).localeCompare(legKey(b.leg));
}

export interface BuiltPick {
  candidate: BuilderCandidate;
  price: number;
}

export interface BuiltSlip {
  picks: BuiltPick[];
  decimal: number;
  american: number;
  /** Product of the picks' model probabilities: independence assumed. */
  allHitProb: number;
  /** Within `TARGET_TOLERANCE` of the target, or no target was set. */
  onTarget: boolean;
  sameGame: boolean;
}

/** Small seeded PRNG, so a shuffle can be reproduced in a test. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * The search order. Seed 0 is strictly best first; any other seed jitters the
 * probabilities a little, so "Shuffle" finds a different slip of near-equal
 * quality rather than a random one.
 */
function searchOrder(pool: BuilderCandidate[], seed: number): BuilderCandidate[] {
  if (seed === 0) return pool.slice(0, SEARCH_DEPTH);
  const random = mulberry32(seed);
  return pool
    .slice(0, SEARCH_DEPTH)
    .map((candidate) => ({ candidate, score: candidate.prob + (random() - 0.5) * SHUFFLE_JITTER }))
    .sort((a, b) => b.score - a.score)
    .map((entry) => entry.candidate);
}

/** Whether `candidate` may join `chosen`: one pick per player, and per game unless allowed. */
function fits(
  candidate: BuilderCandidate,
  chosen: BuilderCandidate[],
  sameGame: boolean,
): boolean {
  return chosen.every((other) => {
    if (other === candidate) return false;
    if (candidate.playerId !== null && candidate.playerId === other.playerId) return false;
    if (candidate.leg.gameId === other.leg.gameId) {
      if (!sameGame) return false;
      // Never both sides or two lines of one game market.
      if (candidate.playerId === null && other.playerId === null &&
          candidate.marketKey === other.marketKey) {
        return false;
      }
    }
    return true;
  });
}

function logDecimal(candidate: BuilderCandidate, book: string): number {
  return Math.log(americanToDecimal(candidate.prices[book]));
}

/**
 * The best `n` picks from `order`, then swapped toward the target.
 *
 * Greedy first: the n strongest picks that fit together. With a target, each
 * round makes the one swap that lands inside the tolerance with the strongest
 * incoming pick, or failing that the swap that gets closest, and stops once
 * on target or when no swap helps. A handful of picks from a few hundred
 * candidates does not need anything cleverer, and this stays well under a
 * frame in the browser.
 */
function buildFixed(
  order: BuilderCandidate[],
  n: number,
  settings: BuilderSettings,
): BuilderCandidate[] | null {
  const chosen: BuilderCandidate[] = [];
  for (const candidate of order) {
    if (chosen.length === n) break;
    if (fits(candidate, chosen, settings.sameGame)) chosen.push(candidate);
  }
  if (chosen.length < n) return null;
  if (settings.targetOdds === null) return chosen;

  const goal = Math.log(americanToDecimal(settings.targetOdds));
  const tolerance = Math.log(1 + TARGET_TOLERANCE);
  let total = chosen.reduce((acc, c) => acc + logDecimal(c, settings.book), 0);

  for (let round = 0; round < 4 * n + 20; round += 1) {
    const miss = Math.abs(goal - total);
    if (miss <= tolerance) break;
    let best: { at: number; with: BuilderCandidate; total: number; inside: boolean } | null = null;
    for (let at = 0; at < chosen.length; at += 1) {
      const rest = chosen.filter((_, i) => i !== at);
      const without = total - logDecimal(chosen[at], settings.book);
      for (const candidate of order) {
        if (chosen.includes(candidate) || !fits(candidate, rest, settings.sameGame)) continue;
        const next = without + logDecimal(candidate, settings.book);
        const nextMiss = Math.abs(goal - next);
        if (nextMiss >= miss) continue;
        const inside = nextMiss <= tolerance;
        const better =
          best === null ||
          (inside && !best.inside) ||
          (inside && best.inside && candidate.prob > best.with.prob) ||
          (!inside && !best.inside && nextMiss < Math.abs(goal - best.total));
        if (better) best = { at, with: candidate, total: next, inside };
      }
    }
    if (best === null) break;
    chosen[best.at] = best.with;
    total = best.total;
  }
  return chosen;
}

function summarise(chosen: BuilderCandidate[], settings: BuilderSettings): BuiltSlip {
  const picks = chosen.map((candidate) => ({
    candidate,
    price: candidate.prices[settings.book],
  }));
  const decimal = picks.reduce((acc, p) => acc * americanToDecimal(p.price), 1);
  const games = chosen.map((c) => c.leg.gameId);
  return {
    picks,
    decimal,
    american: decimalToAmerican(decimal),
    allHitProb: chosen.reduce((acc, c) => acc * c.prob, 1),
    onTarget:
      settings.targetOdds === null ||
      Math.abs(Math.log(decimal) - Math.log(americanToDecimal(settings.targetOdds))) <=
        Math.log(1 + TARGET_TOLERANCE) + 1e-9,
    sameGame: new Set(games).size !== games.length,
  };
}

/**
 * Build a slip, or null when too few picks pass the settings.
 *
 * With "auto" picks and a target, every size from 2 to 5 is tried and the
 * slip that reaches the target with the best chance of all hitting wins; one
 * that misses the target only wins when none reaches it, and then the
 * closest does. `seed` 0 is the best slip; other seeds are the shuffles.
 */
export function buildSlip(
  candidates: BuilderCandidate[],
  settings: BuilderSettings,
  seed = 0,
): BuiltSlip | null {
  const order = searchOrder(eligible(candidates, settings), seed);
  const sizes =
    settings.legs === "auto"
      ? settings.targetOdds === null
        ? [DEFAULT_LEGS]
        : [...AUTO_LEGS]
      : [Math.min(Math.max(1, settings.legs), MAX_BUILDER_LEGS)];

  let best: BuiltSlip | null = null;
  for (const n of sizes) {
    const chosen = buildFixed(order, n, settings);
    if (!chosen) continue;
    const built = summarise(chosen, settings);
    if (best === null || betterSlip(built, best, settings)) best = built;
  }
  return best;
}

function betterSlip(a: BuiltSlip, b: BuiltSlip, settings: BuilderSettings): boolean {
  if (a.onTarget !== b.onTarget) return a.onTarget;
  if (!a.onTarget && settings.targetOdds !== null) {
    const goal = Math.log(americanToDecimal(settings.targetOdds));
    return Math.abs(Math.log(a.decimal) - goal) < Math.abs(Math.log(b.decimal) - goal);
  }
  return a.allHitProb > b.allHitProb;
}

/** Books with candidates, most first: the book picker's order and default. */
export function booksByCoverage(
  candidates: BuilderCandidate[],
  names: Record<string, string>,
): { key: string; name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const candidate of candidates) {
    for (const book of Object.keys(candidate.prices)) {
      counts.set(book, (counts.get(book) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([key, count]) => ({ key, name: names[key] ?? key, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

/** The picks of a slip as one comparable key, order ignored. */
export function slipKey(slip: BuiltSlip | null): string {
  return slip ? slip.picks.map((p) => legKey(p.candidate.leg)).sort().join("|") : "";
}

/**
 * The next seed after `seed` whose slip differs from the one `seed` builds,
 * or simply the next seed when the pool allows no other slip.
 */
export function nextShuffleSeed(
  candidates: BuilderCandidate[],
  settings: BuilderSettings,
  seed: number,
): number {
  const current = slipKey(buildSlip(candidates, settings, seed));
  for (let next = seed + 1; next <= seed + SHUFFLE_TRIES; next += 1) {
    if (slipKey(buildSlip(candidates, settings, next)) !== current) return next;
  }
  return seed + 1;
}
