/**
 * The home page's counts.
 *
 * FOUR HEAD-COUNTS, NOT `getBoardCounts`. That function runs five and returns
 * three fields this page has no use for, and the home page is the first thing a
 * reader loads — the board already carries about two seconds of server render
 * and there is no reason to inherit any of it here.
 *
 * `head: true` asks PostgREST for the count and no rows at all, so each of
 * these is a `count(*)` over an indexed predicate rather than a page of data.
 *
 * SCOPED THE SAME WAY THE BOARD IS. Every number here is a promise about what a
 * tile leads to, so it has to be counted under the filter the destination
 * applies — the displayed conferences. A count of the whole FBS slate beside a
 * link to a narrowed board is a tile that lies by a factor of two.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import {
  CHEAT_TIERS,
  DEFAULT_CHEAT_WINDOW,
  minDecidedFor,
} from "@/lib/core/cheat-sheet";
import { DEFAULT_SPORT } from "@/lib/core/sport";
import type { HomeCounts } from "@/lib/core/home-view";
import { SYNTHETIC_BOOK_KEY } from "@/lib/data/odds";
import { upcomingOnly } from "@/lib/data/query";

export async function getHomeCounts(
  season: number,
  week: number,
  edgeThreshold: number,
  games: number,
  kickoffCutoff: Date,
): Promise<HomeCounts> {
  const supabase = createServerSupabaseClient();

  // Every tile promises what its destination shows, and the destination hides
  // games that have kicked off — so these count under the same cut or the tile
  // lies. On 2026 week 1 the difference is 247 rows.
  const base = () =>
    supabase
      .from("v_board_rows")
      .select("projection_id", { count: "exact", head: true })
      .eq("sport", DEFAULT_SPORT)
      .eq("season", season)
      .eq("week", week)
      .eq("conference_is_displayed", true)
      .or(upcomingOnly(kickoffCutoff));

  /**
   * The cheat-sheet tile's number, counted exactly as `getCheatSheet` selects
   * — same window, same floors, same exclusion — because the tile promises what
   * the reader will find when they click it.
   *
   * IT IS THE ONE COUNT HERE THAT IS NOT A PREDICATE ON AN INDEXED COLUMN: it
   * grades every priced prop on the slate. Measured warm on the fullest week in
   * the database (2025 week 12, 405 entries) it runs in 300-530 ms, which is
   * the same order as the five above and it shares their wave, so it costs the
   * page nothing sequentially. A cold first hit measured 2.4 s; if this page
   * ever needs to be faster than its slowest count, that is the one to cache.
   */
  const cheatSheet = supabase
    .from("v_cheat_sheet")
    .select("projection_id", { count: "exact", head: true })
    .eq("sport", DEFAULT_SPORT)
    .eq("season", season)
    .eq("week", week)
    .eq("window_size", DEFAULT_CHEAT_WINDOW)
    .eq("conference_is_displayed", true)
    .gte("hit_rate", CHEAT_TIERS[CHEAT_TIERS.length - 1].min)
    .gte("decided", minDecidedFor(DEFAULT_CHEAT_WINDOW))
    .or("is_binary.eq.false,hit_side.eq.over")
    .or(upcomingOnly(kickoffCutoff));

  const [props, calls, edges, developmentLine, bookLine, sheet] = await Promise.all(
    [
      base(),
      // The population "best plays" ORDERS, not a threshold within it. A
      // confidence floor was tried and returned zero on both live weeks — the
      // top confidence on an opening slate is 0.574.
      base().eq("has_call", true),
      base().gte("edge", edgeThreshold),
      base().eq("sportsbook_key", SYNTHETIC_BOOK_KEY),
      // "Priced by a real book" is the complement of the synthetic one WITHIN
      // the priced rows, not the complement of it overall — most rows have no
      // line at all, and counting those as book-priced would report a market
      // that does not exist yet.
      base().eq("has_book_line", true).neq("sportsbook_key", SYNTHETIC_BOOK_KEY),
      cheatSheet,
    ],
  );

  for (const [result, label] of [
    [props, "props"],
    [calls, "calls"],
    [edges, "edges"],
    [developmentLine, "development line"],
    [bookLine, "book line"],
    [sheet, "cheat sheet"],
  ] as const) {
    if (result.error) {
      throw new Error(`Home count failed (${label}): ${result.error.message}`);
    }
  }

  return {
    props: props.count ?? 0,
    games,
    calls: calls.count ?? 0,
    edges: edges.count ?? 0,
    developmentLine: developmentLine.count ?? 0,
    bookLine: bookLine.count ?? 0,
    cheatSheet: sheet.count ?? 0,
  };
}
