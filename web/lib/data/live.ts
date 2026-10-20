import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";
import { LIVE_COLUMNS, toLiveScore, type LiveScore } from "@/lib/core/live";
import {
  LIVE_HISTORY_COLUMNS,
  LIVE_PROJECTION_COLUMNS,
  toLivePoint,
  toLiveProjection,
  type LivePoint,
  type LiveProjection,
} from "@/lib/core/live-model";

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

/**
 * The live model's rows for these games (migration 0098), for the first
 * paint; `useLiveProjections` refreshes them from the browser after that.
 * A game the model has not projected has no row.
 */
export async function getLiveProjections(gameIds: number[]): Promise<LiveProjection[]> {
  if (gameIds.length === 0) return [];
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase.from("live_projections").select(LIVE_PROJECTION_COLUMNS).in("game_id", gameIds),
    "live_projections",
  );
  return rows.map((r) => toLiveProjection(r));
}

/** Every moment the live model projected for one game, oldest first: the chart. ~100 rows. */
export async function getLiveHistory(gameId: number): Promise<LivePoint[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("live_projection_history")
      .select(LIVE_HISTORY_COLUMNS)
      .eq("game_id", gameId)
      .order("elapsed", { ascending: true }),
    "live_projection_history",
  );
  return rows.map((r) => toLivePoint(r));
}
