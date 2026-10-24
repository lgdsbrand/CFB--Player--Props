/**
 * The board's filters, sort and counts, applied to a week held in memory.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * WHY THE BOARD STOPPED FILTERING IN POSTGRES (2026-10-24). Every filter used
 * to be pushed into the query, so every combination of the board's controls
 * was a different SQL statement. PostgREST prepares each statement it runs and
 * keeps it on that connection, and one of these keeps 0.5-2.4 MB because it
 * carries the whole expanded `v_board_rows` view. Production saw 474 distinct
 * board statements in the 13 minutes after a restart, and the instance (Small,
 * 2 GB) restarted about ten times a day from memory pressure while CPU sat at
 * 5%. So the week is now read once as a compact index (`board_index`,
 * migration 0104) and everything here runs on it.
 *
 * THE ORIGINAL REASON FOR FILTERING IN SQL STILL HOLDS, AND IS STILL MET. A
 * filter applied after a 1,000-row PostgREST response would operate on an
 * arbitrary truncation of the week. The index is the WHOLE week (a scalar JSON
 * result is not row-capped), so these filters see every row the SQL ones did.
 *
 * EVERY PREDICATE HERE MIRRORS ONE IN `buildBoardQuery`'s former SQL, including
 * NULL handling: `eq`/`gte` against NULL is not true in SQL, so a null value
 * fails every comparison here too. The tests pin each one.
 */

import { MAJOR_BOOKS } from "./board-params.ts";
import { boardSortKeys } from "./board-view.ts";
import type { BoardSort } from "@/lib/data/board";
import type { BetSide, PositionGroup } from "./types.ts";

/**
 * The positional columns `board_index` returns. A CONTRACT with migration
 * 0104: the function builds each row in exactly this order. Append, never
 * reorder.
 */
export const INDEX_COLUMNS = [
  "projection_id",
  "player_id",
  "game_id",
  "market_key",
  "position_group",
  "start_date",
  "conference_name",
  "conference_is_displayed",
  "player_name",
  "has_book_line",
  "has_call",
  "sportsbook_key",
  "team_poll_rank",
  "side",
  "is_binary",
  "edge",
  "display_confidence",
  "opponent_rank_vs_position",
  "publishes_call",
  "book_prob_over",
] as const;

export type IndexRow = {
  projectionId: number;
  playerId: number;
  gameId: number;
  marketKey: string;
  positionGroup: PositionGroup | null;
  /** Kickoff as epoch ms; null for a TBD kickoff. */
  startMs: number | null;
  conferenceName: string | null;
  conferenceIsDisplayed: boolean | null;
  playerName: string;
  hasBookLine: boolean;
  hasCall: boolean;
  sportsbookKey: string | null;
  teamPollRank: number | null;
  side: BetSide | null;
  isBinary: boolean;
  edge: number | null;
  displayConfidence: number | null;
  opponentRankVsPosition: number | null;
  publishesCall: boolean;
  bookProbOver: number | null;
};

const at = (name: (typeof INDEX_COLUMNS)[number]) => INDEX_COLUMNS.indexOf(name);
const COL = Object.fromEntries(INDEX_COLUMNS.map((name) => [name, at(name)])) as Record<
  (typeof INDEX_COLUMNS)[number],
  number
>;

/** `numeric` can arrive as a string; null stays null. */
function optNum(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}

export function decodeIndex(raw: readonly (readonly unknown[])[]): IndexRow[] {
  return raw.map((r) => {
    const start = r[COL.start_date] as string | null;
    return {
      projectionId: Number(r[COL.projection_id]),
      playerId: Number(r[COL.player_id]),
      gameId: Number(r[COL.game_id]),
      marketKey: r[COL.market_key] as string,
      positionGroup: (r[COL.position_group] as PositionGroup | null) ?? null,
      startMs: start === null || start === undefined ? null : Date.parse(start),
      conferenceName: (r[COL.conference_name] as string | null) ?? null,
      conferenceIsDisplayed: (r[COL.conference_is_displayed] as boolean | null) ?? null,
      playerName: (r[COL.player_name] as string | null) ?? "",
      hasBookLine: r[COL.has_book_line] === true,
      hasCall: r[COL.has_call] === true,
      sportsbookKey: (r[COL.sportsbook_key] as string | null) ?? null,
      teamPollRank: optNum(r[COL.team_poll_rank]),
      side: (r[COL.side] as BetSide | null) ?? null,
      isBinary: r[COL.is_binary] === true,
      edge: optNum(r[COL.edge]),
      displayConfidence: optNum(r[COL.display_confidence]),
      opponentRankVsPosition: optNum(r[COL.opponent_rank_vs_position]),
      publishesCall: r[COL.publishes_call] === true,
      bookProbOver: optNum(r[COL.book_prob_over]),
    };
  });
}

