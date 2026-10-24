/**
 * The main board read.
 *
 * One row per PROJECTION with the pick left-joined on, which is what makes the
 * board useful before books post (CLAUDE.md §7). Every pick-derived field is
 * therefore nullable and `hasCall` discriminates — see `BoardRow`.
 *
 * FILTERING HAPPENS ON A CACHED INDEX OF THE WHOLE WEEK, NOT IN POSTGRES
 * (2026-10-24). Every filter used to be pushed into SQL, so every combination of
 * the board's controls was a different statement, and PostgREST keeps every
 * statement it prepares on its connection: 474 of them in 13 minutes, each
 * carrying the expanded view, was the memory that kept restarting production.
 * Now `board_index` (migration 0104) returns the week's filter, sort and count
 * columns as one JSON value, cached for a minute per sport and week; the
 * predicates in `lib/core/board-index.ts` mirror the old SQL ones; and the wide
 * columns are read only for the rows on the page, by id. Any combination of
 * filters costs the same three statements.
 *
 * The old reason for filtering in SQL — PostgREST caps a response at 1,000 rows,
 * so filtering a capped fetch shows the wrong players — does not apply: the
 * index is a single scalar result holding every row of the week.
 */

import { cache } from "react";

import { createServerSupabaseClient } from "@/lib/supabase/server";
import {
  type IndexFilters,
  type IndexRow,
  countIndex,
  decodeIndex,
  matchesBoardFilters,
  matchesCardFilters,
  sortIndexRows,
} from "@/lib/core/board-index";
import type { BoardSort } from "@/lib/core/board-view";
import { parseLadder } from "@/lib/core/ladder-view";
import { DEFAULT_SPORT, type Sport } from "@/lib/core/sport";
import type { BetSide, BoardRow, PositionGroup } from "@/lib/core/types";
import { SYNTHETIC_BOOK_KEY } from "@/lib/data/odds";
import { type DbRow, MAX_ROWS_PER_REQUEST, num, unwrap } from "@/lib/data/query";
import { cachedLiveRead } from "@/lib/data/cache";

/**
 * The sort switch lives in the core (`lib/core/board-view.ts`) because the
 * ORDER BY chain is a decision worth testing without a database. Re-exported
 * here so call sites keep importing it beside the query it configures.
 */
export type { BoardSort };

export type BoardFilters = {
  season: number;
  week: number;

  /**
   * Defaults to `DEFAULT_SPORT`. Every caller omits it today — there is one
   * sport in the database and no toggle — but the predicate is applied
   * regardless, because the failure it prevents is silent. See `core/sport.ts`.
   */
  sport?: Sport;

  marketKey?: string;
  /**
   * Restrict to exactly these markets — how a board scope is expressed.
   *
   * ALWAYS SET IN PRACTICE, and never as "the ones I do not want". The full
   * board sends the full-game keys and the first-quarter board sends the
   * first-quarter ones, so neither is the absence of the other and a market
   * added to the catalogue cannot silently appear in a list that was defined by
   * exclusion. See `lib/core/market-scope.ts`.
   *
   * An EMPTY ARRAY means no market qualifies and the board is empty. That is a
   * real answer — a sport with no first-quarter markets — and deliberately not
   * the same as omitting this, which means no restriction at all.
   */
  marketKeys?: string[];
  positionGroup?: PositionGroup;
  /**
   * Restrict to these games: the board's game picker, the day strip, or both
   * at once (the page intersects them before they get here).
   */
  gameIds?: number[];
  /** Case-insensitive substring on the player's name. */
  search?: string;

  /**
   * Restrict to the conferences marked for display. Defaults to true: the
   * board is scoped to the displayed conferences (every current FBS
   * conference since migration 0081), while ingest also holds FCS opponents.
   * Pass false to see everything ingested.
   */
  displayedConferencesOnly?: boolean;
  conferenceName?: string;
  /** Only players whose own team carries an AP Top 25 rank that week. */
  rankedOnly?: boolean;
  /**
   * Only calls on this side of the line — the Over / Under switch. See
   * `BoardParams.side` for why a row with no line drops out and why Under
   * leaves out the yes/no markets. Applied by `applySide`, which the card
   * read shares.
   */
  side?: BetSide;

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
  /**
   * Only rows whose headline book is FanDuel or DraftKings (`MAJOR_BOOKS`),
   * the board's default since 2026-10-19 (`BoardParams.allBooks`). The
   * headline is the highest-priority book pricing the row and those two hold
   * priorities 10 and 20, so this is exactly "a row either of them prices".
   */
  majorBooksOnly?: boolean;
  /**
   * Only rows the model has actually called — the population BEST PLAYS lists.
   *
   * The home tile counts calls, so without this the list it links to would
   * carry every projection on the slate and contradict the number that sent the
   * reader there. Rows with no call sort last anyway, so the old link LOOKED
   * right on page one and went wrong at the pager.
   */
  withCallOnly?: boolean;

  /**
   * Hide games that have already kicked off, measured against this instant.
   *
   * PUSHED INTO THE QUERY like every other filter, and for the same reason: a
   * week holds ~6,000 rows against a 1,000-row response cap, so filtering after
   * the fetch would drop played games out of an arbitrary truncation of the
   * week and silently show the wrong players. See `lib/core/kickoff.ts`.
   *
   * Undefined means no cut, which is what the player detail read wants — a
   * player's own page is history and must keep the games this hides.
   */
  kickoffCutoff?: Date;

  sort?: BoardSort;
  limit?: number;
  offset?: number;
};

