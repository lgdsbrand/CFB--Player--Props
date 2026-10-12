/**
 * The facts a saved slip's legs are settled from (`lib/core/slip-track.ts`).
 *
 * Sources, all read elsewhere on the site already:
 *   games              the final score of record and its line scores, from the
 *                      daily results ingest.
 *   live_scores        the scoreboard now, college only (migration 0088).
 *   markets            which box-score column each prop market is graded on.
 *   player_game_stats  the box score, the morning after the game.
 */

import { LIVE_COLUMNS, LIVE_SPORTS, toLiveScore } from "@/lib/core/live";
import type { LegRef } from "@/lib/core/slip-check";
import { propStatKey, type TrackFacts, type TrackGame } from "@/lib/core/slip-track";
import type { Sport } from "@/lib/core/sport";
import { type DbRow, num, unwrap } from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

const nums = (v: unknown) => (Array.isArray(v) ? v.map(Number) : null);

export async function getTrackFacts(refs: LegRef[], now: Date): Promise<TrackFacts> {
  const gameIds = [...new Set(refs.map((r) => r.gameId))];
  if (gameIds.length === 0) return { games: [], stats: {} };
  const props = refs.filter((r) => r.kind === "prop");
  const marketKeys = [...new Set(props.map((r) => r.marketKey))];
  const supabase = createServerSupabaseClient();

  const [gameRows, liveRows, marketRows] = await Promise.all([
    supabase
      .from("games")
      .select("id, sport, start_date, completed, home_points, away_points, home_line_scores, away_line_scores")
      .in("id", gameIds)
      .then((r) => unwrap<DbRow[]>(r, "games")),
    supabase
      .from("live_scores")
      .select(LIVE_COLUMNS)
      .in("game_id", gameIds)
      .then((r) => unwrap<DbRow[]>(r, "live_scores")),
    marketKeys.length === 0
      ? Promise.resolve([] as DbRow[])
      : supabase
          .from("markets")
          .select("key, stat_column")
          .in("key", marketKeys)
          .then((r) => unwrap<DbRow[]>(r, "markets")),
  ]);

  // The column names are interpolated into the select, so only plain ones pass.
  const statColumn = new Map(
    marketRows
      .filter((m) => typeof m.stat_column === "string" && /^[a-z0-9_]+$/.test(m.stat_column))
      .map((m) => [m.key as string, m.stat_column as string]),
  );
  const columns = [...new Set(statColumn.values())];
  const propGames = [...new Set(props.map((r) => r.gameId))];
  const statRows =
    columns.length === 0
      ? []
      : unwrap<DbRow[]>(
          await supabase
            .from("player_game_stats")
            .select(`game_id, player_id, ${columns.join(", ")}`)
            .in("game_id", propGames)
            .in("player_id", [...new Set(props.map((r) => r.playerId))]),
          "player_game_stats",
        );

  const stats: TrackFacts["stats"] = {};
  for (const ref of props) {
    const column = statColumn.get(ref.marketKey);
    const row = statRows.find((s) => Number(s.game_id) === ref.gameId && Number(s.player_id) === ref.playerId);
    if (!row || !column) continue;
    stats[propStatKey(ref.gameId, ref.playerId, ref.marketKey)] = num(row[column]);
  }

  // A prop with no box-score row: is that because the box score is not in
  // yet, or because the player has no line in it? One row of the game says.
  const withRows = new Set(statRows.map((s) => Number(s.game_id)));
  const finished = new Set(
    gameRows
      .filter((g) => g.completed === true || Date.parse(String(g.start_date)) < now.getTime() - 5 * 3_600_000)
      .map((g) => Number(g.id)),
  );
  const toProbe = propGames.filter((id) => !withRows.has(id) && finished.has(id));
  const probed = await Promise.all(
    toProbe.map(async (id) => {
      const rows = unwrap<DbRow[]>(
        await supabase.from("player_game_stats").select("game_id").eq("game_id", id).limit(1),
        "player_game_stats",
      );
      return rows.length > 0 ? id : null;
    }),
  );
  for (const id of probed) if (id !== null) withRows.add(id);

  const games: TrackGame[] = gameRows.map((g) => {
    const id = Number(g.id);
    const live = liveRows.find((l) => Number(l.game_id) === id);
    const home = num(g.home_points);
    const away = num(g.away_points);
    return {
      gameId: id,
      startDate: (g.start_date as string | null) ?? null,
      final:
        g.completed === true && home !== null && away !== null
          ? {
              homePoints: home,
              awayPoints: away,
              homeLineScores: nums(g.home_line_scores),
              awayLineScores: nums(g.away_line_scores),
            }
          : null,
      live: live ? toLiveScore(live) : null,
      hasLiveFeed: LIVE_SPORTS.includes(g.sport as Sport),
      statsIn: withRows.has(id),
    };
  });

  return { games, stats };
}
