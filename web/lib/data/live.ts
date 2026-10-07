import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";
import { LIVE_COLUMNS, toLiveScore, type LiveScore } from "@/lib/core/live";

/**
 * The live rows for these games, for the first paint (migration 0088). The
 * browser then refreshes them itself (`useLiveScores`) without re-rendering
 * the page. A game never polled has no row.
 */
export async function getLiveScores(gameIds: number[]): Promise<LiveScore[]> {
  if (gameIds.length === 0) return [];
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase.from("live_scores").select(LIVE_COLUMNS).in("game_id", gameIds),
    "live_scores",
  );
  return rows.map((r) => toLiveScore(r));
}