const COLUMNS =
  "projection_id, pick_id, season, week, sport, market_key, market_name, market_label, " +
  "market_emoji, is_binary, player_id, player_name, position_group, team_id, " +
  "team_school, team_abbreviation, team_color, team_alt_color, opponent_team_id, " +
  "opponent_school, opponent_abbreviation, game_id, start_date, neutral_site, " +
  "is_home, line, side, confidence, model_prob_over, book_prob_over, edge, " +
  "has_book_line, has_call, over_price, under_price, sportsbook_key, " +
  "sportsbook_name, projected_median, projected_p10, projected_p90, prior_weight, " +
  "opponent_rank_vs_position, conference_name, conference_is_displayed, " +
  // Raw first-quarter allowed-per-game (migration 0070). No adjusted or ranked
  // sibling exists — see the column comment for the measurement.
  "opponent_q1_rush_yards_allowed_pg, opponent_q1_rec_yards_allowed_pg, " +
  // What the opponent DOES (migration 0072). Read for both sports though
  // only the NFL has it: a college row comes back null, which is what "no
  // charting source" means, and branching the column list on sport would
  // give one row type two shapes.
  "opponent_blitz_rate, opponent_blitz_rank, opponent_heavy_box_rate, " +
  "opponent_charting_games, " +
  "display_confidence, effective_sample, venue_name, venue_city, venue_state, " +
  "team_spread, game_total, game_line_providers, team_poll_rank, opponent_poll_rank, " +
  // Computed from now() at read time (migration 0052). Selected as well as
  // sorted on, because the card has to SAY a game has started — a row that is
  // merely lower down the board looks like a weak edge, not a settled one.
  "has_kicked_off";

/**
 * The player-detail column list: everything above, plus the ladder.
 *
 * SEPARATE FROM `COLUMNS` ON PURPOSE. A ladder is 5 to 7 jsonb objects per row
 * and the board renders none of them, so putting it in the shared list would put
 * thousands of rung objects on the wire for every page of a board that shows a
 * card per row and a ladder on none. One player's rows are at most nine.
 */
const PLAYER_COLUMNS = `${COLUMNS}, ladder`;

export type BoardPage = {
  rows: BoardRow[];
  /** Total matching rows, before limit/offset — for pagination and counts. */
  total: number;
};

