/**
 * The slip checker's catalogue for one slate week: every bet a book has
 * priced, both sides, at each book's own line, with the model's probability
 * where it has one. See `lib/core/slip-check.ts` for which probability.
 *
 * Read once per week and searched in the browser, like the bet builder's
 * pool (`/builder/pool`), and cached at the edge for two minutes.
 *
 * Sources:
 *   v_latest_prop_lines  each book's newest prop price, both sides.
 *   projections          the player, his team, and the ladder of calibrated
 *                        probabilities at other lines.
 *   picks                the model's calibrated probability at its own line.
 *                        NOT `v_board_rows`: a whole week of it with ladders
 *                        ran past the anonymous 3 s timeout (2026-10-12);
 *                        these two tables are indexed and read in pages.
 *   game_offers          each book's current moneyline, spread and total, for
 *                        the full game, first half and first quarter.
 *   game_projections     the game model's numbers.
 * Books a US reader cannot use and exchanges (no parlays) are left out.
 */

import type { PostgrestError } from "@supabase/supabase-js";

import { parseLadder } from "@/lib/core/ladder-view";
import { spreadSideLabel, totalSideLabel, type GameProjection, type ModelPeriod, type OddsMarket } from "@/lib/core/game-lines";
import { formatLine } from "@/lib/core/format";
import {
  gameProbAtLine,
  propProbAtLine,
  type CheckCatalogue,
  type CheckEntry,
  type CheckModel,
  type CheckSide,
} from "@/lib/core/slip-check";
import { SLIP_HIDDEN_BOOKS, type GameLeg, type PropLeg } from "@/lib/core/slip";
import type { Sport } from "@/lib/core/sport";
import type { GameSummary } from "@/lib/core/types";
import { getGameProjections } from "@/lib/data/game-odds";
import { getSlateGames } from "@/lib/data/games";
import { type DbRow, MAX_ROWS_PER_REQUEST, num, unwrap } from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/** Ten pages is 10,000 rows; a full week's prop lines measured ~6,700. */
const MAX_PAGES = 10;

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

const PROJECTION_COLUMNS = "id, game_id, player_id, team_id, market_key, ladder, player:players!player_id(name)";

const PERIOD_PREFIX: Record<ModelPeriod, string> = { full: "", h1: "1H ", q1: "1Q " };
const MARKET_LABEL: Record<OddsMarket, string> = { h2h: "Moneyline", spreads: "Spread", totals: "Total" };

export async function getCheckCatalogue({
  sport,
  season,
  week,
  now,
}: {
  sport: Sport;
  season: number;
  week: number;
  now: Date;
}): Promise<CheckCatalogue> {
  const supabase = createServerSupabaseClient();
  const games = (await getSlateGames(season, week, sport)).filter(
    (game) => !game.startDate || new Date(game.startDate) > now,
  );
  const gameIds = games.map((g) => g.gameId);
  if (gameIds.length === 0) return { sport, season, week, books: [], entries: [] };

  const [players, picks, markets, lines, offers, projections] = await Promise.all([
    readAll(
      (from, to) =>
        supabase
          .from("projections")
          .select(PROJECTION_COLUMNS)
          .eq("season", season)
          .eq("week", week)
          .in("game_id", gameIds)
          // Along projections_slate_weeks_idx (season, week, game, player),
          // unique with the market for one week's run. Ordering by id
          // instead walked the whole table and timed out.
          .order("game_id", { ascending: true })
          .order("player_id", { ascending: true })
          .order("market_key", { ascending: true })
          .range(from, to),
      "projections",
    ),
    readAll(
      (from, to) =>
        supabase
          .from("picks")
          .select("projection_id, line, model_prob_over")
          .eq("season", season)
          .eq("week", week)
          .in("game_id", gameIds)
          .order("id", { ascending: true })
          .range(from, to),
      "picks",
    ),
    supabase
      .from("markets")
      .select("key, display_name, short_label, is_binary")
      .then((result) => unwrap<DbRow[]>(result, "markets")),
    readAll(
      (from, to) =>
        supabase
          .from("v_latest_prop_lines")
          .select("game_id, player_id, market_key, sportsbook_key, sportsbook_name, line, over_price, under_price")
          .in("game_id", gameIds)
          .order("line_id", { ascending: true })
          .range(from, to),
      "v_latest_prop_lines",
    ),
    readAll(
      (from, to) =>
        supabase
          .from("game_offers")
          .select("game_id, period, market, side, line, price, book:sportsbooks!sportsbook_id(key, display_name, market_role)")
          .in("game_id", gameIds)
          // The primary key, so pages neither skip nor repeat a row.
          .order("game_id", { ascending: true })
          .order("period", { ascending: true })
          .order("market", { ascending: true })
          .order("sportsbook_id", { ascending: true })
          .order("side", { ascending: true })
          .range(from, to),
      "game_offers",
    ),
    getGameProjections(gameIds),
  ]);

  const books = new Map<string, string>();
  const entries = [
    ...propEntries({ games, players, picks, markets, lines }, books),
    ...gameEntries(games, offers, projections, books),
  ].sort((a, b) => (a.startDate ?? "").localeCompare(b.startDate ?? "") || a.id.localeCompare(b.id));

  return {
    sport,
    season,
    week,
    books: [...books.entries()].map(([key, name]) => ({ key, name })),
    entries,
  };
}

