/**
 * The bet builder's candidate pool for one slate week: every pick the model
 * takes that a book has priced, with its hit rate and each book's price.
 *
 * READ ONCE, SEARCHED IN THE BROWSER. The builder re-runs on every setting the
 * reader touches, so the pool goes to the page whole (a route handler, cached
 * at the edge for two minutes) and `lib/core/builder.ts` does the rest. A full
 * week is ~2,000 props, measured on 2026 weeks 5 and 6; each read below is
 * under a second on production.
 *
 * Three sources:
 *   v_cheat_sheet        the board row plus its graded hit rate, at one window.
 *                        Every priced prop is graded there; the sheet page
 *                        only filters to 80%+.
 *   v_latest_prop_lines  each book's newest price. Not `prop_offers`, which
 *                        holds only games captured since bet links were asked
 *                        for (2026-10-07); the slip adds links when it opens.
 *   game_projections +   spreads and totals, on the side the games table
 *   game_offers          shows the edge on, at the model's own line.
 */

import type { PostgrestError } from "@supabase/supabase-js";

import type { BuilderCandidate } from "@/lib/core/builder";
import { marketEdge, type PricedMarket } from "@/lib/core/game-lines";
import { SLIP_HIDDEN_BOOKS, type GameLeg, type PropLeg } from "@/lib/core/slip";
import type { Sport } from "@/lib/core/sport";
import type { GameSummary } from "@/lib/core/types";
import { getGameProjections } from "@/lib/data/game-odds";
import { getSlateGames } from "@/lib/data/games";
import {
  type DbRow,
  MAX_ROWS_PER_REQUEST,
  num,
  unwrap,
  upcomingOnly,
} from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export interface BuilderPool {
  sport: Sport;
  season: number;
  week: number;
  windowSize: number;
  candidates: BuilderCandidate[];
  /** Book key to display name, for every book with a price in the pool. */
  bookNames: Record<string, string>;
  /** Markets present, for the market filter. */
  markets: { key: string; label: string }[];
  games: Pick<GameSummary, "gameId" | "awayAbbreviation" | "homeAbbreviation" | "startDate">[];
}

/** Ten pages is 10,000 rows, five times the fullest week measured. */
const MAX_PAGES = 10;

const SHEET_COLUMNS =
  "game_id, player_id, player_name, team_school, team_abbreviation, " +
  "opponent_school, opponent_abbreviation, is_home, start_date, market_key, " +
  "market_label, is_binary, line, model_side, display_confidence, edge, " +
  "has_call, decided, hits, hit_side, projection_id";

const GAME_MARKET_LABEL: Record<"spreads" | "totals", string> = {
  spreads: "Spread",
  totals: "Total",
};

async function readAll(
  build: (from: number, to: number) => PromiseLike<{ data: unknown; error: PostgrestError | null }>,
  label: string,
): Promise<DbRow[]> {
  const out: DbRow[] = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const from = page * MAX_ROWS_PER_REQUEST;
    const rows = unwrap<DbRow[]>(await build(from, from + MAX_ROWS_PER_REQUEST - 1), label);
    out.push(...rows);
    if (rows.length < MAX_ROWS_PER_REQUEST) break;
  }
  return out;
}

const priceKey = (gameId: number, playerId: number, market: string, line: number) =>
  `${gameId}:${playerId}:${market}:${line.toFixed(2)}`;