/**
 * The week's board index, read once per sport and week per minute.
 *
 * ONE STATEMENT, WHATEVER THE FILTERS. The cache key is only (sport, season,
 * week), so every reader and every filter combination shares the entry.
 * Cached RAW (arrays) and decoded per render, because the cached value must be
 * plain JSON and stay under the 2 MB data-cache entry limit — a full college
 * week is ~1 MB as arrays and would be well over as objects.
 */
async function readBoardIndex(
  sport: Sport,
  season: number,
  week: number,
): Promise<unknown[][]> {
  const supabase = createServerSupabaseClient();
  const { data, error } = await supabase.rpc("board_index", {
    p_sport: sport,
    p_season: season,
    p_week: week,
  });
  if (error) {
    throw new Error(`Board index failed: ${error.message}`);
  }
  return (data ?? []) as unknown[][];
}

const getRawBoardIndex = cachedLiveRead("board-index", readBoardIndex);

/** Decoded once per render, however many board reads the page makes. */
const loadBoardIndex = cache(
  async (sport: Sport, season: number, week: number): Promise<IndexRow[]> =>
    decodeIndex(await getRawBoardIndex(sport, season, week)),
);

/** The decoded week, for other pages that count off the board (the home tiles). */
export function getBoardIndex(sport: Sport, season: number, week: number): Promise<IndexRow[]> {
  return loadBoardIndex(sport, season, week);
}

function indexFor(filters: Pick<BoardFilters, "sport" | "season" | "week">) {
  return loadBoardIndex(filters.sport ?? DEFAULT_SPORT, filters.season, filters.week);
}

/** The board's matching rows, in board order. */
async function matchingRows(filters: BoardFilters): Promise<IndexRow[]> {
  const index = await indexFor(filters);
  const matched = index.filter((row) => matchesBoardFilters(row, filters as IndexFilters));
  return sortIndexRows(matched, filters.sort, new Date());
}

/**
 * The wide columns for exactly these projections. One statement shape for any
 * list (`projection_id = ANY($1)`), so a new page never prepares a new one.
 */
async function readRowsByIds(ids: number[], orderByMarket: boolean): Promise<BoardRow[]> {
  if (ids.length === 0) return [];
  const supabase = createServerSupabaseClient();
  let query = supabase.from("v_board_rows").select(COLUMNS).in("projection_id", ids);
  // Cards list a player's markets in market order, decided by the database's
  // collation as before; the table restores the index's order itself.
  if (orderByMarket) query = query.order("market_key");
  return unwrap<DbRow[]>(await query, "v_board_rows (by id)").map(toBoardRow);
}

const getRowsByIds = cachedLiveRead("board-rows-by-id", readRowsByIds);

/** Rows in the order of `ids`, which came from the index sort. */
async function rowsInOrder(ids: number[]): Promise<BoardRow[]> {
  const order = new Map(ids.map((id, index) => [id, index]));
  return (await getRowsByIds(ids, false)).sort(
    (a, b) => (order.get(a.projectionId) ?? 0) - (order.get(b.projectionId) ?? 0),
  );
}

/** One page of matching rows, with the total before paging. */
export async function getBoardRows(filters: BoardFilters): Promise<BoardPage> {
  const limit = Math.min(filters.limit ?? 200, MAX_ROWS_PER_REQUEST);
  const offset = filters.offset ?? 0;
  const matched = await matchingRows(filters);
  const ids = matched.slice(offset, offset + limit).map((row) => row.projectionId);
  return { rows: await rowsInOrder(ids), total: matched.length };
}

/**
 * One page of board ROWS, for the table layout: which rows from the index, then
 * their wide columns by id. (The single wide, sorted query this once replaced
 * did not finish inside the free tier's `statement_timeout`; the index keeps
 * that two-step shape without either query depending on the filters.)
 */
export async function getBoardRowPage(filters: BoardFilters): Promise<BoardRow[]> {
  const limit = Math.min(filters.limit ?? 50, MAX_ROWS_PER_REQUEST);
  const offset = filters.offset ?? 0;
  const matched = await matchingRows(filters);
  return rowsInOrder(matched.slice(offset, offset + limit).map((row) => row.projectionId));
}

