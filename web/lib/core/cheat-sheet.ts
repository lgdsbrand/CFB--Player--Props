/**
 * The cheat sheet: props whose recent games have already cleared today's line.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). Rows in, sections out; nothing here reads
 * a database.
 *
 * The client asked for "a 80% & 100% hit rate list of all props that have hit
 * for those percentages". The grading itself is `v_cheat_sheet` (migration
 * 0045), transcribed from `gradeGames`/`hitRate` so the sheet and the player
 * page cannot disagree about a push. What lives HERE is everything that decides
 * whether a row deserves to be on a list a reader will act on.
 *
 * ---------------------------------------------------------------------------
 * THREE RULES, AND EACH ONE EXISTS BECAUSE THE OBVIOUS VERSION IS MISLEADING
 * ---------------------------------------------------------------------------
 *
 * 1. A MINIMUM DENOMINATOR. "100%" over two games is 100%, and it is the single
 *    easiest way for a sheet like this to become nonsense — in week 2 every
 *    player who has played once is on a 100% streak, and the perfect list would
 *    be the whole slate. `minDecided` puts a floor under the denominator, and
 *    the page prints the record ("5-0") beside every rate so the reader never
 *    has to trust the percentage alone.
 *
 * 2. THE MODEL'S CALL TRAVELS WITH THE STREAK. A hit rate describes games
 *    already played, graded against a line drawn today; the model's call is a
 *    forecast. They disagree far more often than one would guess: measured over
 *    1,431 entries across 2025 weeks 8-12, the model takes the OTHER SIDE of
 *    the streak on **33%** of them, rising from 32% in week 8 to 40% by week 12.
 *    Publishing the streak by itself would let this page contradict the board
 *    silently, on the same player and market, in the same week — so `agreement`
 *    is computed for every row and shown.
 *
 *    (All 1,431 of those carried a call, none was `no-call`. That follows from
 *    the sheet needing a line and a line producing a pick, but `agreement`
 *    still handles the case rather than assuming it, because the two columns
 *    are independent in the view.)
 *
 * 3. NO "HAS NOT SCORED" ENTRIES. Anytime touchdown is a binary market at a
 *    fixed 0.5 line, so the UNDER side is "has failed to score in five
 *    straight" — true of most of the slate, and information about nobody. It is
 *    the one place where a side of a market is excluded rather than shown, and
 *    `qualifies` is where that is stated rather than being buried in a query.
 *
 * WHAT THIS PAGE IS NOT. A streak is not an edge. Whether the price is worth
 * taking is the board's question and is measured against a de-vigged book price
 * (CLAUDE.md §6); nothing here is evidence of value, and the page says so.
 */

import type { BetSide, PositionGroup } from "@/lib/core/types";

/** One graded prop, at one hit-rate window. */
export type CheatSheetRow = {
  projectionId: number;
  season: number;
  week: number;

  playerId: number;
  playerName: string;
  positionGroup: PositionGroup | null;

  teamId: number;
  teamSchool: string;
  teamAbbreviation: string | null;
  teamColor: string | null;
  teamAltColor: string | null;

  opponentSchool: string;
  opponentAbbreviation: string | null;
  opponentRankVsPosition: number | null;

  gameId: number;
  startDate: string | null;
  isHome: boolean;
  neutralSite: boolean;

  marketKey: string;
  marketLabel: string | null;
  marketEmoji: string | null;
  isBinary: boolean;
  line: number | null;

  /** The model's own call on this prop, or null where it made none. */
  modelSide: BetSide | null;
  displayConfidence: number | null;
  edge: number | null;
  hasCall: boolean;
  hasBookLine: boolean;
  sportsbookKey: string | null;

  windowSize: number;
  /** Games that resolved either way. Pushes are excluded, not counted as losses. */
  decided: number;
  pushes: number;
  hits: number;
  /** The side the history fell on — NOT the model's pick. */
  hitSide: BetSide;
  /** Always ≥ 0.5, because it is the winning side's share. */
  hitRate: number;
};