/** The row-selecting filters of `BoardFilters`, minus season/week/sport/sort/paging. */
export type IndexFilters = {
  marketKey?: string;
  marketKeys?: string[];
  positionGroup?: PositionGroup;
  gameIds?: number[];
  search?: string;
  displayedConferencesOnly?: boolean;
  conferenceName?: string;
  rankedOnly?: boolean;
  side?: BetSide;
  edgesOnly?: boolean;
  edgeThreshold?: number;
  minConfidence?: number;
  minOpponentRank?: number;
  withBookLineOnly?: boolean;
  majorBooksOnly?: boolean;
  withCallOnly?: boolean;
  kickoffCutoff?: Date;
};

const atLeast = (value: number | null, floor: number) => value !== null && value >= floor;

/** `start_date >= cutoff OR start_date IS NULL` — `upcomingOnly` in the read layer. */
export function isUpcoming(row: IndexRow, cutoff: Date | undefined): boolean {
  return cutoff === undefined || row.startMs === null || row.startMs >= cutoff.getTime();
}

/**
 * The Over / Under switch. A null side matches neither value; Under also drops
 * the yes/no markets (an anytime-TD "under" is the model declining the market).
 */
function matchesSide(row: IndexRow, side: BetSide | undefined): boolean {
  if (!side) return true;
  if (row.side !== side) return false;
  return side === "under" ? row.isBinary === false : true;
}

const isMajorBook = (row: IndexRow) =>
  row.sportsbookKey !== null && (MAJOR_BOOKS as readonly string[]).includes(row.sportsbookKey);

/** Scope and market choice, both applied: they can only narrow. */
function matchesMarket(row: IndexRow, f: IndexFilters): boolean {
  if (f.marketKeys && !f.marketKeys.includes(row.marketKey)) return false;
  if (f.marketKey && row.marketKey !== f.marketKey) return false;
  return true;
}

/** Every predicate the board's row query applied, in the same order. */
export function matchesBoardFilters(row: IndexRow, f: IndexFilters): boolean {
  if (!matchesMarket(row, f)) return false;
  if (f.positionGroup && row.positionGroup !== f.positionGroup) return false;
  // An EMPTY list is a real filter meaning "no games", not "no filter".
  if (f.gameIds && !f.gameIds.includes(row.gameId)) return false;
  if (!isUpcoming(row, f.kickoffCutoff)) return false;
  if (f.conferenceName) {
    if (row.conferenceName !== f.conferenceName) return false;
  } else if (f.displayedConferencesOnly !== false && row.conferenceIsDisplayed !== true) {
    return false;
  }
  if (f.search && !row.playerName.toLowerCase().includes(f.search.toLowerCase())) {
    return false;
  }
  if (f.withBookLineOnly && !row.hasBookLine) return false;
  if (f.withCallOnly && !row.hasCall) return false;
  if (f.majorBooksOnly && !isMajorBook(row)) return false;
  if (f.rankedOnly && row.teamPollRank === null) return false;
  if (!matchesSide(row, f.side)) return false;
  if (f.edgesOnly && !atLeast(row.edge, f.edgeThreshold ?? 0.05)) return false;
  if (f.minConfidence !== undefined && !atLeast(row.displayConfidence, f.minConfidence)) {
    return false;
  }
  if (
    f.minOpponentRank !== undefined &&
    !atLeast(row.opponentRankVsPosition, f.minOpponentRank)
  ) {
    return false;
  }
  return true;
}

/**
 * Which of a card's rows it shows: the filters that decide SUB-CARDS (scope,
 * market, position, side, books), never the ones that decide which PLAYERS
 * make the page (edge, confidence, rank). See `getRowsForCards`.
 */
export function matchesCardFilters(row: IndexRow, f: IndexFilters): boolean {
  if (!matchesMarket(row, f)) return false;
  if (f.positionGroup && row.positionGroup !== f.positionGroup) return false;
  if (!matchesSide(row, f.side)) return false;
  if (f.majorBooksOnly && !isMajorBook(row)) return false;
  return true;
}

