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
import type {
  DefenseGameRow,
  DefenseSplitRow,
  PositionGroup,
} from "@/lib/core/types";
import { type DbRow, MAX_ROWS_PER_REQUEST, unwrap } from "@/lib/data/query";

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

const GAME_LOG_COLUMNS =
  "split_id, game_id, defense_team_id, offense_team_id, offense_school, " +
  "offense_abbreviation, season, week, position_group, start_date, " +
  "neutral_site, defense_is_home, plays, rush_attempts, rush_yards_allowed, " +
  "rush_tds_allowed, targets, receptions_allowed, rec_yards_allowed, " +
  "rec_tds_allowed";

/**
 * One defense's game-by-game allowances to one position — the defense detail
 * panel (CLAUDE.md §7).
 *
 * `before` is the same strict cutoff the rest of the app uses: entering week N,
 * only weeks 1..N-1 happened. It is a parameter rather than a default because
 * the caller's cutoff is the caller's to state — but note there is deliberately
 * no way to ask for "the season so far" without naming a week.
 */
export async function getDefenseGameLog(
  defenseTeamId: number,
  season: number,
  positionGroup: PositionGroup,
  { before }: { before?: number } = {},
): Promise<DefenseGameRow[]> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("v_defense_position_game_log")
    .select(GAME_LOG_COLUMNS)
    .eq("defense_team_id", defenseTeamId)
    .eq("season", season)
    .eq("position_group", positionGroup)
    .order("week", { ascending: false });

  if (before !== undefined) query = query.lt("week", before);

  return unwrap<DbRow[]>(await query, "v_defense_position_game_log").map(
    (row) => {
      const n = (key: string) => (row[key] as number | null) ?? null;
      return {
        splitId: row.split_id as number,
        gameId: row.game_id as number,
        defenseTeamId: row.defense_team_id as number,
        offenseTeamId: row.offense_team_id as number,
        offenseSchool: row.offense_school as string,
        offenseAbbreviation: (row.offense_abbreviation as string | null) ?? null,
        season: row.season as number,
        week: row.week as number,
        positionGroup: row.position_group as PositionGroup,
        startDate: (row.start_date as string | null) ?? null,
        neutralSite: row.neutral_site as boolean,
        defenseIsHome: row.defense_is_home as boolean,
        plays: n("plays"),
        rushAttempts: n("rush_attempts"),
        rushYardsAllowed: n("rush_yards_allowed"),
        rushTdsAllowed: n("rush_tds_allowed"),
        targets: n("targets"),
        receptionsAllowed: n("receptions_allowed"),
        recYardsAllowed: n("rec_yards_allowed"),
        recTdsAllowed: n("rec_tds_allowed"),
      } satisfies DefenseGameRow;
    },
  );
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
  /** 1 = the BEST defense against this position (the conventional reading). */
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

  const rows = unwrap<DbRow[]>(await query, "defense_position_ratings");

  // All four positions at one cutoff is ~544 rows today (136 FBS × 4), well
  // under the cap — but a truncated read is indistinguishable from a short one,
  // and the failure would be a targets list quietly missing a position. Cheaper
  // to fail loudly than to page a query that should never need it.
  if (rows.length >= MAX_ROWS_PER_REQUEST) {
    throw new Error(
      `defense_position_ratings returned ${rows.length} rows, at or past ` +
        `PostgREST's ${MAX_ROWS_PER_REQUEST}-row cap — the result is probably ` +
        `truncated. Filter by position or page the read.`,
    );
  }

  return rows.map(toRating);
}

/** Key for a (defense, cutoff week) pair. */
export function rankKey(defenseTeamId: number, week: number): string {
  return `${defenseTeamId}@${week}`;
}

/**
 * Ranks for a set of past matchups, each pinned to its OWN week.
 *
 * This is what the vs-rank hit-rate split needs, and the "own week" part is the
 * whole point. Asking how a player did against defenses that ended the season
 * ranked poorly is a question answerable only in hindsight; asking how he did
 * against defenses that LOOKED poor at the time is a question a reader could
 * have acted on. The second is the one the board already asks — `v_board_rows`
 * pins the opponent rank to the projection's week for the same reason.
 *
 * One request rather than one per game: PostgREST treats two `in` filters as a
 * cross product, so this over-fetches (teams × weeks) and discards the pairs it
 * did not ask for. A season's log is ~15 games, so that is a few hundred rows.
 */
export async function getDefenseRanksAt(
  season: number,
  positionGroup: PositionGroup,
  pairs: { defenseTeamId: number; week: number }[],
): Promise<Map<string, number>> {
  const ranks = new Map<string, number>();
  if (pairs.length === 0) return ranks;

  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("defense_position_ratings")
      .select("defense_team_id, as_of_week, rank_vs_position")
      .eq("season", season)
      .eq("position_group", positionGroup)
      .in("defense_team_id", [...new Set(pairs.map((p) => p.defenseTeamId))])
      .in("as_of_week", [...new Set(pairs.map((p) => p.week))]),
    "defense_position_ratings (by week)",
  );

  const wanted = new Set(
    pairs.map((pair) => rankKey(pair.defenseTeamId, pair.week)),
  );
  for (const row of rows) {
    const key = rankKey(
      row.defense_team_id as number,
      row.as_of_week as number,
    );
    const rank = (row.rank_vs_position as number | null) ?? null;
    if (rank !== null && wanted.has(key)) ranks.set(key, rank);
  }
  return ranks;
}

function toRating(row: DbRow): DefenseRating {
  return {
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
  };
}
