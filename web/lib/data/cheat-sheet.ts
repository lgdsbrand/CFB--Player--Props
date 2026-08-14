/**
 * The cheat sheet read.
 *
 * `v_cheat_sheet` (migration 0045) does the grading; this applies the sheet's
 * inclusion rules and hands back rows in tier order. The rules themselves are
 * stated and tested in `lib/core/cheat-sheet.ts` — what is here is their
 * translation into predicates the DATABASE applies.
 *
 * THEY HAVE TO BE APPLIED IN THE QUERY, not after the fetch. A week holds ~5,200
 * graded rows across both windows and PostgREST caps a response at 1,000
 * silently, so filtering in the page would filter an arbitrary truncation of the
 * slate and drop qualifying players off the sheet in a way indistinguishable
 * from them not qualifying. Same reason the board pushes every filter down.
 *
 * ALWAYS PINNED TO ONE SEASON AND WEEK. Unfiltered, the view grades every priced
 * prop in the database; the planner pushes these two predicates into
 * `v_board_rows` before the per-prop grading runs, which is what keeps the read
 * at ~800 ms on the heaviest week measured.
 */

import { minDecidedFor, type CheatSheetRow } from "@/lib/core/cheat-sheet";
import { DEFAULT_SPORT, type Sport } from "@/lib/core/sport";
import type { BetSide, PositionGroup } from "@/lib/core/types";
import { type DbRow, MAX_ROWS_PER_REQUEST, num, unwrap } from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

const COLUMNS =
  "projection_id, pick_id, sport, season, week, player_id, player_name, " +
  "position_group, team_id, team_school, team_abbreviation, team_color, " +
  "team_alt_color, opponent_team_id, opponent_school, opponent_abbreviation, " +
  "opponent_rank_vs_position, game_id, start_date, is_home, neutral_site, " +
  "market_key, market_label, market_emoji, is_binary, line, model_side, " +
  "display_confidence, edge, has_call, has_book_line, sportsbook_key, " +
  "conference_name, conference_is_displayed, window_size, decided, pushes, " +
  "hits, hit_side, hit_rate";

/**
 * The sheet's ceiling, in rows.
 *
 * Measured on 2025 week 12, the fullest week in the database: 405 rows clear 80%
 * at L5. 600 leaves headroom for a busier slate while staying under the 1,000-row
 * response cap, so a full sheet is one request. `truncated` is reported rather
 * than assumed away — a silently clipped cheat sheet is a list of "the best
 * plays" that is missing some.
 */
const SHEET_LIMIT = 600;

export type CheatSheetPage = {
  rows: CheatSheetRow[];
  /** True when more rows qualified than were returned. */
  truncated: boolean;
  /** Every qualifying row, before the limit — the number the header reports. */
  total: number;
};

export type CheatSheetFilters = {
  season: number;
  week: number;
  windowSize: number;
  sport?: Sport;
  positionGroup?: PositionGroup;
  /** The floor to read at. Defaults to the lowest tier the page shows. */
  minHitRate?: number;
};

export async function getCheatSheet(
  filters: CheatSheetFilters,
): Promise<CheatSheetPage> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("v_cheat_sheet")
    .select(COLUMNS, { count: "exact" })
    .eq("sport", filters.sport ?? DEFAULT_SPORT)
    .eq("season", filters.season)
    .eq("week", filters.week)
    .eq("window_size", filters.windowSize)
    // The board's scope (CLAUDE.md §7). A sheet entry whose player the board
    // will not show is a dead end, and this product has shipped that link three
    // times — see `lib/core/board-scope.ts`.
    .eq("conference_is_displayed", true)
    .gte("hit_rate", filters.minHitRate ?? 0.8)
    // The floor under the denominator. Without it, week 3 puts every player who
    // has played twice on the perfect list.
    .gte("decided", minDecidedFor(filters.windowSize))
    // "Has failed to score in five straight" is true of most of the slate and
    // is not a play. Stated in `qualifies`; this is the same rule as a
    // predicate, because the row cap makes it wrong to apply it afterwards.
    .or("is_binary.eq.false,hit_side.eq.over");

  if (filters.positionGroup) {
    query = query.eq("position_group", filters.positionGroup);
  }

  // Mirrors `compareRows` in the core. The two must agree: this decides which
  // rows survive the limit, and that one decides the order they are shown in.
  const result = await query
    .order("hit_rate", { ascending: false })
    .order("decided", { ascending: false })
    .order("display_confidence", { ascending: false, nullsFirst: false })
    .order("projection_id", { ascending: true })
    .limit(Math.min(SHEET_LIMIT, MAX_ROWS_PER_REQUEST));

  const rows = unwrap<DbRow[]>(result, "v_cheat_sheet");
  const total = result.count ?? rows.length;

  return {
    rows: rows.map(toCheatSheetRow),
    truncated: total > rows.length,
    total,
  };
}

