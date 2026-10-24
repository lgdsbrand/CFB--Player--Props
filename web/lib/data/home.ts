/**
 * The home page's counts.
 *
 * THE FIVE BOARD COUNTS COME FROM THE BOARD INDEX (2026-10-24), the cached week
 * the board itself lists from (`getBoardIndex`, migration 0104), so a home view
 * costs the database nothing for them. They were five uncached `head: true`
 * counts over `v_board_rows` on every view of the landing page.
 *
 * SCOPED THE SAME WAY THE BOARD IS. Every number here is a promise about what a
 * tile leads to, so it has to be counted under the filter the destination
 * applies — the displayed conferences and the games still to kick. A count of
 * the whole FBS slate beside a link to a narrowed board is a tile that lies by
 * a factor of two. See `countHomeBoard`.
 *
 * The cheat-sheet tile counts from the sheet's own cached read; the tracker
 * count is one fixed statement, cached for a minute.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { countHomeBoard } from "@/lib/core/board-index";
import { CHEAT_TIERS, DEFAULT_CHEAT_WINDOW } from "@/lib/core/cheat-sheet";
import { DEFAULT_SPORT, type Sport } from "@/lib/core/sport";
import type { HomeCounts } from "@/lib/core/home-view";
import { getBoardIndex } from "@/lib/data/board";
import { cachedLiveRead } from "@/lib/data/cache";
import { getCheatSheet } from "@/lib/data/cheat-sheet";
import { SYNTHETIC_BOOK_KEY } from "@/lib/data/odds";

/**
 * The cheat-sheet tile's number, from the same cached read the sheet itself
 * renders from (`getCheatSheet`), at the default window and the lowest tier:
 * the tile promises what the reader will find when they click it.
 */
async function getCheatSheetCount(
  sport: Sport,
  season: number,
  week: number,
  kickoffCutoff: Date,
): Promise<number> {
  const sheet = await getCheatSheet({
    sport,
    season,
    week,
    windowSize: DEFAULT_CHEAT_WINDOW,
    minHitRate: CHEAT_TIERS[CHEAT_TIERS.length - 1].min,
    kickoffCutoff,
  });
  return sheet.total;
}

/**
 * The tracker tile: graded picks, both engines the page shows. Across the whole
 * history, not this week, because that is what the page opens on.
 */
async function readTrackedCount(sport: Sport): Promise<number> {
  const supabase = createServerSupabaseClient();
  const result = await supabase
    .from("v_tracker_picks")
    .select("id", { count: "exact", head: true })
    .eq("sport", sport)
    .eq("period", "full")
    .in("engine", ["v2", "rules"])
    .neq("result", "pending");
  if (result.error) {
    throw new Error(`Home count failed (tracker): ${result.error.message}`);
  }
  return result.count ?? 0;
}

const getTrackedCount = cachedLiveRead("home-tracked-count", readTrackedCount);

export async function getHomeCounts(
  season: number,
  week: number,
  edgeThreshold: number,
  games: number,
  kickoffCutoff: Date,
  sport: Sport = DEFAULT_SPORT,
): Promise<HomeCounts> {
  const [index, cheatSheet, tracked] = await Promise.all([
    getBoardIndex(sport, season, week),
    getCheatSheetCount(sport, season, week, kickoffCutoff),
    getTrackedCount(sport),
  ]);
  // "Best plays" ORDERS the called population rather than thresholding it: a
  // confidence floor returned zero on both live weeks (top confidence on an
  // opening slate is 0.574). "Priced by a real book" is the complement of the
  // synthetic book WITHIN the priced rows — most rows have no line at all.
  const board = countHomeBoard(index, {
    edgeThreshold,
    syntheticBookKey: SYNTHETIC_BOOK_KEY,
    kickoffCutoff,
  });
  return { ...board, games, cheatSheet, tracked };
}