/**
 * The windows offered, matching the board's own hit-rate control.
 *
 * L5 leads because it is the one that fills in. Measured on 2025: at L10 with a
 * six-game minimum, the perfect list holds 0-3 entries for most of the season,
 * while L5 holds 10-45. A default that is empty two weeks in three would teach
 * the reader the page is broken.
 */
export const CHEAT_WINDOWS: readonly number[] = [5, 10];
export const DEFAULT_CHEAT_WINDOW = 5;

/**
 * The floor under the denominator, by window.
 *
 * Four for L5 and six for L10. Four rather than three because 3-0 is 100% and
 * reads as a streak while being one good month; six rather than five for L10
 * because a ten-game window advertising itself on five games is the same lie
 * one size up.
 */
export function minDecidedFor(windowSize: number): number {
  return Math.max(4, Math.ceil(windowSize * 0.6));
}

/** The two lists the client asked for, in the order they are shown. */
export type CheatTier = {
  key: "perfect" | "strong";
  /** Inclusive floor on the hit rate. */
  min: number;
  title: string;
  /** What membership of this tier means, in one line. */
  blurb: string;
};

export const CHEAT_TIERS: readonly CheatTier[] = [
  {
    key: "perfect",
    min: 1,
    title: "Perfect",
    blurb:
      "Every decided game in the window landed on the same side of the line.",
  },
  {
    key: "strong",
    min: 0.8,
    title: "80% and up",
    blurb: "Four in five or better, but not a clean sweep.",
  },
] as const;

/**
 * Whether a row belongs on the sheet at all.
 *
 * Applied in the DATABASE as well as here — see `lib/data/cheat-sheet.ts`. The
 * duplication is deliberate and is not a filter running twice for safety: the
 * query has to apply it so the row cap counts qualifying rows, and this has to
 * state it so the rule is readable and tested somewhere other than a PostgREST
 * argument string.
 */
export function qualifies(row: CheatSheetRow): boolean {
  if (row.decided < minDecidedFor(row.windowSize)) return false;
  // Rule 3: "has failed to score in five straight" is not a play.
  if (row.isBinary && row.hitSide === "under") return false;
  return row.hitRate >= CHEAT_TIERS[CHEAT_TIERS.length - 1].min;
}

/** Where the model stands relative to the streak. */
export type Agreement = "agrees" | "disagrees" | "no-call";

export function agreement(row: CheatSheetRow): Agreement {
  if (!row.hasCall || row.modelSide === null) return "no-call";
  return row.modelSide === row.hitSide ? "agrees" : "disagrees";
}

export type CheatSection = {
  tier: CheatTier;
  rows: CheatSheetRow[];
};

/**
 * Split qualifying rows into the tiers, most reliable first.
 *
 * Tiers are EXCLUSIVE — a 100% row appears under Perfect and not again under
 * "80% and up". Two sections that both contained it would make the second
 * count meaningless as an answer to "how many are at 80%".
 */
export function cheatSections(rows: CheatSheetRow[]): CheatSection[] {
  const remaining = rows.filter(qualifies).sort(compareRows);

  const sections: CheatSection[] = [];
  let pool = remaining;
  for (const tier of CHEAT_TIERS) {
    const taken = pool.filter((row) => row.hitRate >= tier.min);
    pool = pool.filter((row) => row.hitRate < tier.min);
    sections.push({ tier, rows: taken });
  }
  return sections;
}

/**
 * Sheet order: strongest streak, then the longest sample behind it, then the
 * model's own confidence.
 *
 * SAMPLE BEATS CONFIDENCE in the tiebreak on purpose. Both 5-0 and 4-0 are
 * 100%, and the extra game is evidence; the model's confidence is a different
 * claim about a different question and only breaks a tie the first two could
 * not. The projection id is last so the order is stable across renders.
 */
