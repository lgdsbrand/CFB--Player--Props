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

import type { BoardRow, PositionGroup } from "@/lib/core/types";

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

  /** In the order the read layer returned them — already sorted. */
  markets: BoardRow[];

  /** Strongest edge across this player's markets, or null if none is priced. */
  topEdge: number | null;
  /** Highest confidence across this player's markets. Always present. */
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
      existing.topConfidence = maxOrNull(existing.topConfidence, row.confidence);
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
      markets: [row],
      topEdge: row.edge,
      topConfidence: row.confidence,
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
 * The bar's window: the projected range, widened to always contain the line.
 *
 * A line well outside p10..p90 is exactly the interesting case — it is what a
 * high-confidence call looks like — and a bar that clipped it would hide the
 * reason the pick exists. Padding keeps the marker off the very edge so it
 * stays visible.
 */
export function barWindow(
  low: number | null,
  high: number | null,
  line: number | null,
): { low: number; high: number } | null {
  if (low === null || high === null) return null;
  if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) return null;

  let min = low;
  let max = high;
  if (line !== null && Number.isFinite(line)) {
    min = Math.min(min, line);
    max = Math.max(max, line);
  }
  const pad = (max - min) * 0.08;
  return { low: min - pad, high: max + pad };
}
