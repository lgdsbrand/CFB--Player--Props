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

export type BoardParams = {
  season?: number;
  week?: number;
  position?: PositionGroup;
  market?: string;
  game?: number;
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
  "season", "week", "position", "market", "game", "conference", "q", "sort",
  "edges", "top25", "conf", "rank", "window", "page",
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

  return {
    season: int(raw.season),
    week: int(raw.week),
    position:
      position && POSITION_GROUPS.includes(position) ? position : undefined,
    market: first(raw.market),
    game: int(raw.game),
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
    page: Math.max(int(raw.page) ?? 1, 1),
  };
}

/**
 * Build a URL with some parameters changed and the rest preserved.
 *
 * Changing a filter resets to page 1 unless the caller is explicitly paging:
 * landing on page 4 of a three-page result is a dead end, and the user did not
 * ask to keep their offset when they changed the question.
 */
export function boardHref(
  current: BoardParams,
  changes: Partial<BoardParams>,
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
  if (next.page > 1) set("page", next.page);

  const query = search.toString();
  return query ? `${BOARD_PATH}?${query}` : BOARD_PATH;
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