export function compareRows(a: CheatSheetRow, b: CheatSheetRow): number {
  if (a.hitRate !== b.hitRate) return b.hitRate - a.hitRate;
  if (a.decided !== b.decided) return b.decided - a.decided;
  const confA = a.displayConfidence ?? -1;
  const confB = b.displayConfidence ?? -1;
  if (confA !== confB) return confB - confA;
  return a.projectionId - b.projectionId;
}

/** `5, 0` -> `"5-0"`. The record, printed beside every rate. */
export function formatRecord(hits: number, decided: number): string {
  return `${hits}-${decided - hits}`;
}

/**
 * How a side reads for this market.
 *
 * Anytime touchdown is binary, so "OVER 0.5 offensive TDs" is a clumsy way of
 * writing SCORED. The board's market row already makes this distinction; the
 * sheet has to make it too or it will print the only entry a casual reader
 * recognises in the least recognisable form.
 */
export function sideLabel(row: CheatSheetRow): string {
  if (row.isBinary) return row.hitSide === "over" ? "SCORED" : "NO SCORE";
  return row.hitSide.toUpperCase();
}

/**
 * Why the sheet is empty, given what the slate holds.
 *
 * A TILE OR PAGE WITH NOTHING BEHIND IT MUST SAY WHY — the rule recorded in
 * `home-view.ts`, which this product has now shipped wrong three times. An
 * empty cheat sheet has three quite different causes and only one of them is
 * about the reader's filters:
 *
 *   no games played  — before kickoff, and for the whole of week 1, there is no
 *                      history to grade. Nothing is broken and nothing the
 *                      reader does will help.
 *   too early        — the season is under way but shorter than the minimum
 *                      sample. In week 3 a player has two games; an entry needs
 *                      four decided. Caught by looking at the rendered page:
 *                      week 2 was reporting "that is an ordinary week", which
 *                      is false — it is an arithmetically impossible one, and
 *                      it stays impossible until about week 5.
 *   no lines         — a hit rate is measured against a line, and college books
 *                      post props late (CLAUDE.md §7).
 *   nothing clears   — the genuinely ordinary empty, and the only one worth
 *                      suggesting a wider window for.
 *
 * NO INPUT COMES FROM THE GRADING. The two counts are over indexed columns —
 * priced rows on the board, and box scores earlier in the season — and the
 * third is the week number. This runs exactly when the sheet is empty, and
 * asking the expensive view why it returned nothing costs as much as the query
 * that returned nothing: measured on dev, a count over `v_cheat_sheet` without
 * the rate filters takes ~3 s, and two of them were enough to hang the page.
 */
export type EmptyReason =
  | "no-games"
  | "too-early"
  | "no-lines"
  | "nothing-clears";

export function emptyReason({
  pricedProps,
  playedGames,
  weeksPlayed,
  windowSize,
}: {
  /** Props on the slate carrying a line at all. */
  pricedProps: number;
  /** Box scores recorded earlier THIS season — the material a hit rate needs. */
  playedGames: number;
  /**
   * Weeks of this season already behind the slate on screen — an UPPER BOUND on
   * how many games any player can have. It really is an upper bound rather than
   * the exact figure: CFBD's week 1 spans nine or ten days and a few teams play
   * twice inside it, and byes take games away. Both errors are safe here, since
   * this only ever rules a week out as too young.
   */
  weeksPlayed: number;
  windowSize: number;
}): EmptyReason {
  // Ordered from the most fundamental blocker outwards. Before the season
  // starts every one of these is true at once, and "nobody has played yet" is
  // the answer that resolves on a date the reader can look up.
  if (playedGames === 0) return "no-games";
  if (weeksPlayed < minDecidedFor(windowSize)) return "too-early";
  if (pricedProps === 0) return "no-lines";
  return "nothing-clears";
}
