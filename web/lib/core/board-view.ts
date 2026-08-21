/**
 * Turning board rows into the cards the board renders.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * The read layer returns one row per PLAYER-MARKET, because that is what a
 * projection is. The client's pitcher card is one card per PLAYER holding a
 * sub-card per market (CLAUDE.md §7), so the grouping happens here rather than
 * in a component — it is a data shape, and it is worth testing.
 */

// Relative, not aliased: these are VALUES, so they survive type stripping and
// the test runner has to resolve them for real. Type-only imports keep the
// alias because they are erased before Node sees them.
import { gradeGames, hitRate } from "./hit-rate.ts";
import type { GradedGame, HitRateSummary } from "@/lib/core/hit-rate";
import type {
  BetSide,
  BoardRow,
  Market,
  PlayerGameLogRow,
  PositionGroup,
} from "@/lib/core/types";

/** How the board orders rows. Mirrors the sort-priority switch in §7. */
export type BoardSort = "edge" | "confidence" | "opponent_rank";

/**
 * What one row actually claims — the three states every board surface renders.
 *
 * SHARED SO THE CARD AND THE TABLE CANNOT DISAGREE. Both must invert anytime
 * TD the same way, and that inversion is subtle enough to be got wrong
 * independently in two components: the called side there is `under` on ~97% of
 * picks, so a receiver with a 10% chance to score reads "UNDER 90%", which is
 * true, useless, and looks like a strong pick. `modelProbOver` is P(more than
 * 0.5 touchdowns), which IS the anytime-scorer probability CLAUDE.md §1 asks
 * for. This repo has already shipped one bug of exactly this shape (a panel and
 * the board disagreeing about scope, with no test able to catch it), so the
 * decision lives here as data and each surface only chooses how to draw it.
 */
export type BoardCall =
  /** A binary market: the probability the player DOES it, never a called side. */
  | { kind: "binary"; probability: number | null }
  /** A line exists and the model has taken a side. */
  | { kind: "call"; side: BetSide; confidence: number }
  /** No line to call against — the model's lean, and nothing to measure it on. */
  | { kind: "lean" };

export function callFor(row: BoardRow): BoardCall {
  if (row.isBinary) return { kind: "binary", probability: row.modelProbOver };
  if (row.hasCall && row.side && row.confidence !== null) {
    return { kind: "call", side: row.side, confidence: row.confidence };
  }
  return { kind: "lean" };
}

/**
 * Grade this row's past games against the line now showing.
 *
 * LIFTED OUT OF THE PLAYER CARD so the card and the table grade identically.
 * It was a file-local helper there, which was fine while one surface used it
 * and is exactly how two surfaces come to disagree about a push.
 *
 * Returns an EMPTY ARRAY, never null, when there is nothing to grade — no line,
 * no side, or a market whose stat column the game log does not carry. Callers
 * turn that into an explicit "no line to grade against" rather than a zero,
 * which would read as "never hits".
 *
 * Binary markets grade on the OVER side whatever the call was, so a green dot
 * means the player scored. Grading on the call would paint five green dots for
 * a player who has not scored all season — accurate against "under 0.5" and the
 * opposite of what a reader takes from it.
 */
export function gradeRow(
  row: Pick<BoardRow, "line" | "side">,
  market: Pick<Market, "statColumn" | "isBinary"> | undefined,
  games: PlayerGameLogRow[],
): GradedGame[] {
  if (row.line === null || row.side === null || !market) return [];
  const side: BetSide = market.isBinary ? "over" : row.side;
  return gradeGames(games, market.statColumn, row.line, side);
}

/**
 * The card's hit-rate summary for one row: grade, then take one window.
 *
 * The table deliberately does NOT use this — it needs three windows off one
 * grading pass, so it calls `gradeRow` once and `hitRate` per column rather
 * than re-grading the same games three times.
 */