/**
 * Why the sheet came back empty — read ONLY when it did.
 *
 * NEITHER COUNT TOUCHES `v_cheat_sheet`, and that is the point. The obvious
 * version asked the same view how many rows it had before the rate filters, and
 * it cost as much as the query that had just returned nothing: measured on dev,
 * ~3 s per count, and running two of them on an empty week was enough to hang
 * the page while the connection pool drained. Both facts are available from
 * indexed columns instead:
 *
 *   pricedProps  does any prop on this slate carry a line to grade against
 *   playedGames  has anyone played a game this season yet
 *
 * Between them they separate the two structural empties from the ordinary one,
 * which is all the page needs to say something true.
 */
export async function getCheatSheetContext(
  season: number,
  week: number,
): Promise<{ pricedProps: number; playedGames: number }> {
  const supabase = createServerSupabaseClient();

  const [priced, played] = await Promise.all([
    supabase
      .from("v_board_rows")
      .select("projection_id", { count: "exact", head: true })
      .eq("sport", DEFAULT_SPORT)
      .eq("season", season)
      .eq("week", week)
      .eq("conference_is_displayed", true)
      .not("line", "is", null),
    // Strictly earlier weeks, matching what the grading may look at. A box
    // score from the week being predicted is not material a hit rate may use.
    supabase
      .from("v_player_game_log")
      .select("player_id", { count: "exact", head: true })
      .eq("season", season)
      .lt("week", week),
  ]);

  for (const [result, label] of [
    [priced, "priced"],
    [played, "played"],
  ] as const) {
    if (result.error) {
      throw new Error(
        `Cheat sheet context failed (${label}): ${result.error.message}`,
      );
    }
  }

  return { pricedProps: priced.count ?? 0, playedGames: played.count ?? 0 };
}

function toCheatSheetRow(row: DbRow): CheatSheetRow {
  return {
    projectionId: row.projection_id as number,
    season: row.season as number,
    week: row.week as number,

    playerId: row.player_id as number,
    playerName: row.player_name as string,
    positionGroup: (row.position_group as PositionGroup | null) ?? null,

    teamId: row.team_id as number,
    teamSchool: row.team_school as string,
    teamAbbreviation: (row.team_abbreviation as string | null) ?? null,
    teamColor: (row.team_color as string | null) ?? null,
    teamAltColor: (row.team_alt_color as string | null) ?? null,

    opponentSchool: row.opponent_school as string,
    opponentAbbreviation: (row.opponent_abbreviation as string | null) ?? null,
    opponentRankVsPosition: num(row.opponent_rank_vs_position),

    gameId: row.game_id as number,
    startDate: (row.start_date as string | null) ?? null,
    isHome: row.is_home as boolean,
    neutralSite: row.neutral_site as boolean,

    marketKey: row.market_key as string,
    marketLabel: (row.market_label as string | null) ?? null,
    marketEmoji: (row.market_emoji as string | null) ?? null,
    isBinary: row.is_binary as boolean,
    // `numeric` arrives from PostgREST as a STRING, so every one of these is
    // coerced rather than cast — a cast typechecks and then compares "0.8" to
    // 0.8 as a string at runtime.
    line: num(row.line),

    modelSide: (row.model_side as BetSide | null) ?? null,
    displayConfidence: num(row.display_confidence),
    edge: num(row.edge),
    hasCall: row.has_call as boolean,
    hasBookLine: row.has_book_line as boolean,
    sportsbookKey: (row.sportsbook_key as string | null) ?? null,

    windowSize: row.window_size as number,
    decided: row.decided as number,
    pushes: row.pushes as number,
    hits: row.hits as number,
    hitSide: row.hit_side as BetSide,
    hitRate: num(row.hit_rate) ?? 0,
  };
}
