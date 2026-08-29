/**
 * The board's filter state, carried in the URL.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * WHY THE URL AND NOT COMPONENT STATE. The board is server-rendered per week,
 * and every filter is a database predicate — the row cap makes client-side
 * filtering incorrect, not merely slow (see `lib/data/board.ts`). Putting the
 * state in the URL means a filtered board is a real address: shareable with the
 * client, linkable from a report, and identical after a hard refresh. It also
 * means the controls need no client-side JavaScript at all — pill groups are
 * links and the search box is a GET form.
 */

import type { BoardSort } from "@/lib/data/board";
// Relative, not aliased: `POSITION_GROUPS` is a VALUE, so it survives type
// stripping and the test runner has to resolve it for real. Type-only imports
// above can keep the alias because they are erased before Node sees them.
import { POSITION_GROUPS, type PositionGroup } from "./types.ts";

/**
 * How the board lays its results out.
 *
 * `cards` is one card per PLAYER holding a sub-card per market — the client's
 * pitcher card (CLAUDE.md §7). `table` is one row per PROP, columns aligned so a
 * reader can scan a single number down the page.
 *
 * These are two answers to different questions, not a preference. A card
 * compares a player's markets to each other; a table compares one market across
 * players. That is why both exist rather than one replacing the other.
 */
export type BoardView = "cards" | "table";

export type BoardParams = {
  season?: number;
  week?: number;
  position?: PositionGroup;
  market?: string;
  game?: number;
  /**
   * One day of the slate week, `YYYY-MM-DD` in `SLATE_TIME_ZONE`.
   *
   * A week is not a day here: 2026 week 1 ran ten calendar days over six game
   * days. Narrower than the week, wider than `game`, and resolved against the
   * days that actually exist — an unknown value means "all days" rather than an
   * empty board, so a stale link degrades instead of stranding the reader.
   */
  day?: string;
  conference?: string;
  /**
   * Only players whose OWN team is in the AP Top 25 that week.
   *
   * Scoped to the player's team, not to the game, matching the conference
   * filter — someone narrowing to ranked teams wants ranked players to look
   * at, and "his opponent is ranked" is a different question the board answers
   * with the opponent-rank control instead.
   */
  rankedOnly: boolean;
  search?: string;
  sort: BoardSort;
  edgesOnly: boolean;
  minConfidence?: number;
  minOpponentRank?: number;
  hitRateWindow: number;
  /**
   * Undefined means "not chosen", which is NOT the same as `cards`. The default
   * depends on the market filter — see `resolveBoardView` — so storing a
   * concrete value here would freeze whichever layout the reader happened to
   * land on first and stop the default ever applying again.
   */
  view?: BoardView;
  page: number;
};

/** Raw `searchParams` as Next hands them over. */
export type RawParams = Record<string, string | string[] | undefined>;

/**
 * Where the board lives.
 *
 * It moved off `/` when the home page took that address. Kept as a constant
 * rather than typed into `boardHref` and a dozen components, because the two
 * places that build board URLs by hand — the week strip's `basePath` and the
 * player page's back link — are exactly the ones that would be missed.
 */
export const BOARD_PATH = "/props";

/**
 * Every key the board reads out of the URL.
 *
 * Used by `boardParamsPresent` to recognise a board link that arrives at `/`.
 * Listed once so the two cannot drift: a key added to the parser but not here
 * would make a shared link silently land on the home page instead.
 */
const BOARD_PARAM_KEYS = [
  "season", "week", "position", "market", "game", "day", "conference", "q",
  "sort", "edges", "top25", "conf", "rank", "window", "view", "page",
] as const;

/**
 * Does this look like a board URL?
 *
 * The board used to live at `/`, so links shared before the move — including
 * any the client saved — carry board filters on the home page's address. This
 * lets `/` forward them rather than dropping a reader on a landing page and
 * silently discarding the week and filters they were pointed at.
 *
 * A BARE `/` IS NOT A BOARD LINK. That is the home page, and it must not
 * bounce.
 */
export function boardParamsPresent(raw: RawParams): boolean {
  return BOARD_PARAM_KEYS.some((key) => first(raw[key]) !== undefined);
}

export const DEFAULT_HIT_RATE_WINDOW = 5;

/** Cards per page. Deliberately modest — see `getGameLogsByPlayer`. */
export const CARDS_PER_PAGE = 25;

/**
 * Rows per page in table view.
 *
 * Sized against the same constraint the card page is: every row on the page
 * needs its player's game log to grade a hit rate, and `getGameLogsByPlayer`
 * batches 40 players per request. A card page is 25 players; 50 rows is at
 * worst 50 distinct players and in practice fewer, because a player's markets
 * cluster in the sort. Two batches at the very worst, usually one.
 *
 * Also chosen so a table page is a comparable amount of PAGE to a card page —
 * 50 rows at ~52px is about 2,600px against the card grid's ~9,900px, which is
 * the whole point of the layout.
 */
export const ROWS_PER_PAGE = 50;

/**
 * Which layout to render, given what the reader asked for.
 *
 * AN EXPLICIT CHOICE ALWAYS WINS. Absent one, the default follows the MARKET
 * filter, which is the client's own framing of the problem: with All markets
 * selected a card carries up to six sub-cards and runs 963px tall, so the board
 * is ~185px of page per prop. Narrowed to a single market a card holds exactly
 * one sub-card and the card layout is the denser and more legible of the two.
 *
 * So the default is table for All and cards for a single market, and the toggle
 * overrides either — carried in the URL like every other filter, so a layout is
 * part of a shared link rather than a local preference.
 */