export async function getBuilderPool({
  sport,
  season,
  week,
  windowSize,
  kickoffCutoff,
}: {
  sport: Sport;
  season: number;
  week: number;
  windowSize: number;
  kickoffCutoff: Date;
}): Promise<BuilderPool> {
  const supabase = createServerSupabaseClient();

  const [sheet, slateGames] = await Promise.all([
    readAll(
      (from, to) =>
        supabase
          .from("v_cheat_sheet")
          .select(SHEET_COLUMNS)
          .eq("sport", sport)
          .eq("season", season)
          .eq("week", week)
          .eq("window_size", windowSize)
          .eq("conference_is_displayed", true)
          .eq("has_book_line", true)
          .or(upcomingOnly(kickoffCutoff))
          .order("projection_id", { ascending: true })
          .range(from, to),
      "v_cheat_sheet",
    ),
    getSlateGames(season, week, sport),
  ]);

  const upcoming = slateGames.filter(
    (game) => !game.startDate || new Date(game.startDate) >= kickoffCutoff,
  );
  const propGameIds = [...new Set(sheet.map((row) => Number(row.game_id)))];

  const [lines, gameCandidates] = await Promise.all([
    propGameIds.length === 0
      ? Promise.resolve([] as DbRow[])
      : readAll(
          (from, to) =>
            supabase
              .from("v_latest_prop_lines")
              .select("game_id, player_id, market_key, sportsbook_key, sportsbook_name, line, over_price, under_price")
              .in("game_id", propGameIds)
              .order("line_id", { ascending: true })
              .range(from, to),
          "v_latest_prop_lines",
        ),
    gameLineCandidates(upcoming),
  ]);

  const bookNames: Record<string, string> = { ...gameCandidates.bookNames };
  const prices = new Map<string, { over: Record<string, number>; under: Record<string, number> }>();
  for (const row of lines) {
    const book = row.sportsbook_key as string;
    if (SLIP_HIDDEN_BOOKS.has(book)) continue;
    const line = num(row.line);
    if (line === null) continue;
    bookNames[book] = row.sportsbook_name as string;
    const key = priceKey(Number(row.game_id), Number(row.player_id), row.market_key as string, line);
    const entry = prices.get(key) ?? { over: {}, under: {} };
    const over = num(row.over_price);
    const under = num(row.under_price);
    if (over !== null) entry.over[book] = over;
    if (under !== null) entry.under[book] = under;
    prices.set(key, entry);
  }

  const candidates: BuilderCandidate[] = [];
  const markets = new Map<string, string>();
  for (const row of sheet) {
    const binary = row.is_binary === true;
    // Anytime TD is "Yes" or nothing: books price it one way.
    const side = binary ? "over" : (row.model_side as "over" | "under" | null);
    if (!side || (!binary && row.has_call !== true)) continue;
    const prob = num(row.display_confidence);
    const line = num(row.line);
    if (prob === null || line === null) continue;
    const gameId = Number(row.game_id);
    const playerId = Number(row.player_id);
    const marketKey = row.market_key as string;
    const bookPrices = prices.get(priceKey(gameId, playerId, marketKey, line))?.[side];
    if (!bookPrices || Object.keys(bookPrices).length === 0) continue;

    const decided = num(row.decided) ?? 0;
    const hitsOnHitSide = num(row.hits) ?? 0;
    const hits = row.hit_side === side ? hitsOnHitSide : decided - hitsOnHitSide;
    const team = (row.team_abbreviation as string | null) ?? (row.team_school as string);
    const opponent =
      (row.opponent_abbreviation as string | null) ?? (row.opponent_school as string);
    const market = (row.market_label as string | null) ?? marketKey;
    markets.set(marketKey, market);

    const leg: PropLeg = {
      kind: "prop",
      gameId,
      playerId,
      marketKey,
      side,
      line,
      binary,
      player: row.player_name as string,
      market,
      matchup: row.is_home ? `${opponent} @ ${team}` : `${team} @ ${opponent}`,
      startDate: (row.start_date as string | null) ?? null,
    };
    candidates.push({
      leg,
      playerId,
      marketKey,
      prob,
      edge: binary ? null : num(row.edge),
      hits: decided > 0 ? hits : null,
      decided: decided > 0 ? decided : null,
      prices: bookPrices,
    });
  }

  for (const candidate of gameCandidates.candidates) {
    candidates.push(candidate);
    markets.set(candidate.marketKey, GAME_MARKET_LABEL[candidate.marketKey as "spreads" | "totals"]);
  }

  return {
    sport,
    season,
    week,
    windowSize,
    candidates,
    bookNames,
    markets: [...markets.entries()]
      .map(([key, label]) => ({ key, label }))
      .sort((a, b) => a.label.localeCompare(b.label)),
    games: upcoming.map((game) => ({
      gameId: game.gameId,
      awayAbbreviation: game.awayAbbreviation ?? game.awaySchool,
      homeAbbreviation: game.homeAbbreviation ?? game.homeSchool,
      startDate: game.startDate,
    })),
  };
}

/**
 * Spreads and totals on the side the games table shows the edge on, at the
 * model's own line. Only where the model publishes (not a team new to FBS),
 * and only books posting that exact line.
 */
async function gameLineCandidates(
  games: GameSummary[],
): Promise<{ candidates: BuilderCandidate[]; bookNames: Record<string, string> }> {
  const empty = { candidates: [], bookNames: {} };
  if (games.length === 0) return empty;
  const projections = await getGameProjections(games.map((game) => game.gameId));
  const priced = games.filter((game) => {
    const projection = projections.get(game.gameId);
    return projection && !projection.missingPriorSeason && (projection.spread || projection.total);
  });
  if (priced.length === 0) return empty;

  const offers = unwrap<DbRow[]>(
    await createServerSupabaseClient()
      .from("game_offers")
      .select("game_id, market, side, line, price, book:sportsbooks!sportsbook_id(key, display_name, market_role)")
      .in("game_id", priced.map((game) => game.gameId))
      .eq("period", "full")
      .in("market", ["spreads", "totals"]),
    "game_offers",
  );

  const bookNames: Record<string, string> = {};
  const candidates: BuilderCandidate[] = [];
  for (const game of priced) {
    const projection = projections.get(game.gameId)!;
    const home = game.homeAbbreviation ?? game.homeSchool;
    const away = game.awayAbbreviation ?? game.awaySchool;
    for (const market of ["spreads", "totals"] as const) {
      const pm: PricedMarket | null = market === "spreads" ? projection.spread : projection.total;
      if (!pm) continue;
      const pick = marketEdge(pm);
      const side =
        market === "spreads"
          ? pick.side === "first" ? "home" : "away"
          : pick.side === "first" ? "over" : "under";
      const prices: Record<string, number> = {};
      for (const row of offers) {
        const book = row.book as { key: string; display_name: string; market_role: string };
        if (
          Number(row.game_id) !== game.gameId || row.market !== market || row.side !== side ||
          SLIP_HIDDEN_BOOKS.has(book.key) || book.market_role === "exchange"
        ) {
          continue;
        }
        const line = num(row.line);
        if (line === null || Math.abs(line - pm.line) > 1e-6) continue;
        prices[book.key] = Number(row.price);
        bookNames[book.key] = book.display_name;
      }
      if (Object.keys(prices).length === 0) continue;
      const leg: GameLeg = {
        kind: "game",
        gameId: game.gameId,
        period: "full",
        market,
        side,
        line: pm.line,
        home,
        away,
        startDate: game.startDate,
      };
      candidates.push({
        leg,
        playerId: null,
        marketKey: market,
        prob: pick.modelProb,
        edge: pick.edge,
        hits: null,
        decided: null,
        prices,
      });
    }
  }
  return { candidates, bookNames };
}
