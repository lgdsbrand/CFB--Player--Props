/**
 * The rule plays read: `v_rule_plays` (migration 0091), one football week of
 * one sport. Public before kickoff by design (`game_picks_rule_plays_public`);
 * a few dozen rows a week, far inside PostgREST's 1,000.
 */

import type { RulePlay } from "@/lib/core/rule-plays";
import type { Sport } from "@/lib/core/sport";
import { type DbRow, num, unwrap } from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

const COLUMNS =
  "id, game_id, market, side, line, price, model_prob, raw_prob, edge, model_line, gap, " +
  "sportsbook_name, season, week, start_date, neutral_site, home_points, away_points, final, " +
  "home_school, home_abbreviation, home_color, home_alt_color, " +
  "away_school, away_abbreviation, away_color, away_alt_color";

/**
 * A failed read is an empty list, logged, not a failed page: the panel sits
 * above the whole slate, and losing it must not take Analyze Games down.
 */
export async function getRulePlays(sport: Sport, season: number, week: number): Promise<RulePlay[]> {
  const supabase = createServerSupabaseClient();
  try {
    const rows = unwrap<DbRow[]>(
      await supabase
        .from("v_rule_plays")
        .select(COLUMNS)
        .eq("sport", sport)
        .eq("season", season)
        .eq("week", week)
        .order("start_date", { ascending: true }),
      "v_rule_plays",
    );
    return rows.map(toPlay);
  } catch (error) {
    console.error("Rule plays read failed; showing none.", error);
    return [];
  }
}

function toPlay(row: DbRow): RulePlay {
  return {
    id: Number(row.id),
    gameId: Number(row.game_id),
    market: row.market as RulePlay["market"],
    side: row.side as RulePlay["side"],
    line: num(row.line),
    price: Number(row.price),
    modelProb: Number(row.model_prob),
    rawProb: num(row.raw_prob),
    edge: num(row.edge),
    modelLine: num(row.model_line),
    gap: num(row.gap),
    sportsbookName: (row.sportsbook_name as string | null) ?? null,
    season: Number(row.season),
    week: Number(row.week),
    startDate: row.start_date as string,
    neutralSite: Boolean(row.neutral_site),
    homePoints: num(row.home_points),
    awayPoints: num(row.away_points),
    final: Boolean(row.final),
    home: (row.home_abbreviation as string | null) ?? (row.home_school as string),
    away: (row.away_abbreviation as string | null) ?? (row.away_school as string),
    homeSchool: row.home_school as string,
    awaySchool: row.away_school as string,
    homeColor: (row.home_color as string | null) ?? null,
    homeAltColor: (row.home_alt_color as string | null) ?? null,
    awayColor: (row.away_color as string | null) ?? null,
    awayAltColor: (row.away_alt_color as string | null) ?? null,
  };
}