/**
 * How many rows match. Fetched before the page so the page can be clamped;
 * PostgREST no longer sees the range at all, but a page past the end still has
 * to land on the last real page.
 */
export async function getBoardRowCount(filters: BoardFilters): Promise<number> {
  return (await matchingRows(filters)).length;
}

export type CardKey = { playerId: number; gameId: number };

export type CardKeyPage = {
  keys: CardKey[];
  /**
   * True when the week was not fully scanned. The SQL scan was capped at
   * 12,000 rows; the index is the whole week, so this is now always false. Kept
   * so the page's partial-slate banner has nothing new to learn.
   */
  truncated: boolean;
};

/**
 * The player-games matching the filters, in board order.
 *
 * WHY THIS EXISTS RATHER THAN PAGINATING ROWS. The board is one card per
 * player, but a row is one player-MARKET, so slicing rows into pages would cut
 * a player's markets across a page boundary and a card would silently show
 * three of his five markets. This establishes which players make the page and
 * in what order; `getRowsForCards` then fetches every row for exactly those.
 */
export async function getBoardCardKeys(filters: BoardFilters): Promise<CardKeyPage> {
  const seen = new Set<string>();
  const keys: CardKey[] = [];
  for (const row of await matchingRows(filters)) {
    const id = `${row.playerId}-${row.gameId}`;
    if (seen.has(id)) continue;
    seen.add(id);
    keys.push({ playerId: row.playerId, gameId: row.gameId });
  }
  return { keys, truncated: false };
}

/**
 * Every row for a specific set of player-games.
 *
 * The scope, market, position, side and book filters still apply — they decide
 * which sub-cards a card shows — but the filters that pick WHICH players make
 * the page (edge, confidence, rank) deliberately do NOT. A player who earns his
 * place with a 72% receiving-yards call should show his other markets too.
 */
export async function getRowsForCards(
  keys: CardKey[],
  filters: Pick<
    BoardFilters,
    | "sport"
    | "season"
    | "week"
    | "marketKey"
    | "marketKeys"
    | "positionGroup"
    | "side"
    | "majorBooksOnly"
  >,
): Promise<BoardRow[]> {
  if (keys.length === 0) return [];
  const wanted = new Set(keys.map((key) => `${key.playerId}-${key.gameId}`));
  const ids = (await indexFor(filters))
    .filter(
      (row) =>
        wanted.has(`${row.playerId}-${row.gameId}`) &&
        matchesCardFilters(row, filters as IndexFilters),
    )
    .map((row) => row.projectionId)
    // Sorted so the same set of cards is the same cache entry.
    .sort((a, b) => a - b);
  return getRowsByIds(ids, true);
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
  /**
   * Real-book rows whose de-vigged price is exactly 0.500, so `edge` restates
   * confidence instead of disagreeing with a market. A SUBSET of
   * `withBookLine`, and deliberately not of `withDevLine` — the synthetic book
   * produces the same arithmetic and is already reported separately, so
   * counting it here would double-describe the same rows.
   */
  withEvenBookPrice: number;
  overThreshold: number;
};

