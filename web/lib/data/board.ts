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
import { type BoardSort, boardSortKeys } from "@/lib/core/board-view";
import type { BetSide, BoardRow, PositionGroup } from "@/lib/core/types";
import { SYNTHETIC_BOOK_KEY } from "@/lib/data/odds";
import { type DbRow, MAX_ROWS_PER_REQUEST, unwrap } from "@/lib/data/query";

/**
 * The sort switch lives in the core (`lib/core/board-view.ts`) because the
 * ORDER BY chain is a decision worth testing without a database. Re-exported
 * here so call sites keep importing it beside the query it configures.
 */
export type { BoardSort };

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
  /**
   * Only rows at or above this confidence, e.g. 0.6. Measured on
   * `display_confidence` — the number the card shows — so the control cannot
   * admit a row the reader reads as 5%.
   */
  minConfidence?: number;
  /**
   * Only matchups against a defense ranked this poorly or worse — i.e. only
   * SOFT matchups. Rank 1 is the BEST defense (the conventional reading), so
   * softer means a LARGER number.
   */
  minOpponentRank?: number;
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
  "opponent_rank_vs_position, conference_name, conference_is_displayed, " +
  "display_confidence, effective_sample, venue_name, venue_city, venue_state, " +
  "team_spread, game_total, game_line_providers";

export type BoardPage = {
  rows: BoardRow[];
  /** Total matching rows, before limit/offset — for pagination and counts. */
  total: number;
};

/**
 * Build a filtered board query.
 *
 * ONE definition of "which rows match", shared by the row read and the card-key
 * scan. Those two must agree exactly — the scan decides which players make the
 * page and the read fills their cards — and two copies of a dozen predicates
 * would eventually disagree about an edge case and paginate wrongly.
 */
function buildBoardQuery(select: string, filters: BoardFilters, count?: "exact") {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("v_board_rows")
    .select(select, count ? { count } : undefined)
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
    query = query.gte("display_confidence", filters.minConfidence);
  }
  // Rank 1 is the BEST defense, so "only show me soft matchups" is a FLOOR on
  // the rank, not a ceiling. This was `lte` on a field called maxOpponentRank
  // under the old convention; renaming it forced every call site to be looked
  // at rather than quietly leaving a filter that now selects the exact
  // opposite set of rows while still type-checking.
  if (filters.minOpponentRank !== undefined) {
    query = query.gte("opponent_rank_vs_position", filters.minOpponentRank);
  }

  return query;
}

type BoardQuery = ReturnType<typeof buildBoardQuery>;

/**
 * Apply the sort switch (CLAUDE.md §7).
 *
 * The chain itself — including why every sort ends in opponent rank before the
 * id tiebreak — is `boardSortKeys` in the core. This only translates it into
 * PostgREST calls.
 *
 * Opponent rank sorts DESCENDING wherever it appears. Rank 1 is the BEST
 * defense, and nobody sorts a props board to see the toughest matchups first.
 */
function applySort(query: BoardQuery, sort: BoardSort = "edge"): BoardQuery {
  let sorted = query;
  for (const key of boardSortKeys(sort)) {
    sorted = sorted.order(key.column, {
      ascending: key.ascending,
      nullsFirst: key.nullsFirst,
    });
  }
  return sorted;
}

export async function getBoardRows(filters: BoardFilters): Promise<BoardPage> {
  const limit = Math.min(filters.limit ?? 200, MAX_ROWS_PER_REQUEST);
  const offset = filters.offset ?? 0;

  const query = applySort(
    buildBoardQuery(COLUMNS, filters, "exact"),
    filters.sort,
  ).range(offset, offset + limit - 1);

  const result = await query;
  const rows = unwrap<DbRow[]>(result, "v_board_rows");

  return {
    rows: rows.map(toBoardRow),
    total: result.count ?? rows.length,
  };
}

export type CardKey = { playerId: number; gameId: number };

export type CardKeyPage = {
  keys: CardKey[];
  /** True when the scan hit the row cap, so `keys` is not the whole week. */
  truncated: boolean;
};

/**
 * The player-games matching the filters, in board order.
 *
 * WHY THIS EXISTS RATHER THAN PAGINATING ROWS. The board is one card per
 * player, but the read layer returns one row per player-MARKET. Slicing rows
 * into pages would cut a player's markets across a page boundary, so a card
 * would silently show three of a player's five markets and look complete.
 *
 * So pagination works in two steps: this establishes which players belong on
 * the page and in what order, and `getRowsForCards` then fetches every row for
 * exactly those players. Two cheap queries instead of one wrong one.
 *
 * Selecting two columns keeps the scan light even at the row cap.
 */
/**
 * Ceiling on the card-key scan, in rows.
 *
 * Deduplicating player-games means scanning every matching ROW, and PostgREST
 * returns at most `MAX_ROWS_PER_REQUEST` per response regardless of the range
 * asked for — so a whole week takes several requests.
 *
 * THE BUG THIS REPLACES IS WORTH RECORDING. A single request with a 2,000-row
 * range returned 1,000 rows and a count of 3,788, without error. The board
 * showed 694 cards unfiltered and 827 when filtered to one market — MORE cards
 * from a narrower query, because fewer rows per player fit under the cap. A
 * filter that increases the result count is what silent truncation looks like
 * from the outside.
 *
 * The largest week ingested is ~7,000 rows across all FBS; this clears it.
 */
const KEY_SCAN_MAX_ROWS = 12000;

