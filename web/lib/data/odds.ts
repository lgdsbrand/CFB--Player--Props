/**
 * Per-book quotes for the odds row on a card.
 *
 * `v_board_rows` carries ONE book per row — the highest-priority one — so the
 * board stays one row per player-market. The card's per-book breakdown comes
 * from here (CLAUDE.md §7).
 *
 * `book_prob_over` arrives already de-vigged by `devig_two_way`, which reads
 * `app_config.devig_method`. It is deliberately not recomputed in TypeScript: a
 * second implementation of the de-vig would be a second definition of edge, and
 * the two would drift. Edge is defined once, in SQL.
 *
 * A one-sided price cannot be de-vigged and arrives as null. That means "no
 * book probability", never zero edge — books routinely post only the favoured
 * side of a longshot anytime-TD market, and treating that as a fair 50/50 would
 * invent an edge out of a missing number.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { BookQuote } from "@/lib/core/types";
import { type DbRow, unwrap } from "@/lib/data/query";

const COLUMNS =
  "line_id, game_id, player_id, market_key, sportsbook_key, sportsbook_name, " +
  "line, over_price, under_price, book_prob_over, captured_at";

/**
 * The development book the projection job writes under `--synthetic-lines`.
 *
 * Must match `SYNTHETIC_BOOK_KEY` in `worker/jobs/run_projections.py`. It is a
 * display concern here and nothing more: any surface showing an EDGE against
 * this book has to say the edge is an artifact, because a −110/−110 quote
 * de-vigs to exactly 0.500 and turns edge into confidence minus 50%.
 */
export const SYNTHETIC_BOOK_KEY = "dev";

/** Every book's current quote for one player in one week. */
export async function getPlayerQuotes(
  playerId: number,
  season: number,
  week: number,
): Promise<BookQuote[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_latest_prop_lines")
      .select(COLUMNS)
      .eq("player_id", playerId)
      .eq("season", season)
      .eq("week", week)
      .order("market_key")
      .order("sportsbook_key"),
    "v_latest_prop_lines (player)",
  );
  return rows.map(toQuote);
}

/**
 * Quotes for a set of players in one market, keyed by player.
 *
 * Batched because a board of 200 cards must not issue 200 reads. An empty
 * `playerIds` short-circuits: PostgREST turns `in.()` into a syntax error
 * rather than an empty result.
 */
export async function getQuotesByPlayer(
  playerIds: number[],
  season: number,
  week: number,
  marketKey?: string,
): Promise<Map<number, BookQuote[]>> {
  const byPlayer = new Map<number, BookQuote[]>();
  if (playerIds.length === 0) return byPlayer;

  const supabase = createServerSupabaseClient();
  let query = supabase
    .from("v_latest_prop_lines")
    .select(COLUMNS)
    .eq("season", season)
    .eq("week", week)
    .in("player_id", playerIds);

  if (marketKey) query = query.eq("market_key", marketKey);

  for (const row of unwrap<DbRow[]>(await query, "v_latest_prop_lines (batch)")) {
    const quote = toQuote(row);
    const list = byPlayer.get(quote.playerId) ?? [];
    list.push(quote);
    byPlayer.set(quote.playerId, list);
  }
  return byPlayer;
}

function toQuote(row: Record<string, unknown>): BookQuote {
  return {
    lineId: row.line_id as number,
    gameId: row.game_id as number,
    playerId: row.player_id as number,
    marketKey: row.market_key as string,
    sportsbookKey: row.sportsbook_key as string,
    sportsbookName: row.sportsbook_name as string,
    line: row.line as number,
    overPrice: (row.over_price as number | null) ?? null,
    underPrice: (row.under_price as number | null) ?? null,
    bookProbOver: (row.book_prob_over as number | null) ?? null,
    capturedAt: row.captured_at as string,
  };
}
