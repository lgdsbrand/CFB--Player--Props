import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";
import type { Sport } from "@/lib/core/sport";
import type { GamePower, PowerRanking } from "@/lib/core/power";
import { cachedLiveMapRead, cachedRead } from "@/lib/data/cache";

/**
 * Power ratings and game score (client, 2026-10-29; migration 20261029120000).
 *
 * POINT IN TIME, like the defense ratings beside them: the rankings for week
 * N are fitted on games before week N, so the table never knows a result the
 * reader is looking ahead at.
 */

/**
 * The ranked league's power order entering a week. Unranked teams (FCS) are
 * rated in the table but left out here. 136 rows in college, 32 in the NFL.
 *
 * SPORT IS REQUIRED: `team_power_ratings` carries no sport column, so the
 * filter goes through `teams` (the sport-blind read `lib/core/sport.ts` warns
 * about).
 */
async function readPowerRankings(
  season: number,
  week: number,
  sport: Sport,
): Promise<PowerRanking[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("team_power_ratings")
      .select(
        "team_id, rating, power_rank, games_included, " +
          "teams!inner(school, abbreviation, color, alt_color, sport)",
      )
      .eq("season", season)
      .eq("as_of_week", week)
      .eq("teams.sport", sport)
      .not("power_rank", "is", null)
      .order("power_rank"),
    "team_power_ratings",
  );
  return rows.map((r) => {
    const team = r.teams as DbRow;
    return {
      teamId: r.team_id as number,
      rank: r.power_rank as number,
      rating: Number(r.rating),
      games: r.games_included as number,
      school: team.school as string,
      abbreviation: (team.abbreviation as string | null) ?? null,
      color: (team.color as string | null) ?? null,
      altColor: (team.alt_color as string | null) ?? null,
    };
  });
}

export const getPowerRankings = cachedRead("power-rankings", readPowerRankings);

/** Each game's ranks, ratings and game score at its week, keyed by game id. */
async function readGamePower(gameIds: number[]): Promise<Map<number, GamePower>> {
  const out = new Map<number, GamePower>();
  if (gameIds.length === 0) return out;
  const supabase = createServerSupabaseClient();
  const num = (value: unknown) =>
    value === null || value === undefined ? null : Number(value);
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("game_power")
      .select("game_id, home_rank, away_rank, home_rating, away_rating, p_home_win, game_score")
      .in("game_id", gameIds),
    "game_power",
  );
  for (const r of rows) {
    out.set(r.game_id as number, {
      gameId: r.game_id as number,
      homeRank: num(r.home_rank),
      awayRank: num(r.away_rank),
      homeRating: num(r.home_rating),
      awayRating: num(r.away_rating),
      pHomeWin: Number(r.p_home_win),
      gameScore: r.game_score as number,
    });
  }
  return out;
}

export const getGamePower = cachedLiveMapRead("game-power", readGamePower);