export function summariseRow(
  row: Pick<BoardRow, "line" | "side">,
  market: Pick<Market, "statColumn" | "isBinary"> | undefined,
  games: PlayerGameLogRow[],
  window: number,
): HitRateSummary | null {
  const graded = gradeRow(row, market, games);
  if (graded.length === 0) return null;
  return hitRate(graded, window);
}

/**
 * Hit rate across everything graded, for the SZN column.
 *
 * SEASON TO DATE, AND ONLY THAT. The games handed in come from
 * `getGameLogsByPlayer(..., { before: week })`, so this is the current season up
 * to but excluding the week on screen — never a career figure and never
 * including the result being predicted.
 *
 * THIS IS WHY THERE IS NO L20 COLUMN. A college team plays 12-13 games, so an
 * L20 window would be the season for every player alive and would print the
 * same number as this column beside it. The MLB board the client sent has one
 * because a baseball season is 162 games.
 */
export function seasonToDate(graded: GradedGame[]): HitRateSummary | null {
  if (graded.length === 0) return null;
  return hitRate(graded, graded.length);
}

/** One ORDER BY term, in the shape PostgREST's `.order()` takes. */
export type BoardSortKey = {
  column: string;
  ascending: boolean;
  nullsFirst: boolean;
};

const descending = (column: string): BoardSortKey => ({
  column,
  ascending: false,
  nullsFirst: false,
});

/**
 * The ORDER BY chain for a sort choice.
 *
 * `nullsFirst: false` on every descending term. A row with no book line has a
 * null edge and a row with an unrated opponent has a null rank; Postgres sorts
 * NULLs first on DESC by default, which would put exactly the least informative
 * rows at the top of the board.
 *
 * CONFIDENCE MEANS `display_confidence` HERE, NOT `picks.confidence`. The two
 * point in opposite directions for anytime TD, where the called side is `under`
 * on ~97% of picks: `confidence` there is the probability a player does NOT
 * score. Sorting on it opened the board with the players most certain to do
 * nothing. `display_confidence` is the number the card prints — see
 * `BoardRow.displayConfidence` and migration 20260803130000.
 *
 * EVERY CHAIN FALLS BACK TO OPPONENT RANK, AND THAT IS THE POINT. Edge and
 * confidence both come from `picks` — without a line there is no pick, so both
 * are null on every row. Ordering by edge then confidence therefore decides
 * NOTHING on an unpriced slate, and the old chain fell straight through to the
 * `projection_id` tiebreak: the board came out in insertion order, which is to
 * say arbitrary. That is not a rare edge case. College books post props on
 * Thursday or Friday for Saturday games (CLAUDE.md §7), so for most of a live
 * week most of the board is unpriced and this fallback IS the sort.
 *
 * Opponent rank is the only signal a row carries before a book prices it that
 * means the same thing across markets, and softest-first is the direction a
 * reader wants — the same direction the weekly-targets panel ranks in.
 */
export function boardSortKeys(sort: BoardSort = "edge"): BoardSortKey[] {
  const primary: BoardSortKey[] =
    sort === "confidence"
      ? [descending("display_confidence")]
      : sort === "opponent_rank"
        ? [descending("opponent_rank_vs_position")]
        : [descending("edge"), descending("display_confidence")];

  const keys = [...primary];
  if (!keys.some((key) => key.column === "opponent_rank_vs_position")) {
    keys.push(descending("opponent_rank_vs_position"));
  }

  // A stable last term, so paging through a week cannot repeat or skip a row.
  keys.push({ column: "projection_id", ascending: true, nullsFirst: false });
  return keys;
}

/**
 * The four populations a week's projections fall into, by where their line came
 * from — or that they have none.
 *
 * WHY THIS IS A NAMED FUNCTION AND NOT TWO SUBTRACTIONS IN THE PAGE. The board
 * used to describe "still awaiting a line" as `withCall - withBookLine`, which
 * is a real population but the wrong one: those are the rows a book has NOT
 * priced and the model still calls, because their line is structural rather
 * than quoted (anytime TD is "over 0.5 touchdowns" whether or not anyone is
 * taking bets). They are not waiting for anything. On 2025 week 12 that put
 * "979 still awaiting a line" under a slate where the number genuinely without
 * one was 2,899 once the synthetic development lines are discounted.
 *
 * Each field is derived from a count of a disjoint condition and named for the
 * state it describes, so the page picks a number rather than deriving one.
 */
