/**
 * Game odds and records — the reads behind the game model's screens
 * (CLAUDE.md §11, G1).
 *
 * THE SLATE READS A SUMMARY, NEVER PER-BOOK ROWS. A full slate is ~85 games x
 * 28 books x 3 markets, about 7,000 current prices, and PostgREST stops at
 * 1,000 rows without saying so (`MAX_ROWS_PER_REQUEST`). A table built from
 * per-book rows would silently lose books from the later games. The per-book
 * read is for ONE game, where it is ~85 rows.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type {
  BookOdds,
  GameMarketSummary,
  GameOddsSummary,
  GradedGame,
  MarketRole,
  OddsMarket,
} from "@/lib/core/game-lines";
import { type DbRow, unwrap } from "@/lib/data/query";

const SUMMARY_COLUMNS =
  "game_id, market, books, consensus_line, consensus_first_line, consensus_fair, " +
  "sharp_line, sharp_first_line, sharp_fair, sharp_first_fair, exchange_line, " +
  "exchange_fair, retail_line, retail_first_line, retail_fair, sharp_books, " +
  "exchange_books, retail_books, first_seen_at, last_moved_at";

const num = (value: unknown): number | null =>
  value === null || value === undefined ? null : Number(value);

function toSummary(row: DbRow): GameMarketSummary {
  return {
    gameId: row.game_id as number,
    market: row.market as OddsMarket,
    books: Number(row.books),
    consensusLine: num(row.consensus_line),
    consensusFirstLine: num(row.consensus_first_line),
    consensusFair: num(row.consensus_fair),
    sharpLine: num(row.sharp_line),
    sharpFirstLine: num(row.sharp_first_line),
    sharpFair: num(row.sharp_fair),
    sharpFirstFair: num(row.sharp_first_fair),
    exchangeLine: num(row.exchange_line),
    exchangeFair: num(row.exchange_fair),
    retailLine: num(row.retail_line),
    retailFirstLine: num(row.retail_first_line),
    retailFair: num(row.retail_fair),
    sharpBooks: Number(row.sharp_books),
    exchangeBooks: Number(row.exchange_books),
    retailBooks: Number(row.retail_books),
    firstSeenAt: (row.first_seen_at as string | null) ?? null,
    lastMovedAt: (row.last_moved_at as string | null) ?? null,
  };
}

/**
 * Full-game summaries for a set of games, keyed by game id. Three rows per
 * game at most, so a 100-game slate is 300 rows.
 */
export async function getGameOddsSummaries(
  gameIds: number[],
): Promise<Map<number, GameOddsSummary>> {
  const out = new Map<number, GameOddsSummary>();
  if (gameIds.length === 0) return out;
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_game_odds_summary")
      .select(SUMMARY_COLUMNS)
      .in("game_id", gameIds)
      .eq("period", "full"),
    "v_game_odds_summary",
  );
  for (const row of rows) {
    const summary = toSummary(row);
    const entry = out.get(summary.gameId) ?? {};
    entry[summary.market] = summary;
    out.set(summary.gameId, entry);
  }
  return out;
}

const BOOK_COLUMNS =
  "sportsbook_key, sportsbook_name, market_role, market, line, home_price, " +
  "away_price, over_price, under_price, first_line, captured_at, first_captured_at";

/** Every book's current full-game price on one game. */
export async function getGameBookOdds(gameId: number): Promise<BookOdds[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_game_odds_current")
      .select(BOOK_COLUMNS)
      .eq("game_id", gameId)
      .eq("period", "full"),
    "v_game_odds_current",
  );
  return rows.map((row) => ({
    sportsbookKey: row.sportsbook_key as string,
    sportsbookName: row.sportsbook_name as string,
    role: row.market_role as MarketRole,
    market: row.market as OddsMarket,
    line: num(row.line),
    homePrice: num(row.home_price),
    awayPrice: num(row.away_price),
    overPrice: num(row.over_price),
    underPrice: num(row.under_price),
    firstLine: num(row.first_line),
    capturedAt: row.captured_at as string,
    firstCapturedAt: row.first_captured_at as string,
  }));
}

// =============================================================================
// Played games with their lines — ATS, over/under and head-to-head
// =============================================================================
// Graded against CFBD's consensus (`v_game_line_consensus`): the median across
// ESPN Bet, DraftKings and Bovada, refreshed daily until kickoff, so for a
// played game it is the last number before the game rather than a true close.
// It costs no odds credits and reaches back to 2024; our own captures begin
// 2026-09-23.

const GAME_COLUMNS =
  "id, season, week, start_date, home_team_id, away_team_id, home_points, away_points";

async function withLines(games: DbRow[]): Promise<GradedGame[]> {
  const played = games.filter(
    (g) => g.home_points !== null && g.away_points !== null,
  );
  if (played.length === 0) return [];
  const supabase = createServerSupabaseClient();
  const lines = unwrap<DbRow[]>(
    await supabase
      .from("v_game_line_consensus")
      .select("game_id, spread, over_under")
      .in(
        "game_id",
        played.map((g) => g.id as number),
      ),
    "v_game_line_consensus",
  );
  const byGame = new Map(lines.map((l) => [l.game_id as number, l]));
  return played.map((g) => {
    const line = byGame.get(g.id as number);
    return {
      gameId: g.id as number,
      season: g.season as number,
      week: g.week as number,
      startDate: (g.start_date as string | null) ?? null,
      homeTeamId: g.home_team_id as number,
      awayTeamId: g.away_team_id as number,
      homePoints: g.home_points as number,
      awayPoints: g.away_points as number,
      homeSpread: line ? num(line.spread) : null,
      total: line ? num(line.over_under) : null,
    };
  });
}

/**
 * Both teams' completed games this season BEFORE this one, with lines.
 * Bounded by kickoff rather than week so a postseason game, which CFBD
 * numbers from week 1 again, cannot leak in (see the bowl-week collision).
 */
export async function getSeasonResults(
  season: number,
  teamIds: number[],
  before: string | null,
): Promise<GradedGame[]> {
  const supabase = createServerSupabaseClient();
  const ids = teamIds.join(",");
  let query = supabase
    .from("games")
    .select(GAME_COLUMNS)
    .eq("season", season)
    .eq("completed", true)
    .or(`home_team_id.in.(${ids}),away_team_id.in.(${ids})`)
    .order("start_date");
  if (before) query = query.lt("start_date", before);
  return withLines(unwrap<DbRow[]>(await query, "games (season results)"));
}

/** Every completed meeting between two teams we hold, newest first. */
export async function getHeadToHead(
  teamA: number,
  teamB: number,
  before: string | null,
): Promise<GradedGame[]> {
  const supabase = createServerSupabaseClient();
  let query = supabase
    .from("games")
    .select(GAME_COLUMNS)
    .eq("completed", true)
    .or(
      `and(home_team_id.eq.${teamA},away_team_id.eq.${teamB}),` +
        `and(home_team_id.eq.${teamB},away_team_id.eq.${teamA})`,
    )
    .order("start_date", { ascending: false });
  if (before) query = query.lt("start_date", before);
  return withLines(unwrap<DbRow[]>(await query, "games (head to head)"));
}

/** The earliest season this database holds games for, for honest captions. */
export async function getEarliestSeason(sport: string): Promise<number | null> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("games")
      .select("season")
      .eq("sport", sport)
      .order("season")
      .limit(1),
    "games (earliest season)",
  );
  return rows.length > 0 ? (rows[0].season as number) : null;
}