function propEntries(
  {
    games,
    players,
    picks,
    markets,
    lines,
  }: { games: GameSummary[]; players: DbRow[]; picks: DbRow[]; markets: DbRow[]; lines: DbRow[] },
  books: Map<string, string>,
): CheckEntry[] {
  const gameById = new Map(games.map((g) => [g.gameId, g]));
  const marketByKey = new Map(markets.map((m) => [m.key as string, m]));
  const pickByProjection = new Map(picks.map((p) => [Number(p.projection_id), p]));
  const projectionByBet = new Map<string, DbRow>();
  for (const row of players) projectionByBet.set(`${row.game_id}:${row.player_id}:${row.market_key}`, row);

  // Group each book's price by bet and line.
  const byLine = new Map<string, { projection: DbRow; line: number; over: Record<string, number>; under: Record<string, number> }>();
  for (const row of lines) {
    const book = row.sportsbook_key as string;
    if (SLIP_HIDDEN_BOOKS.has(book)) continue;
    const line = num(row.line);
    const projection = projectionByBet.get(`${row.game_id}:${row.player_id}:${row.market_key}`);
    if (line === null || !projection) continue;
    const key = `${row.game_id}:${row.player_id}:${row.market_key}:${line.toFixed(2)}`;
    const group = byLine.get(key) ?? { projection, line, over: {}, under: {} };
    const over = num(row.over_price);
    const under = num(row.under_price);
    if (over !== null) group.over[book] = over;
    if (under !== null) group.under[book] = under;
    if (over !== null || under !== null) books.set(book, row.sportsbook_name as string);
    byLine.set(key, group);
  }

  const out: CheckEntry[] = [];
  for (const [id, { projection, line, over, under }] of byLine) {
    const gameId = Number(projection.game_id);
    const game = gameById.get(gameId);
    const marketKey = projection.market_key as string;
    const marketRow = marketByKey.get(marketKey);
    if (!game || !marketRow) continue;
    const binary = marketRow.is_binary === true;
    const playerId = Number(projection.player_id);
    const market = (marketRow.short_label as string | null) ?? (marketRow.display_name as string);
    const isHome = Number(projection.team_id) === game.homeTeamId;
    const team = isHome ? (game.homeAbbreviation ?? game.homeSchool) : (game.awayAbbreviation ?? game.awaySchool);
    const opponent = isHome ? (game.awayAbbreviation ?? game.awaySchool) : (game.homeAbbreviation ?? game.homeSchool);
    const matchup = `${game.awayAbbreviation ?? game.awaySchool} @ ${game.homeAbbreviation ?? game.homeSchool}`;
    const startDate = game.startDate;
    const player = (projection.player as { name: string } | null)?.name ?? "Unknown player";
    const leg = (side: "over" | "under"): PropLeg => ({
      kind: "prop", gameId, playerId, marketKey, side, line, binary, player, market, matchup, startDate,
    });

    // Over first, always: the model's probability is stated for it. A yes/no
    // market is priced one way, so "No" is not offered.
    const sides: CheckSide[] = [
      { leg: leg("over"), label: binary ? "Yes" : `Over ${formatLine(line)}`, prices: over },
    ];
    if (!binary) sides.push({ leg: leg("under"), label: `Under ${formatLine(line)}`, prices: under });
    if (sides.every((side) => Object.keys(side.prices).length === 0)) continue;

    const pick = pickByProjection.get(Number(projection.id));
    const pickLine = pick ? num(pick.line) : null;
    const pickProb = pick ? num(pick.model_prob_over) : null;
    const point = pickLine !== null && pickProb !== null ? { line: pickLine, probOver: pickProb } : null;
    const first = binary ? (point?.probOver ?? null) : propProbAtLine(point, parseLadder(projection.ladder), line);
    out.push({
      id: `p:${id}`,
      kind: "prop",
      gameId,
      startDate,
      title: player,
      market: binary ? market : `${market} ${formatLine(line)}`,
      matchup,
      search: `${player} ${team} ${opponent} ${game.homeSchool} ${game.awaySchool} ${market} ${marketRow.display_name}`.toLowerCase(),
      sides,
      model: first === null ? null : { kind: "prob", first },
    });
  }
  return out;
}

