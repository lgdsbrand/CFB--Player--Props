/**
 * URL state for the player detail view.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * Same reasoning as the board (`board-params.ts`): the page is server-rendered
 * and the selected market decides which line the whole page grades against, so
 * it belongs in the address rather than in component state. A link to a
 * player's receiving-yards read is then a real link — sendable to the client,
 * quotable in a report, identical after a refresh — and the market tabs need no
 * client JavaScript.
 */

import type { RawParams } from "@/lib/core/board-params";
// Relative, with the extension: these are VALUES, and Node's test runner does
// not understand the `@/*` alias.
import { DEFAULT_SPORT, resolveSport, type Sport } from "./sport.ts";

export type PlayerParams = {
  playerId: number;
  /**
   * Which league the player is in. REQUIRED, so every link into this page has
   * to decide it rather than inherit college by omission.
   *
   * It was absent until 2026-09-11, and that was a reported bug: every link into
   * an NFL player's page called him a college player, so the page read college's
   * weeks and defense ratings, lit NCAAF in the header, and sent "Back to the
   * board" to an empty college board. The page still checks this against the
   * player's own rows and redirects when a link claims the wrong sport — a
   * player id DETERMINES its sport, the URL only claims one.
   */
  sport: Sport;
  season?: number;
  week?: number;
  /** Which market's line the chart, splits and log are graded against. */
  market?: string;
  /**
   * Which game, for the players who have two in one week.
   *
   * CFBD's week 1 spans nine or ten days every season, so a team can play twice
   * inside it — see `player-games.ts`. Everything on the page describes one
   * game, so which one has to be addressable rather than implicit.
   */
  game?: number;
};

export function parsePlayerParams(
  playerId: number,
  raw: RawParams,
): PlayerParams {
  const single = (value: string | string[] | undefined) => {
    const first = Array.isArray(value) ? value[0] : value;
    return first && first.length > 0 ? first : undefined;
  };
  const int = (value: string | string[] | undefined) => {
    const text = single(value);
    if (text === undefined) return undefined;
    const parsed = Number.parseInt(text, 10);
    return Number.isFinite(parsed) ? parsed : undefined;
  };

  return {
    playerId,
    sport: resolveSport(raw.sport),
    season: int(raw.season),
    week: int(raw.week),
    market: single(raw.market),
    game: int(raw.game),
  };
}

export function playerHref(
  current: PlayerParams,
  changes: Partial<PlayerParams> = {},
): string {
  const next = { ...current, ...changes };
  const search = new URLSearchParams();
  // First and omitted for college, the way `boardHref` writes it.
  if (next.sport !== DEFAULT_SPORT) search.set("sport", next.sport);
  if (next.season !== undefined) search.set("season", String(next.season));
  if (next.week !== undefined) search.set("week", String(next.week));
  if (next.market) search.set("market", next.market);
  if (next.game !== undefined) search.set("game", String(next.game));

  const query = search.toString();
  return `/player/${next.playerId}${query ? `?${query}` : ""}`;
}
