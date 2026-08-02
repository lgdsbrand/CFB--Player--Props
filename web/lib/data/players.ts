/**
 * Player game logs — the actuals behind the hit-rate chart and splits.
 *
 * `player_game_stats` is the single home for realised outcomes, and
 * `v_player_game_log` is its display shape. Reading actuals here is not the
 * lookahead the schema guards against: a completed game is a fact, and the
 * cutoff rules govern what may FEED a projection, not what may be shown next to
 * one after the fact.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { PlayerGameLogRow, PositionGroup } from "@/lib/core/types";
import { type DbRow, unwrap } from "@/lib/data/query";

const COLUMNS =
  "player_id, game_id, season, week, position_group, is_home, " +
  "opponent_abbreviation, opponent_school, start_date, neutral_site, " +
  "pass_attempts, pass_completions, pass_yards, pass_tds, interceptions, " +
  "rush_attempts, rush_yards, rush_tds, targets, receptions, rec_yards, " +
  "rec_tds, offensive_tds";

/**
 * One player's completed games, most recent first.
 *
 * `before` restricts to games strictly earlier than a week, which is what the
 * board needs: showing week 10's own result beside a week 10 projection would
 * be marking the model's homework with the answer sheet visible. Omit it on a
 * page reviewing a finished week.
 */
export async function getPlayerGameLog(
  playerId: number,
  {
    season,
    before,
    limit = 20,
  }: { season?: number; before?: number; limit?: number } = {},
): Promise<PlayerGameLogRow[]> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("v_player_game_log")
    .select(COLUMNS)
    .eq("player_id", playerId)
    .order("season", { ascending: false })
    .order("week", { ascending: false })
    .limit(limit);

  if (season !== undefined) query = query.eq("season", season);
  if (before !== undefined) query = query.lt("week", before);

  return unwrap<DbRow[]>(await query, "v_player_game_log").map(toGameLogRow);
}

function toGameLogRow(row: Record<string, unknown>): PlayerGameLogRow {
  const n = (key: string) => (row[key] as number | null) ?? null;
  return {
    playerId: row.player_id as number,
    gameId: row.game_id as number,
    season: row.season as number,
    week: row.week as number,
    positionGroup: (row.position_group as PositionGroup | null) ?? null,
    isHome: row.is_home as boolean,
    opponentAbbreviation: (row.opponent_abbreviation as string | null) ?? null,
    opponentSchool: row.opponent_school as string,
    startDate: (row.start_date as string | null) ?? null,
    neutralSite: row.neutral_site as boolean,

    passAttempts: n("pass_attempts"),
    passCompletions: n("pass_completions"),
    passYards: n("pass_yards"),
    passTds: n("pass_tds"),
    interceptions: n("interceptions"),
    rushAttempts: n("rush_attempts"),
    rushYards: n("rush_yards"),
    rushTds: n("rush_tds"),
    targets: n("targets"),
    receptions: n("receptions"),
    recYards: n("rec_yards"),
    recTds: n("rec_tds"),
    offensiveTds: n("offensive_tds"),
  };
}
