import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";
import { OFFENSE_GROUP, type DepthRow, type InjuryRow } from "@/lib/core/starters";
import { cachedRead } from "@/lib/data/cache";

const SKILL_POSITIONS = ["QB", "RB", "WR", "TE"];

/**
 * Both teams' depth charts and injury reports going into one week's game
 * (migration 0086). Keyed by team and week, so no sport filter is needed: a
 * team belongs to one sport, and a college team simply has no rows.
 *
 * Only the offense group's skill positions are read from the depth chart,
 * which is all the page shows: about 40 rows a team rather than 70.
 */
async function readStartersAndInjuries(
  season: number,
  week: number,
  teamIds: number[],
): Promise<{ depth: DepthRow[]; injuries: InjuryRow[] }> {
  if (teamIds.length === 0) return { depth: [], injuries: [] };
  const supabase = createServerSupabaseClient();
  const [depth, injuries] = await Promise.all([
    supabase
      .from("depth_charts")
      .select("team_id, position_group, slot, depth, position, player_id, player_name, source_as_of")
      .eq("season", season)
      .eq("week", week)
      .in("team_id", teamIds)
      .eq("position_group", OFFENSE_GROUP)
      .in("position", SKILL_POSITIONS),
    supabase
      .from("player_injuries")
      .select("team_id, player_id, player_name, position, status, body_part")
      .eq("season", season)
      .eq("week", week)
      .in("team_id", teamIds),
  ]);
  return {
    depth: unwrap<DbRow[]>(depth, "depth_charts").map((r) => ({
      teamId: r.team_id as number,
      positionGroup: r.position_group as string,
      slot: r.slot as number,
      depth: r.depth as number,
      position: r.position as string,
      playerId: (r.player_id as number | null) ?? null,
      playerName: r.player_name as string,
      sourceAsOf: r.source_as_of as string,
    })),
    injuries: unwrap<DbRow[]>(injuries, "player_injuries").map((r) => ({
      teamId: r.team_id as number,
      playerId: (r.player_id as number | null) ?? null,
      playerName: r.player_name as string,
      position: (r.position as string | null) ?? null,
      status: r.status as string,
      bodyPart: (r.body_part as string | null) ?? null,
    })),
  };
}

/**
 * Cached (2026-10-24): these ran uncached on every page view. History reads
 * keep five minutes, odds, sims and live reads one; the browser pollers still
 * refresh live panels between renders.
 */
export const getStartersAndInjuries = cachedRead("starters-and-injuries", readStartersAndInjuries);
