/**
 * The props record reads (migration 0097).
 *
 * `v_prop_record` is already summed per week, market and edge band, a few
 * hundred rows a season; paged anyway so a long season is never silently cut
 * at PostgREST's 1,000 rows. The picks list is one page of
 * `v_prop_pick_results`, only games that have kicked off, newest first.
 */

import type { PropPick, PropRecordRow, WeekKey } from "@/lib/core/prop-record";
import type { Sport } from "@/lib/core/sport";
import { type DbRow, MAX_ROWS_PER_REQUEST, num, unwrap } from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

const MAX_PAGES = 10;
export const PICKS_PER_PAGE = 50;

export async function getPropRecord(sport: Sport): Promise<PropRecordRow[]> {
  const supabase = createServerSupabaseClient();
  const rows: DbRow[] = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const from = page * MAX_ROWS_PER_REQUEST;
    const batch = unwrap<DbRow[]>(
      await supabase
        .from("v_prop_record")
        .select("season, week, market_key, market_label, band, wins, losses, pushes, voids, pending, units, unpriced_wins")
        .eq("sport", sport)
        .order("season")
        .order("week")
        .order("market_key")
        .order("band", { nullsFirst: true })
        .range(from, from + MAX_ROWS_PER_REQUEST - 1),
      "v_prop_record",
    );
    rows.push(...batch);
    if (batch.length < MAX_ROWS_PER_REQUEST) break;
  }
  return rows.map((r) => ({
    season: r.season as number,
    week: r.week as number,
    marketKey: r.market_key as string,
    marketLabel: (r.market_label as string | null) ?? (r.market_key as string),
    band: num(r.band),
    wins: Number(r.wins),
    losses: Number(r.losses),
    pushes: Number(r.pushes),
    voids: Number(r.voids),
    pending: Number(r.pending),
    units: num(r.units) ?? 0,
    unpricedWins: Number(r.unpriced_wins),
  }));
}

/**
 * One page of graded picks, newest first. Asks for one more row than it shows,
 * so the page knows whether there is a next page without a count query.
 */
export async function getPropPicks(
  sport: Sport,
  scope: {
    season: number;
    week: WeekKey | null;
    minEdge: number | null;
    market: string | null;
    page: number;
  },
): Promise<{ picks: PropPick[]; hasMore: boolean }> {
  const supabase = createServerSupabaseClient();
  let query = supabase
    .from("v_prop_pick_results")
    .select(
      "id, start_date, player_name, team_abbreviation, opponent_abbreviation, market_label, " +
        "side, line, price, sportsbook_name, edge, actual, result, units",
    )
    .eq("sport", sport)
    .eq("season", scope.season)
    .lte("start_date", new Date().toISOString());
  if (scope.week) query = query.eq("week", scope.week.week);
  if (scope.minEdge !== null) query = query.gte("edge", scope.minEdge);
  if (scope.market) query = query.eq("market_key", scope.market);
  const from = scope.page * PICKS_PER_PAGE;
  const rows = unwrap<DbRow[]>(
    await query
      .order("start_date", { ascending: false })
      .order("id", { ascending: false })
      .range(from, from + PICKS_PER_PAGE),
    "v_prop_pick_results",
  );
  return {
    hasMore: rows.length > PICKS_PER_PAGE,
    picks: rows.slice(0, PICKS_PER_PAGE).map((r) => ({
      id: r.id as number,
      startDate: r.start_date as string,
      playerName: r.player_name as string,
      team: (r.team_abbreviation as string | null) ?? null,
      opponent: (r.opponent_abbreviation as string | null) ?? null,
      marketLabel: (r.market_label as string | null) ?? "",
      side: r.side as PropPick["side"],
      line: num(r.line) ?? 0,
      price: num(r.price),
      sportsbookName: (r.sportsbook_name as string | null) ?? null,
      edge: num(r.edge),
      actual: num(r.actual),
      result: r.result as PropPick["result"],
      units: num(r.units),
    })),
  };
}