export async function getBoardCardKeys(
  filters: BoardFilters,
  maxRows: number = KEY_SCAN_MAX_ROWS,
): Promise<CardKeyPage> {
  const chunk = (offset: number, withCount: boolean) =>
    applySort(
      buildBoardQuery("player_id, game_id", filters, withCount ? "exact" : undefined),
      filters.sort,
    ).range(offset, offset + MAX_ROWS_PER_REQUEST - 1);

  // The first request also reports the exact total, which is what says how many
  // more are needed — rather than paging until a short response, which cannot
  // tell "that was the end" from "the server capped me".
  const head = await chunk(0, true);
  const first = unwrap<DbRow[]>(head, "v_board_rows (card keys)");
  const total = head.count ?? first.length;

  const wanted = Math.min(total, maxRows);
  const offsets: number[] = [];
  for (
    let offset = MAX_ROWS_PER_REQUEST;
    offset < wanted;
    offset += MAX_ROWS_PER_REQUEST
  ) {
    offsets.push(offset);
  }

  // Order is deterministic (the sort ends in a projection_id tiebreak), so the
  // chunks can be fetched in parallel and concatenated by offset.
  const rest = await Promise.all(
    offsets.map(async (offset) =>
      unwrap<DbRow[]>(await chunk(offset, false), "v_board_rows (card keys)"),
    ),
  );

  const seen = new Set<string>();
  const keys: CardKey[] = [];
  let scanned = 0;
  for (const rows of [first, ...rest]) {
    scanned += rows.length;
    for (const row of rows) {
      const playerId = row.player_id as number;
      const gameId = row.game_id as number;
      const id = `${playerId}-${gameId}`;
      if (seen.has(id)) continue;
      seen.add(id);
      keys.push({ playerId, gameId });
    }
  }

  return { keys, truncated: total > scanned };
}

/**
 * Every row for a specific set of player-games.
 *
 * The market and position filters still apply — they decide which sub-cards a
 * card shows — but the row-level filters that pick WHICH players make the page
 * (edge, confidence, rank) deliberately do NOT. A player who earns his place
 * with a 72% receiving-yards call should show his other markets too; hiding
 * them would make the card claim he has one market this week.
 */
export async function getRowsForCards(
  keys: CardKey[],
  filters: Pick<
    BoardFilters,
    "season" | "week" | "marketKey" | "positionGroup"
  >,
): Promise<BoardRow[]> {
  if (keys.length === 0) return [];

  const supabase = createServerSupabaseClient();
  let query = supabase
    .from("v_board_rows")
    .select(COLUMNS)
    .eq("season", filters.season)
    .eq("week", filters.week)
    .in("player_id", [...new Set(keys.map((key) => key.playerId))])
    .in("game_id", [...new Set(keys.map((key) => key.gameId))]);

  if (filters.marketKey) query = query.eq("market_key", filters.marketKey);
  if (filters.positionGroup) {
    query = query.eq("position_group", filters.positionGroup);
  }

  const rows = unwrap<DbRow[]>(
    await query.order("market_key"),
    "v_board_rows (cards)",
  );

  // The `in` pair above is a cross product, so it can return a player paired
  // with someone else's game. Keep only the pairs actually asked for.
  const wanted = new Set(keys.map((key) => `${key.playerId}-${key.gameId}`));
  return rows
    .map(toBoardRow)
    .filter((row) => wanted.has(`${row.playerId}-${row.gameId}`));
}

export type BoardCounts = {
  rows: number;
  withCall: number;
  withBookLine: number;
  /**
   * Book lines written by the synthetic DEV book, counted separately because
   * they are not evidence a market exists. See `lineCoverage`.
   */
  withDevLine: number;
  overThreshold: number;
};

/**
 * Headline counts for a week, without transferring the rows.
 *
 * `head: true` asks PostgREST for the count and no body. Cheap queries beat one
 * that ships 6,000 rows so the page can call `.length` on them — but each one is
 * still a round trip the board waits on, so nothing is counted here that nothing
 * renders. Two counts (`thin` and `ranked`) were dropped when the slate-level
 * evidence note was removed at the client's request; they had no other reader.
 *
 * THESE ARE RAW COUNTS OF DISJOINT CONDITIONS AND NOTHING ELSE. Turning them
 * into "how many are still waiting for a book" is `lineCoverage` in the core,
 * where it is tested — a subtraction done inline in the page is exactly how the
 * board came to describe its structural-line rows as awaiting a line.
 *
 * `withDevLine` exists because "a line is attached" and "a market exists" are
 * different facts while `--synthetic-lines` is in use, and the board has to be
 * able to say which it is looking at.
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

  const [all, withCall, withBookLine, withDevLine, overThreshold] =
    await Promise.all([
      base(),
      base().eq("has_call", true),
      base().eq("has_book_line", true),
      base().eq("sportsbook_key", SYNTHETIC_BOOK_KEY),
      base().gte("edge", edgeThreshold),
    ]);

  for (const [result, label] of [
    [all, "all"],
    [withCall, "with call"],
    [withBookLine, "with book line"],
    [withDevLine, "with development line"],
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
    withDevLine: withDevLine.count ?? 0,
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

    venueName: (row.venue_name as string | null) ?? null,
    venueCity: (row.venue_city as string | null) ?? null,
    venueState: (row.venue_state as string | null) ?? null,

    teamSpread: (row.team_spread as number | null) ?? null,
    gameTotal: (row.game_total as number | null) ?? null,
    gameLineProviders: (row.game_line_providers as number | null) ?? null,

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
    effectiveSample: (row.effective_sample as number | null) ?? null,

    opponentRankVsPosition:
      (row.opponent_rank_vs_position as number | null) ?? null,

    conferenceName: (row.conference_name as string | null) ?? null,
    conferenceIsDisplayed:
      (row.conference_is_displayed as boolean | null) ?? null,

    displayConfidence: (row.display_confidence as number | null) ?? null,
  };
}