function gameEntries(
  games: GameSummary[],
  offers: DbRow[],
  projections: Map<number, GameProjection>,
  books: Map<string, string>,
): CheckEntry[] {
  const gameById = new Map(games.map((g) => [g.gameId, g]));
  type Group = { game: GameSummary; period: ModelPeriod; market: OddsMarket; line: number | null; first: Record<string, number>; second: Record<string, number> };
  const groups = new Map<string, Group>();
  for (const row of offers) {
    const book = row.book as { key: string; display_name: string; market_role: string };
    if (SLIP_HIDDEN_BOOKS.has(book.key) || book.market_role === "exchange") continue;
    const game = gameById.get(Number(row.game_id));
    if (!game) continue;
    const period = row.period as ModelPeriod;
    const market = row.market as OddsMarket;
    const line = market === "h2h" ? null : num(row.line);
    if (market !== "h2h" && line === null) continue;
    const key = `${game.gameId}:${period}:${market}:${line ?? ""}`;
    const group = groups.get(key) ?? { game, period, market, line, first: {}, second: {} };
    const first = row.side === "home" || row.side === "over";
    (first ? group.first : group.second)[book.key] = Number(row.price);
    books.set(book.key, book.display_name);
    groups.set(key, group);
  }

  const out: CheckEntry[] = [];
  for (const [id, { game, period, market, line, first, second }] of groups) {
    if (Object.keys(first).length === 0 && Object.keys(second).length === 0) continue;
    const home = game.homeAbbreviation ?? game.homeSchool;
    const away = game.awayAbbreviation ?? game.awaySchool;
    const prefix = PERIOD_PREFIX[period];
    const leg = (side: GameLeg["side"]): GameLeg => ({
      kind: "game", gameId: game.gameId, period, market, side, line, home, away, startDate: game.startDate,
    });
    const [firstSide, secondSide] = market === "totals" ? (["over", "under"] as const) : (["home", "away"] as const);
    const label = (side: "first" | "second") => {
      if (market === "h2h") return `${prefix}${side === "first" ? home : away} ML`;
      if (market === "totals") return prefix + totalSideLabel(line as number, side);
      return prefix + spreadSideLabel(line as number, side, home, away);
    };
    // Home (or over) first: the model's probability is stated for it.
    const sides: CheckSide[] = [
      { leg: leg(firstSide), label: label("first"), prices: first },
      { leg: leg(secondSide), label: label("second"), prices: second },
    ];
    out.push({
      id: `g:${id}`,
      kind: "game",
      gameId: game.gameId,
      startDate: game.startDate,
      title: `${away} @ ${home}`,
      market: `${prefix}${MARKET_LABEL[market]}`,
      matchup: `${away} @ ${home}`,
      search: `${away} ${home} ${game.awaySchool} ${game.homeSchool} ${prefix}${MARKET_LABEL[market]} ${market === "h2h" ? "ml" : ""}`.toLowerCase(),
      sides,
      model: gameModel(projections.get(game.gameId), period, market, line),
    });
  }
  return out;
}

function gameModel(
  projection: GameProjection | undefined,
  period: ModelPeriod,
  market: OddsMarket,
  line: number | null,
): CheckModel {
  // First half and first quarter are not calibrated; a team new to FBS shows
  // no model numbers anywhere (migration 0083).
  if (!projection || projection.missingPriorSeason || period !== "full") return null;
  if (market === "h2h") return { kind: "h2h", rawHome: projection.pHomeWin };
  if (line === null) return null;
  const range = market === "spreads" ? projection.periods.full.margin : projection.periods.full.total;
  const priced = market === "spreads" ? projection.spread : projection.total;
  return { kind: "prob", first: gameProbAtLine(market, range, line, priced) };
}
