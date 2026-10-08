/**
 * The tracker read: `v_tracker_picks` (migration 0090), every graded or
 * in-progress pick of one sport, both engines.
 *
 * The view exposes a pick only once its game has kicked off (the
 * `game_picks_public_after_kickoff` policy), so nothing here can leak a pick
 * still to play. A season is ~100 picks a week per engine; paged so a full
 * season is never silently capped at PostgREST's 1,000 rows.
 */

import type { Engine, TrackerPick, TrackerResult } from "@/lib/core/tracker";
import type { Sport } from "@/lib/core/sport";
import { type DbRow, MAX_ROWS_PER_REQUEST, num, unwrap } from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

const COLUMNS =
  "id, engine, game_id, market, side, line, price, model_prob, edge, " +
  "sportsbook_name, season, week, start_date, home_points, away_points, " +
  "home_school, home_abbreviation, away_school, away_abbreviation, result, units";

/** Twenty pages: far past a full season of both engines. */
const MAX_PAGES = 20;

export async function getTrackerPicks(sport: Sport, engine: Engine): Promise<TrackerPick[]> {
  const supabase = createServerSupabaseClient();
  const rows: DbRow[] = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const from = page * MAX_ROWS_PER_REQUEST;
    const batch = unwrap<DbRow[]>(
      await supabase
        .from("v_tracker_picks")
        .select(COLUMNS)
        .eq("sport", sport)
        .eq("engine", engine)
        .eq("period", "full")
        .order("id", { ascending: true })
        .range(from, from + MAX_ROWS_PER_REQUEST - 1),
      "v_tracker_picks",
    );
    rows.push(...batch);
    if (batch.length < MAX_ROWS_PER_REQUEST) break;
  }
  return rows.map(toPick);
}

function toPick(row: DbRow): TrackerPick {
  return {
    id: Number(row.id),
    engine: row.engine as Engine,
    gameId: Number(row.game_id),
    market: row.market as TrackerPick["market"],
    side: row.side as TrackerPick["side"],
    line: num(row.line),
    price: Number(row.price),
    modelProb: Number(row.model_prob),
    edge: num(row.edge),
    sportsbookName: (row.sportsbook_name as string | null) ?? null,
    season: Number(row.season),
    week: Number(row.week),
    startDate: row.start_date as string,
    homePoints: num(row.home_points),
    awayPoints: num(row.away_points),
    home: (row.home_abbreviation as string | null) ?? (row.home_school as string),
    away: (row.away_abbreviation as string | null) ?? (row.away_school as string),
    result: row.result as TrackerResult,
    units: num(row.units),
  };
}
