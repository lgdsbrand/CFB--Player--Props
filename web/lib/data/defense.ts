/**
 * What defenses concede by position — the core signal (CLAUDE.md §5).
 *
 * Two shapes, and the difference matters:
 *
 *   `defense_position_splits_through(season, week)` — RAW, cumulative, and
 *   OPPONENT-UNADJUSTED. Actual yards conceded, which is what a game-by-game
 *   panel should show a reader.
 *
 *   `defense_position_ratings` — OPPONENT-ADJUSTED and ranked, keyed by
 *   `as_of_week`. This is what the model consumes and what the "who to target"
 *   list must sort by, because raw numbers flatter a unit that happened to play
 *   three weak offences (CLAUDE.md §5).
 *
 * Showing the adjusted number to a user without saying so would be confusing —
 * it will not match anything they can look up. Showing the raw number as a rank
 * would be wrong. So both are available, labelled.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { DefenseSplitRow, PositionGroup } from "@/lib/core/types";
import { type DbRow, unwrap } from "@/lib/data/query";

/**
 * Cumulative raw allowances to each position, through weeks BEFORE `week`.
 *
 * The strict cutoff is enforced inside the function, which is why the function
 * exists at all: there is deliberately no view returning "current" splits
 * without one, because that is the shape of query that produces lookahead.
 */
export async function getDefenseSplitsThrough(
  season: number,
  week: number,
): Promise<DefenseSplitRow[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase.rpc("defense_position_splits_through", {
      p_season: season,
      p_as_of_week: week,
    }),
    "defense_position_splits_through",
  );

  return rows.map((row) => ({
    defenseTeamId: row.defense_team_id as number,
    positionGroup: row.position_group as PositionGroup,
    gamesIncluded: row.games_included as number,
    rushAttempts: row.rush_attempts as number,
    rushYardsAllowed: row.rush_yards_allowed as number,
    rushTdsAllowed: row.rush_tds_allowed as number,
    targets: row.targets as number,
    receptionsAllowed: row.receptions_allowed as number,
    recYardsAllowed: row.rec_yards_allowed as number,
    recTdsAllowed: row.rec_tds_allowed as number,
    rushYardsAllowedPg: (row.rush_yards_allowed_pg as number | null) ?? null,
    recYardsAllowedPg: (row.rec_yards_allowed_pg as number | null) ?? null,
  }));
}

export type DefenseRating = {
  defenseTeamId: number;
  positionGroup: PositionGroup;
  asOfWeek: number;
  gamesIncluded: number;
  adjRushYardsAllowedPg: number | null;
  adjRecYardsAllowedPg: number | null;
  adjReceptionsAllowedPg: number | null;
  adjRushTdsAllowedPg: number | null;
  adjRecTdsAllowedPg: number | null;
  /** 1 = allows the MOST. Inverted vs a conventional ranking — always label it. */
  rankVsPosition: number | null;
  /** How much the rating is trusted, 0..1. Low early in the season. */
  shrinkageWeight: number | null;
};

/**
 * Opponent-adjusted ratings as they stood entering `week`.
 *
 * Pinned to `as_of_week = week`, never "the latest". A larger value would import
 * knowledge from after the games being displayed; a smaller one is merely stale.
 * Both are bugs, so the read pins equality exactly as the worker's feature
 * queries do.
 */
export async function getDefenseRatings(
  season: number,
  week: number,
  { positionGroup }: { positionGroup?: PositionGroup } = {},
): Promise<DefenseRating[]> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("defense_position_ratings")
    .select(
      "defense_team_id, position_group, as_of_week, games_included, " +
        "adj_rush_yards_allowed_pg, adj_rec_yards_allowed_pg, " +
        "adj_receptions_allowed_pg, adj_rush_tds_allowed_pg, " +
        "adj_rec_tds_allowed_pg, rank_vs_position, shrinkage_weight",
    )
    .eq("season", season)
    .eq("as_of_week", week)
    .order("rank_vs_position", { nullsFirst: false });

  if (positionGroup) query = query.eq("position_group", positionGroup);

  return unwrap<DbRow[]>(await query, "defense_position_ratings").map((row) => ({
    defenseTeamId: row.defense_team_id as number,
    positionGroup: row.position_group as PositionGroup,
    asOfWeek: row.as_of_week as number,
    gamesIncluded: row.games_included as number,
    adjRushYardsAllowedPg: (row.adj_rush_yards_allowed_pg as number | null) ?? null,
    adjRecYardsAllowedPg: (row.adj_rec_yards_allowed_pg as number | null) ?? null,
    adjReceptionsAllowedPg:
      (row.adj_receptions_allowed_pg as number | null) ?? null,
    adjRushTdsAllowedPg: (row.adj_rush_tds_allowed_pg as number | null) ?? null,
    adjRecTdsAllowedPg: (row.adj_rec_tds_allowed_pg as number | null) ?? null,
    rankVsPosition: (row.rank_vs_position as number | null) ?? null,
    shrinkageWeight: (row.shrinkage_weight as number | null) ?? null,
  }));
}