function sortValue(row: IndexRow, column: string, nowMs: number): number | boolean | null {
  switch (column) {
    // `coalesce(start_date <= now(), false)` in the view, evaluated at read time.
    case "has_kicked_off":
      return row.startMs !== null && row.startMs <= nowMs;
    case "edge":
      return row.edge;
    case "display_confidence":
      return row.displayConfidence;
    case "opponent_rank_vs_position":
      return row.opponentRankVsPosition;
    case "projection_id":
      return row.projectionId;
    default:
      // A new sort column must be added to the index and here, not silently ignored.
      throw new Error(`board-index cannot sort on ${column}`);
  }
}

/** `boardSortKeys` as a comparator, with Postgres's NULLS FIRST/LAST semantics. */
export function sortIndexRows(rows: IndexRow[], sort: BoardSort | undefined, now: Date): IndexRow[] {
  const keys = boardSortKeys(sort);
  const nowMs = now.getTime();
  return [...rows].sort((a, b) => {
    for (const key of keys) {
      const va = sortValue(a, key.column, nowMs);
      const vb = sortValue(b, key.column, nowMs);
      if (va === null && vb === null) continue;
      if (va === null) return key.nullsFirst ? -1 : 1;
      if (vb === null) return key.nullsFirst ? 1 : -1;
      if (va < vb) return key.ascending ? -1 : 1;
      if (va > vb) return key.ascending ? 1 : -1;
    }
    return 0;
  });
}

export type HomeBoardCounts = {
  props: number;
  calls: number;
  edges: number;
  developmentLine: number;
  bookLine: number;
};

/**
 * The home tiles' five board counts, as the head-counts in `getHomeCounts` took
 * them: displayed conferences, games still to kick, and NOT restricted to
 * `publishes_call` (unlike `countIndex`) — each tile counts what its link shows.
 */
export function countHomeBoard(
  rows: IndexRow[],
  {
    edgeThreshold,
    syntheticBookKey,
    kickoffCutoff,
  }: { edgeThreshold: number; syntheticBookKey: string; kickoffCutoff: Date },
): HomeBoardCounts {
  const counts: HomeBoardCounts = {
    props: 0,
    calls: 0,
    edges: 0,
    developmentLine: 0,
    bookLine: 0,
  };
  for (const row of rows) {
    if (row.conferenceIsDisplayed !== true || !isUpcoming(row, kickoffCutoff)) continue;
    counts.props += 1;
    if (row.hasCall) counts.calls += 1;
    if (atLeast(row.edge, edgeThreshold)) counts.edges += 1;
    if (row.sportsbookKey === syntheticBookKey) counts.developmentLine += 1;
    // `neq` against a NULL book is not true in SQL.
    if (
      row.hasBookLine &&
      row.sportsbookKey !== null &&
      row.sportsbookKey !== syntheticBookKey
    ) {
      counts.bookLine += 1;
    }
  }
  return counts;
}

export type IndexCounts = {
  rows: number;
  withCall: number;
  withBookLine: number;
  withDevLine: number;
  withEvenBookPrice: number;
  overThreshold: number;
};

/** What `board_counts` (migration 0059) returned, computed from the index. */
export function countIndex(
  rows: IndexRow[],
  {
    edgeThreshold,
    syntheticBookKey,
    displayedConferencesOnly = true,
    kickoffCutoff,
  }: {
    edgeThreshold: number;
    syntheticBookKey: string;
    displayedConferencesOnly?: boolean;
    kickoffCutoff?: Date;
  },
): IndexCounts {
  const counts: IndexCounts = {
    rows: 0,
    withCall: 0,
    withBookLine: 0,
    withDevLine: 0,
    withEvenBookPrice: 0,
    overThreshold: 0,
  };
  for (const row of rows) {
    if (!row.publishesCall) continue;
    if (displayedConferencesOnly && row.conferenceIsDisplayed !== true) continue;
    if (!isUpcoming(row, kickoffCutoff)) continue;
    counts.rows += 1;
    if (row.hasCall) counts.withCall += 1;
    if (row.hasBookLine) counts.withBookLine += 1;
    if (row.sportsbookKey === syntheticBookKey) counts.withDevLine += 1;
    // `<>` against a NULL book is not true in SQL, so a row with no book is out.
    if (
      row.bookProbOver === 0.5 &&
      row.sportsbookKey !== null &&
      row.sportsbookKey !== syntheticBookKey
    ) {
      counts.withEvenBookPrice += 1;
    }
    if (atLeast(row.edge, edgeThreshold)) counts.overThreshold += 1;
  }
  return counts;
}
