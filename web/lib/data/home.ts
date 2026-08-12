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
import { DEFAULT_SPORT } from "@/lib/core/sport";
import type { HomeCounts } from "@/lib/core/home-view";
import { SYNTHETIC_BOOK_KEY } from "@/lib/data/odds";

export async function getHomeCounts(
  season: number,
  week: number,
  edgeThreshold: number,
  games: number,
): Promise<HomeCounts> {
  const supabase = createServerSupabaseClient();

  const base = () =>
    supabase
      .from("v_board_rows")
      .select("projection_id", { count: "exact", head: true })
      .eq("sport", DEFAULT_SPORT)
      .eq("season", season)
      .eq("week", week)
      .eq("conference_is_displayed", true);

  const [props, calls, edges, developmentLine, bookLine] = await Promise.all(
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
    ],
  );

  for (const [result, label] of [
    [props, "props"],
    [calls, "calls"],
    [edges, "edges"],
    [developmentLine, "development line"],
    [bookLine, "book line"],
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
  };
}
