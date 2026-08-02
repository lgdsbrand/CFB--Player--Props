/**
 * The main board read.
 *
 * One row per PROJECTION with the pick left-joined on, which is what makes the
 * board useful before books post (CLAUDE.md §7). Every pick-derived field is
 * therefore nullable and `hasCall` discriminates — see `BoardRow`.
 *
 * FILTERING HAPPENS IN POSTGRES, NOT IN THE PAGE. A week holds ~6,000 rows and
 * PostgREST caps a response at 1,000 silently, so a filter applied after the
 * fetch would operate on an arbitrary truncation of the week and quietly show
 * the wrong players. Every filter below is pushed into the query for that
 * reason, not for speed.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { BetSide, BoardRow, PositionGroup } from "@/lib/core/types";
import { type DbRow, MAX_ROWS, unwrap } from "@/lib/data/query";

/** How the board orders rows. Mirrors the sort-priority switch in §7. */
export type BoardSort = "edge" | "confidence" | "opponent_rank";

export type BoardFilters = {
  season: number;
  week: number;

  marketKey?: string;
  positionGroup?: PositionGroup;
  gameId?: number;
  /** Case-insensitive substring on the player's name. */
  search?: string;

  /**
   * Restrict to the conferences marked for display. Defaults to true: the
   * board is scoped to the five conferences in CLAUDE.md §7, while ingest
   * deliberately covers all FBS. Pass false to see everything ingested.
   */
  displayedConferencesOnly?: boolean;
  conferenceName?: string;

  /** Only rows whose edge clears the threshold — the EDGES ONLY toggle. */
  edgesOnly?: boolean;
  edgeThreshold?: number;
  /** Only rows at or above this confidence, e.g. 0.6. */
  minConfidence?: number;
  /**
   * Only matchups at or better than this rank vs the position. Rank 1 allows
   * the MOST, so "better" means a SMALLER number — the scale is inverted
   * relative to a conventional defensive ranking and anything surfacing it
   * has to label it.
   */
  maxOpponentRank?: number;
  /** Only rows that have a book line attached. */
  withBookLineOnly?: boolean;

  sort?: BoardSort;
  limit?: number;
  offset?: number;
};

const COLUMNS =
  "projection_id, pick_id, season, week, market_key, market_name, market_label, " +
  "market_emoji, is_binary, player_id, player_name, position_group, team_id, " +
  "team_school, team_abbreviation, team_color, team_alt_color, opponent_team_id, " +
  "opponent_school, opponent_abbreviation, game_id, start_date, neutral_site, " +
  "is_home, line, side, confidence, model_prob_over, book_prob_over, edge, " +
  "has_book_line, has_call, over_price, under_price, sportsbook_key, " +
  "sportsbook_name, projected_median, projected_p10, projected_p90, prior_weight, " +
  "opponent_rank_vs_position, conference_name, conference_is_displayed";

export type BoardPage = {
  rows: BoardRow[];
  /** Total matching rows, before limit/offset — for pagination and counts. */
  total: number;
};

export async function getBoardRows(filters: BoardFilters): Promise<BoardPage> {
  const supabase = createServerSupabaseClient();
  const limit = Math.min(filters.limit ?? 200, MAX_ROWS);
  const offset = filters.offset ?? 0;

  let query = supabase
    .from("v_board_rows")
    .select(COLUMNS, { count: "exact" })
    .eq("season", filters.season)
    .eq("week", filters.week);

  if (filters.marketKey) query = query.eq("market_key", filters.marketKey);
  if (filters.positionGroup) {
    query = query.eq("position_group", filters.positionGroup);
  }
  if (filters.gameId) query = query.eq("game_id", filters.gameId);
  if (filters.conferenceName) {
    query = query.eq("conference_name", filters.conferenceName);
  } else if (filters.displayedConferencesOnly !== false) {
    query = query.eq("conference_is_displayed", true);
  }

  if (filters.search) {
    // `ilike` with escaped wildcards: a player named "%" is not a query.
    const escaped = filters.search.replace(/[%_]/g, (c) => `\\${c}`);
    query = query.ilike("player_name", `%${escaped}%`);
  }

  if (filters.withBookLineOnly) query = query.eq("has_book_line", true);

  if (filters.edgesOnly) {
    // A null edge means "no edge computable", not "below threshold" — gte on a
    // null column already excludes those rows, which is the behaviour we want.
    query = query.gte("edge", filters.edgeThreshold ?? 0.05);
  }
  if (filters.minConfidence !== undefined) {
    query = query.gte("confidence", filters.minConfidence);
  }
  if (filters.maxOpponentRank !== undefined) {
    query = query.lte("opponent_rank_vs_position", filters.maxOpponentRank);
  }

  // `nullsFirst: false` everywhere: a row with no book line has a null edge and
  // a row with an unrated opponent has a null rank. Neither is a strong pick,
  // and Postgres sorts NULLs first on DESC by default, which would put exactly
  // the least informative rows at the top of the board.
  const desc = { ascending: false, nullsFirst: false } as const;
  switch (filters.sort ?? "edge") {
    case "confidence":
      query = query.order("confidence", desc);
      break;
    case "opponent_rank":
      query = query.order("opponent_rank_vs_position", {
        ascending: true,
        nullsFirst: false,
      });
      break;
    default:
      query = query.order("edge", desc).order("confidence", desc);
  }
  // A stable tiebreak, so paging through a week cannot repeat or skip a row.
  query = query.order("projection_id", { ascending: true });
  query = query.range(offset, offset + limit - 1);

  const result = await query;
  const rows = unwrap<DbRow[]>(result, "v_board_rows");

  return {
    rows: rows.map(toBoardRow),
    total: result.count ?? rows.length,
  };
}

