/**
 * Games on the slate — the read behind Analyze Games, and behind the board's
 * game selector.
 *
 * ONE DEFINITION OF "THE GAMES THIS WEEK", used by both. The board used to read
 * `games` directly with the two teams embedded while this view read something
 * else; two reads that both mean the same thing are how the weekly-targets
 * panel and the board came to disagree about scope, which no test caught
 * (see `lib/core/board-scope.ts`). They agree here because there is one query.
 *
 * `v_slate_games` carries every game, including those with no projections
 * behind them. That is deliberate and explained in migration 0037: a game with
 * no props still has a kickoff, a venue and a line. Deciding which games to
 * LIST is a conference question, and it belongs to the page — see
 * `offenseOnBoard`.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { DEFAULT_SPORT } from "@/lib/core/sport";
import type { GameSummary } from "@/lib/core/types";
import { type DbRow, unwrap } from "@/lib/data/query";

const COLUMNS =
  "game_id, season, week, start_date, start_time_tbd, neutral_site, completed, " +
  "home_points, away_points, home_team_id, home_school, home_abbreviation, " +
  "home_color, home_alt_color, away_team_id, away_school, away_abbreviation, " +
  "away_color, away_alt_color, venue_name, venue_city, venue_state, " +
  "home_spread, game_total, game_line_providers, home_poll_rank, " +
  "away_poll_rank, projections, players, calls";

function toGame(row: DbRow): GameSummary {
  return {
    gameId: row.game_id as number,
    season: row.season as number,
    week: row.week as number,
    startDate: row.start_date as string | null,
    startTimeTbd: row.start_time_tbd as boolean,
    neutralSite: row.neutral_site as boolean,
    completed: row.completed as boolean,
    homePoints: (row.home_points as number | null) ?? null,
    awayPoints: (row.away_points as number | null) ?? null,

    homeTeamId: row.home_team_id as number,
    homeSchool: row.home_school as string,
    homeAbbreviation: (row.home_abbreviation as string | null) ?? null,
    homeColor: (row.home_color as string | null) ?? null,
    homeAltColor: (row.home_alt_color as string | null) ?? null,

    awayTeamId: row.away_team_id as number,
    awaySchool: row.away_school as string,
    awayAbbreviation: (row.away_abbreviation as string | null) ?? null,
    awayColor: (row.away_color as string | null) ?? null,
    awayAltColor: (row.away_alt_color as string | null) ?? null,

    venueName: (row.venue_name as string | null) ?? null,
    venueCity: (row.venue_city as string | null) ?? null,
    venueState: (row.venue_state as string | null) ?? null,

    homeSpread: (row.home_spread as number | null) ?? null,
    gameTotal: (row.game_total as number | null) ?? null,
    gameLineProviders: (row.game_line_providers as number | null) ?? null,

    homePollRank: (row.home_poll_rank as number | null) ?? null,
    awayPollRank: (row.away_poll_rank as number | null) ?? null,

    projections: row.projections as number,
    players: row.players as number,
    calls: row.calls as number,
  };
}

/**
 * Every game in one week, in kickoff order.
 *
 * About a hundred rows, comfortably inside PostgREST's cap. Ordered by kickoff
 * with the id as a tiebreak so two games at the same minute keep a stable order
 * across requests — without it the list reshuffles between renders and a game
 * card appears to move on its own.
 */
export async function getSlateGames(
  season: number,
  week: number,
): Promise<GameSummary[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_slate_games")
      .select(COLUMNS)
      .eq("sport", DEFAULT_SPORT)
      .eq("season", season)
      .eq("week", week)
      .order("start_date", { nullsFirst: false })
      .order("game_id"),
    "v_slate_games",
  );
  return rows.map(toGame);
}

/**
 * One game, by id.
 *
 * Not narrowed by season or week: the id already identifies the game, and
 * requiring the caller to carry a matching season and week would mean a URL
 * with a stale week silently 404s a game that exists.
 */
export async function getGame(gameId: number): Promise<GameSummary | null> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_slate_games")
      .select(COLUMNS)
      .eq("game_id", gameId)
      .limit(1),
    "v_slate_games (one)",
  );
  return rows.length > 0 ? toGame(rows[0]) : null;
}