export type LineCoverage = {
  /** No line at all: the model's lean, and nothing to call it against. */
  awaitingLine: number;
  /** Called against `markets.default_line` — structural, never quoted. */
  structuralLine: number;
  /** Called against the synthetic DEV book. Fake by construction. */
  developmentLine: number;
  /** Called against a price a real sportsbook posted. */
  bookLine: number;
};

export function lineCoverage(counts: {
  rows: number;
  withCall: number;
  withBookLine: number;
  withDevLine: number;
}): LineCoverage {
  const developmentLine = Math.min(
    Math.max(counts.withDevLine, 0),
    Math.max(counts.withBookLine, 0),
  );
  return {
    awaitingLine: Math.max(counts.rows - counts.withCall, 0),
    structuralLine: Math.max(counts.withCall - counts.withBookLine, 0),
    developmentLine,
    bookLine: Math.max(counts.withBookLine - developmentLine, 0),
  };
}

export type PlayerCard = {
  key: string;
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
  conferenceName: string | null;

  /**
   * Where the game is played. GAME-LEVEL, like the kickoff beside it, so the
   * card header renders it once rather than per market.
   */
  venueName: string | null;
  venueCity: string | null;
  venueState: string | null;

  /** Game line context. GAME-level, so the card header renders it once. */
  teamSpread: number | null;
  gameTotal: number | null;

  /** AP Top 25 rank of this player's team entering the week, else null. */
  teamPollRank: number | null;

  /**
   * How much the model knows about this player this week.
   *
   * PROMOTED TO THE CARD BECAUSE THEY ARE PLAYER-LEVEL FACTS. The feature frame
   * computes both once per player-week, so every market on a card carries
   * identical values — rendering them per market would repeat one number five
   * times and imply it varies. Taken from the first row for that reason.
   */
  priorWeight: number | null;
  effectiveSample: number | null;

  /** In the order the read layer returned them — already sorted. */
  markets: BoardRow[];

  /** Strongest edge across this player's markets, or null if none is priced. */
  topEdge: number | null;
  /**
   * Highest DISPLAY confidence across this player's markets, or null when none
   * is called. Drives the GRADE badge.
   *
   * Taken from `displayConfidence` rather than `confidence` for the reason the
   * sort chain is: a card whose only called market is anytime TD would read its
   * under-side confidence and badge a player with a 5% chance to score as A+,
   * directly beside the "5% TO SCORE" it prints.
   */
  topConfidence: number | null;
};

/**
 * Group rows into player cards, preserving the order rows arrived in.
 *
 * ORDER MATTERS AND IS NOT ALPHABETICAL. The query already sorted by edge (or
 * whatever the sort switch asked for), so the first time a player appears is
 * their best row. Re-sorting here would silently override the user's choice;
 * keeping first-appearance order means the card sort and the row sort agree.
 *
 * A player appears once per GAME, not once per week. That distinction costs
 * nothing here and prevents a genuine bug if a team ever plays twice in one
 * week — the two matchups would otherwise merge into one card.
 */
