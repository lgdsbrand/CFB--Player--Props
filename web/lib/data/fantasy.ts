/**
 * The client's NFL fantasy game model for one game (phase F3): both versions
 * of its projection (`fantasy_game_projections`, migration 0094) and the two
 * teams' five starters that week (`fantasy_lineups`, 0093). Two small reads,
 * at most 2 and 10 rows. The book prices come from the page's existing
 * `getGameBookOdds`, so the panel needs no odds read of its own.
 */

import type {
  FantasyProjection,
  FantasyVersion,
  LineupEntry,
  LineupSlot,
} from "@/lib/core/fantasy-view";
import { type DbRow, num, unwrap } from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export interface FantasyGame {
  projections: FantasyProjection[];
  lineups: LineupEntry[];
}

export async function getFantasyGame(
  gameId: number,
  season: number,
  week: number,
  teamIds: number[],
): Promise<FantasyGame> {
  const supabase = createServerSupabaseClient();
  const [projectionRows, lineupRows] = await Promise.all([
    supabase
      .from("fantasy_game_projections")
      .select(
        "model_version, away_score, home_score, total, home_margin, p_home_win, " +
          "away_fantasy, home_fantasy, inputs_as_of_week, made_at",
      )
      .eq("game_id", gameId),
    supabase
      .from("fantasy_lineups")
      .select("team_id, slot, player_name, fantasy_points, points_source, picked_by, skipped")
      .eq("season", season)
      .eq("week", week)
      .in("team_id", teamIds),
  ]);
  return {
    projections: unwrap<DbRow[]>(projectionRows, "fantasy_game_projections").map((r) => ({
      version: r.model_version as FantasyVersion,
      awayScore: Number(r.away_score),
      homeScore: Number(r.home_score),
      total: Number(r.total),
      homeMargin: Number(r.home_margin),
      pHomeWin: Number(r.p_home_win),
      awayFantasy: Number(r.away_fantasy),
      homeFantasy: Number(r.home_fantasy),
      inputsAsOfWeek: Number(r.inputs_as_of_week),
      madeAt: r.made_at as string,
    })),
    lineups: unwrap<DbRow[]>(lineupRows, "fantasy_lineups").map((r) => ({
      teamId: Number(r.team_id),
      slot: r.slot as LineupSlot,
      playerName: (r.player_name as string | null) ?? null,
      fantasyPoints: num(r.fantasy_points) ?? 0,
      pointsSource: r.points_source as LineupEntry["pointsSource"],
      pickedBy: r.picked_by as LineupEntry["pickedBy"],
      skipped: (r.skipped as string | null) ?? null,
    })),
  };
}
