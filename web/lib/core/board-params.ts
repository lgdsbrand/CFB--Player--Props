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
import type { PositionGroup } from "@/lib/core/types";
import { POSITION_GROUPS } from "@/lib/core/types";

export type BoardParams = {
  season?: number;
  week?: number;
  position?: PositionGroup;
  market?: string;
  game?: number;
  conference?: string;
  search?: string;
  sort: BoardSort;
  edgesOnly: boolean;
  minConfidence?: number;
  maxOpponentRank?: number;
  hitRateWindow: number;
  page: number;
};

/** Raw `searchParams` as Next hands them over. */
export type RawParams = Record<string, string | string[] | undefined>;

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
    search: first(raw.q),
    sort:
      sort === "confidence" || sort === "opponent_rank" || sort === "edge"
        ? sort
        : "edge",
    edgesOnly: edgesOnly === undefined ? edgesOnlyDefault : edgesOnly === "1",
    minConfidence: float(raw.conf),
    maxOpponentRank: int(raw.rank),
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
  set("conf", next.minConfidence);
  set("rank", next.maxOpponentRank);
  if (next.hitRateWindow !== DEFAULT_HIT_RATE_WINDOW) {
    set("window", next.hitRateWindow);
  }
  if (next.page > 1) set("page", next.page);

  const query = search.toString();
  return query ? `/?${query}` : "/";
}

/**
 * The same state as hidden form fields, for the GET form the search box uses.
 *
 * A form submits only its own inputs, so without these every other filter would
 * silently reset the moment someone typed a name.
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