export function groupIntoCards(rows: BoardRow[]): PlayerCard[] {
  const cards = new Map<string, PlayerCard>();

  for (const row of rows) {
    const key = `${row.playerId}-${row.gameId}`;
    const existing = cards.get(key);

    if (existing) {
      existing.markets.push(row);
      existing.topEdge = maxOrNull(existing.topEdge, row.edge);
      existing.topConfidence = maxOrNull(
        existing.topConfidence,
        row.displayConfidence,
      );
      continue;
    }

    cards.set(key, {
      key,
      playerId: row.playerId,
      playerName: row.playerName,
      positionGroup: row.positionGroup,
      teamId: row.teamId,
      teamSchool: row.teamSchool,
      teamAbbreviation: row.teamAbbreviation,
      teamColor: row.teamColor,
      teamAltColor: row.teamAltColor,
      opponentSchool: row.opponentSchool,
      opponentAbbreviation: row.opponentAbbreviation,
      opponentRankVsPosition: row.opponentRankVsPosition,
      gameId: row.gameId,
      startDate: row.startDate,
      isHome: row.isHome,
      neutralSite: row.neutralSite,
      conferenceName: row.conferenceName,
      venueName: row.venueName,
      venueCity: row.venueCity,
      venueState: row.venueState,
      teamSpread: row.teamSpread,
      gameTotal: row.gameTotal,
      teamPollRank: row.teamPollRank,
      priorWeight: row.priorWeight,
      effectiveSample: row.effectiveSample,
      markets: [row],
      topEdge: row.edge,
      topConfidence: row.displayConfidence,
    });
  }

  return [...cards.values()];
}

function maxOrNull(a: number | null, b: number | null): number | null {
  if (a === null) return b;
  if (b === null) return a;
  return Math.max(a, b);
}

/**
 * Where the projected median sits between the p10 and p90, as a 0..1 fraction.
 *
 * Backs the projected-vs-line bar. Returns null when the range is degenerate,
 * so the bar renders as unavailable rather than as a misleading zero-width one.
 */
export function positionInRange(
  value: number | null,
  low: number | null,
  high: number | null,
): number | null {
  if (value === null || low === null || high === null) return null;
  const span = high - low;
  if (!Number.isFinite(span) || span <= 0) return null;
  return clamp01((value - low) / span);
}

function clamp01(value: number): number {
  return Math.min(Math.max(value, 0), 1);
}

/**
 * The floor a projected quantile is displayed at.
 *
 * EVERY MARKET IN THE CATALOGUE IS A NON-NEGATIVE STAT — yards, attempts,
 * completions, receptions, touchdowns. Some are fitted with a distribution that
 * has unbounded left support, so the stored p10 runs below zero on rows where
 * the mean is low relative to the spread: 555 of 747 rush-yards rows on 2025
 * week 13, the worst at −187.1. A card offering "−187.1 … proj 12.0 … 61.4"
 * discredits the projection beside it.
 *
 * THIS IS A DISPLAY FLOOR AND NOTHING ELSE. No probability is recomputed and
 * the stored distribution is untouched — the calibration report measures P(over)
 * at book-realistic lines in the bulk of the distribution, not in a tail no
 * outcome can reach. Truncating the shown range at the physical minimum is the
 * honest presentation of the same belief. The family choice that produces the
 * negative tail is a model-level question, not a rendering one.
 */
export const PROJECTION_FLOOR = 0;

export function displayQuantile(value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return Math.max(value, PROJECTION_FLOOR);
}

/**
 * The bar's window: the projected range, widened to always contain the line.
 *
 * A line well outside p10..p90 is exactly the interesting case — it is what a
 * high-confidence call looks like — and a bar that clipped it would hide the
 * reason the pick exists. Padding keeps the marker off the very edge so it
 * stays visible.
 *
 * The low end is floored before anything else, so the bar's geometry and its
 * end labels agree about where the range starts.
 */
export function barWindow(
  low: number | null,
  high: number | null,
  line: number | null,
): { low: number; high: number } | null {
  const floored = displayQuantile(low);
  if (floored === null || high === null) return null;
  if (!Number.isFinite(high) || high <= floored) return null;

  let min = floored;
  let max = high;
  if (line !== null && Number.isFinite(line)) {
    min = Math.min(min, line);
    max = Math.max(max, line);
  }
  const pad = (max - min) * 0.08;
  return { low: min - pad, high: max + pad };
}
