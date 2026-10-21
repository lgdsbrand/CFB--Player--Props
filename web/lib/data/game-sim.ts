import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";
import {
  GAME_SIM_COLUMNS,
  GAME_SIM_REPLAY_COLUMNS,
  SIM_EDGE_COLUMNS,
  toGameSim,
  toSimEdge,
  type GameSim,
  type SimEdge,
} from "@/lib/core/game-sim";

/**
 * One NFL game's simulations (migration 0099): the pregame run with its
 * replay, frozen at kickoff, and the live run (no replay) while the game is on.
 * Either may be missing: a game with no projection is never simulated.
 */
export async function getGameSims(
  gameId: number,
): Promise<{ pregame: GameSim | null; live: GameSim | null }> {
  const supabase = createServerSupabaseClient();
  const [pre, live] = await Promise.all([
    supabase
      .from("game_sims")
      .select(GAME_SIM_REPLAY_COLUMNS)
      .eq("game_id", gameId)
      .eq("stage", "pregame"),
    supabase
      .from("game_sims")
      .select(GAME_SIM_COLUMNS)
      .eq("game_id", gameId)
      .eq("stage", "live"),
  ]);
  const preRow = unwrap<DbRow[]>(pre, "game_sims")[0];
  const liveRow = unwrap<DbRow[]>(live, "game_sims")[0];
  return {
    pregame: preRow ? toGameSim(preRow) : null,
    live: liveRow ? toGameSim(liveRow) : null,
  };
}

/**
 * The first quarter's and first half's markets against one book each
 * (migrations 0100, 0101), as the site shows them, with each book's name.
 */
export async function getSimEdges(
  gameId: number,
): Promise<{ edges: SimEdge[]; books: Record<number, string> }> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_game_sim_edge_results")
      .select(SIM_EDGE_COLUMNS)
      .eq("game_id", gameId),
    "v_game_sim_edge_results",
  );
  const edges = rows.map(toSimEdge);
  const ids = [...new Set(edges.map((e) => e.sportsbookId))];
  if (ids.length === 0) return { edges, books: {} };
  const bookRows = unwrap<DbRow[]>(
    await supabase.from("sportsbooks").select("id, display_name").in("id", ids),
    "sportsbooks",
  );
  const books: Record<number, string> = {};
  for (const b of bookRows) books[Number(b.id)] = String(b.display_name);
  return { edges, books };
}