export type BoardCounts = {
  rows: number;
  withCall: number;
  withBookLine: number;
  overThreshold: number;
};

/**
 * Headline counts for a week, without transferring the rows.
 *
 * `head: true` asks PostgREST for the count and no body. Four cheap queries
 * beat one that ships 6,000 rows so the page can call `.length` on them.
 *
 * `withCall` minus `withBookLine` is the population that matters most right
 * now: rows the model has an opinion on that no book has priced. Before
 * Thursday in a live week that is nearly all of them.
 */
export async function getBoardCounts(
  season: number,
  week: number,
  edgeThreshold: number,
  { displayedConferencesOnly = true }: { displayedConferencesOnly?: boolean } = {},
): Promise<BoardCounts> {
  const supabase = createServerSupabaseClient();

  const base = () => {
    const query = supabase
      .from("v_board_rows")
      .select("projection_id", { count: "exact", head: true })
      .eq("season", season)
      .eq("week", week);
    return displayedConferencesOnly
      ? query.eq("conference_is_displayed", true)
      : query;
  };

  const [all, withCall, withBookLine, overThreshold] = await Promise.all([
    base(),
    base().eq("has_call", true),
    base().eq("has_book_line", true),
    base().gte("edge", edgeThreshold),
  ]);

  for (const [result, label] of [
    [all, "all"],
    [withCall, "with call"],
    [withBookLine, "with book line"],
    [overThreshold, "over threshold"],
  ] as const) {
    if (result.error) {
      throw new Error(`Board count failed (${label}): ${result.error.message}`);
    }
  }

  return {
    rows: all.count ?? 0,
    withCall: withCall.count ?? 0,
    withBookLine: withBookLine.count ?? 0,
    overThreshold: overThreshold.count ?? 0,
  };
}

/** One player's rows for a week — every market, for the detail view. */
export async function getPlayerBoardRows(
  playerId: number,
  season: number,
  week: number,
): Promise<BoardRow[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_board_rows")
      .select(COLUMNS)
      .eq("player_id", playerId)
      .eq("season", season)
      .eq("week", week)
      .order("market_key"),
    "v_board_rows (player)",
  );
  return rows.map(toBoardRow);
}

function toBoardRow(row: Record<string, unknown>): BoardRow {
  return {
    projectionId: row.projection_id as number,
    pickId: (row.pick_id as number | null) ?? null,

    season: row.season as number,
    week: row.week as number,

    marketKey: row.market_key as string,
    marketName: row.market_name as string,
    marketLabel: (row.market_label as string | null) ?? null,
    marketEmoji: (row.market_emoji as string | null) ?? null,
    isBinary: row.is_binary as boolean,

    playerId: row.player_id as number,
    playerName: row.player_name as string,
    positionGroup: (row.position_group as PositionGroup | null) ?? null,

    teamId: row.team_id as number,
    teamSchool: row.team_school as string,
    teamAbbreviation: (row.team_abbreviation as string | null) ?? null,
    teamColor: (row.team_color as string | null) ?? null,
    teamAltColor: (row.team_alt_color as string | null) ?? null,

    opponentTeamId: row.opponent_team_id as number,
    opponentSchool: row.opponent_school as string,
    opponentAbbreviation: (row.opponent_abbreviation as string | null) ?? null,

    gameId: row.game_id as number,
    startDate: (row.start_date as string | null) ?? null,
    neutralSite: row.neutral_site as boolean,
    isHome: row.is_home as boolean,

    line: (row.line as number | null) ?? null,
    side: (row.side as BetSide | null) ?? null,
    confidence: (row.confidence as number | null) ?? null,
    modelProbOver: (row.model_prob_over as number | null) ?? null,
    bookProbOver: (row.book_prob_over as number | null) ?? null,
    edge: (row.edge as number | null) ?? null,

    hasBookLine: row.has_book_line as boolean,
    hasCall: row.has_call as boolean,

    overPrice: (row.over_price as number | null) ?? null,
    underPrice: (row.under_price as number | null) ?? null,
    sportsbookKey: (row.sportsbook_key as string | null) ?? null,
    sportsbookName: (row.sportsbook_name as string | null) ?? null,

    projectedMedian: (row.projected_median as number | null) ?? null,
    projectedP10: (row.projected_p10 as number | null) ?? null,
    projectedP90: (row.projected_p90 as number | null) ?? null,
    priorWeight: (row.prior_weight as number | null) ?? null,

    opponentRankVsPosition:
      (row.opponent_rank_vs_position as number | null) ?? null,

    conferenceName: (row.conference_name as string | null) ?? null,
    conferenceIsDisplayed:
      (row.conference_is_displayed as boolean | null) ?? null,
  };
}