export function resolveBoardView(params: BoardParams): BoardView {
  if (params.view) return params.view;
  return params.market ? "cards" : "table";
}

function first(raw: string | string[] | undefined): string | undefined {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return value && value.length > 0 ? value : undefined;
}

function int(raw: string | string[] | undefined): number | undefined {
  const value = first(raw);
  if (value === undefined) return undefined;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function float(raw: string | string[] | undefined): number | undefined {
  const value = first(raw);
  if (value === undefined) return undefined;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * Parse the URL into filter state.
 *
 * Anything unrecognised is dropped rather than passed through. These values
 * reach a database predicate, and a board that renders empty because someone
 * hand-edited `position=LB` is a worse answer than one that ignores it.
 */
export function parseBoardParams(
  raw: RawParams,
  { edgesOnlyDefault = false }: { edgesOnlyDefault?: boolean } = {},
): BoardParams {
  const position = first(raw.position) as PositionGroup | undefined;
  const sort = first(raw.sort);
  const edgesOnly = first(raw.edges);
  const view = first(raw.view);

  return {
    season: int(raw.season),
    week: int(raw.week),
    position:
      position && POSITION_GROUPS.includes(position) ? position : undefined,
    market: first(raw.market),
    game: int(raw.game),
    day: first(raw.day),
    conference: first(raw.conference),
    rankedOnly: first(raw.top25) === "1",
    search: first(raw.q),
    sort:
      sort === "confidence" || sort === "opponent_rank" || sort === "edge"
        ? sort
        : "edge",
    edgesOnly: edgesOnly === undefined ? edgesOnlyDefault : edgesOnly === "1",
    minConfidence: float(raw.conf),
    minOpponentRank: int(raw.rank),
    hitRateWindow: int(raw.window) ?? DEFAULT_HIT_RATE_WINDOW,
    view: view === "cards" || view === "table" ? view : undefined,
    page: Math.max(int(raw.page) ?? 1, 1),
  };
}

/**
 * Build a URL with some parameters changed and the rest preserved.
 *
 * Changing a filter resets to page 1 unless the caller is explicitly paging:
 * landing on page 4 of a three-page result is a dead end, and the user did not
 * ask to keep their offset when they changed the question.
 *
 * `basePath` EXISTS BECAUSE THE FILTERS OUTLIVED THE BOARD. The day strip and
 * the conference pills now appear on Analyze Games too, and both mean exactly
 * what they mean here — same parser, same keys, same values. What must not be
 * shared is the destination: a reader changing the day on `/games` is asking
 * for another day of games, not to be moved to the props board. Same reasoning
 * as `WeekStrip`'s own `basePath`, and it defaults to the board so every
 * existing caller is unchanged.
 *
 * Params the other page has no control for simply survive the round trip
 * untouched, which is the behaviour we want: they cost nothing, and dropping
 * them would silently discard state a shared link was carrying.
 */
export function boardHref(
  current: BoardParams,
  changes: Partial<BoardParams>,
  basePath: string = BOARD_PATH,
): string {
  const next = { ...current, ...changes };
  if (!("page" in changes)) next.page = 1;

  const search = new URLSearchParams();
  const set = (key: string, value: string | number | undefined) => {
    if (value !== undefined && value !== "") search.set(key, String(value));
  };

  set("season", next.season);
  set("week", next.week);
  set("position", next.position);
  set("market", next.market);
  set("game", next.game);
  set("day", next.day);
  set("conference", next.conference);
  set("q", next.search);
  if (next.sort !== "edge") set("sort", next.sort);
  if (next.edgesOnly) set("edges", "1");
  if (next.rankedOnly) set("top25", "1");
  set("conf", next.minConfidence);
  set("rank", next.minOpponentRank);
  if (next.hitRateWindow !== DEFAULT_HIT_RATE_WINDOW) {
    set("window", next.hitRateWindow);
  }
  // Written even when it matches what the default would pick. Omitting it would
  // turn an explicit choice back into "not chosen", so the next market change
  // would silently flip the layout out from under a reader who had just set it.
  set("view", next.view);
  if (next.page > 1) set("page", next.page);

  const query = search.toString();
  return query ? `${basePath}?${query}` : basePath;
}

/**
 * The board with every filter cleared.
 *
 * ONE definition, used by both the Reset button and the empty board's "clear
 * the filters" link. They used to clear different sets — the empty board left
 * the sort and hit-rate window alone while Reset took them back to default —
 * so two controls that read as the same promise did two different things.
 *
 * Season and week survive: reset means "show me all of this slate", not "send
 * me to another week". Everything else is omitted rather than forced, so the
 * board lands on whatever the configured defaults are — `edgesOnly` included,
 * which is why it is left out instead of set to false.
 */
export function resetBoardHref(current: BoardParams): string {
  return boardHref(
    {
      season: current.season,
      week: current.week,
      sort: "edge",
      edgesOnly: false,
      rankedOnly: false,
      hitRateWindow: DEFAULT_HIT_RATE_WINDOW,
      page: 1,
    },
    {},
  );
}

/**
 * The same state as hidden form fields, for the GET form the search box uses.
 *
 * A form submits only its own inputs, so without these every other filter would
 * silently reset the moment someone typed a name. Still needed after the move
 * to instant filters: the form is the pre-hydration fallback, and until the
 * script lands a submit is a real GET.
 */
export function hiddenFields(
  current: BoardParams,
  omit: string[] = [],
): { name: string; value: string }[] {
  const url = boardHref(current, {});
  const search = new URLSearchParams(url.split("?")[1] ?? "");
  return [...search.entries()]
    .filter(([name]) => !omit.includes(name))
    .map(([name, value]) => ({ name, value }));
}