/**
 * Headline counts for a week, without transferring the rows.
 *
 * WAS SIX PARALLEL `head: true` COUNTS, then one `board_counts` scan (six exact
 * counts meant one render evaluated the view six times; measured 2026-09-09 at
 * 1.8-1.9 s each in parallel against ~100 ms as one query).
 *
 * NOW COUNTED FROM THE BOARD INDEX (2026-10-24), the same cached week the board
 * lists from, by `countIndex` in the core — which reproduces `board_counts`
 * (migration 0059) predicate for predicate. The RPC cost ~170 ms of database
 * time per call; this costs none beyond the index the board reads anyway.
 *
 * It takes a sport, and must: the six counts this began as filtered season,
 * week, conference and cutoff but never sport, so on 2026 week 1 both boards
 * printed college's and the NFL's rows summed. See `lib/core/sport.ts`.
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
  {
    sport = DEFAULT_SPORT,
    displayedConferencesOnly = true,
    kickoffCutoff,
  }: {
    sport?: Sport;
    displayedConferencesOnly?: boolean;
    kickoffCutoff?: Date;
  } = {},
): Promise<BoardCounts> {
  return countIndex(await loadBoardIndex(sport, season, week), {
    edgeThreshold,
    syntheticBookKey: SYNTHETIC_BOOK_KEY,
    displayedConferencesOnly,
    // Counted under the same cut the board lists under: a banner counting
    // settled rows would describe a market the reader cannot see.
    kickoffCutoff,
  });
}

/** One player's rows for a week — every market, for the detail view. */
async function readPlayerBoardRows(
  playerId: number,
  season: number,
  week: number,
): Promise<BoardRow[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_board_rows")
      .select(PLAYER_COLUMNS)
      .eq("player_id", playerId)
      .eq("season", season)
      .eq("week", week)
      .order("market_key"),
    "v_board_rows (player)",
  );
  return rows.map(toBoardRow);
}

export const getPlayerBoardRows = cachedLiveRead("player-board-rows", readPlayerBoardRows);

function toBoardRow(row: Record<string, unknown>): BoardRow {
  return {
    projectionId: row.projection_id as number,
    pickId: (row.pick_id as number | null) ?? null,

    season: row.season as number,
    week: row.week as number,
    sport: row.sport as Sport,

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
    // Defaults to false, matching the view's own coalesce: a row that somehow
    // arrives without the column is treated as upcoming, which is the same
    // shown-not-hidden call `hasKickedOff` makes for a null kickoff.
    hasKickedOff: (row.has_kicked_off as boolean | null) ?? false,
    neutralSite: row.neutral_site as boolean,
    isHome: row.is_home as boolean,

    venueName: (row.venue_name as string | null) ?? null,
    venueCity: (row.venue_city as string | null) ?? null,
    venueState: (row.venue_state as string | null) ?? null,

    teamSpread: (row.team_spread as number | null) ?? null,
    gameTotal: (row.game_total as number | null) ?? null,
    gameLineProviders: (row.game_line_providers as number | null) ?? null,

    teamPollRank: (row.team_poll_rank as number | null) ?? null,
    opponentPollRank: (row.opponent_poll_rank as number | null) ?? null,

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
    // Absent from the board's column list, so `row.ladder` is undefined there
    // rather than null. Parsed rather than cast: the column is jsonb with only an
    // outer array constraint, so element shape is a worker guarantee, not a
    // database one.
    ladder: row.ladder === undefined ? null : parseLadder(row.ladder),
    priorWeight: (row.prior_weight as number | null) ?? null,
    effectiveSample: (row.effective_sample as number | null) ?? null,

    opponentRankVsPosition:
      (row.opponent_rank_vs_position as number | null) ?? null,

    // `numeric` arrives from PostgREST as a STRING, so these are coerced rather
    // than cast — a cast would typecheck and then format "27.800" or do string
    // arithmetic, the same trap `ladderStep` carries a note about.
    opponentQ1RushYardsAllowedPg: num(row.opponent_q1_rush_yards_allowed_pg),
    opponentQ1RecYardsAllowedPg: num(row.opponent_q1_rec_yards_allowed_pg),

    opponentBlitzRate: num(row.opponent_blitz_rate),
    opponentBlitzRank: num(row.opponent_blitz_rank),
    opponentHeavyBoxRate: num(row.opponent_heavy_box_rate),
    opponentChartingGames: num(row.opponent_charting_games),

    conferenceName: (row.conference_name as string | null) ?? null,
    conferenceIsDisplayed:
      (row.conference_is_displayed as boolean | null) ?? null,

    displayConfidence: (row.display_confidence as number | null) ?? null,
  };
}
