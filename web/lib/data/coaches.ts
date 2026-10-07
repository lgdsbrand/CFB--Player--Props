import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";
import type { CoachSeason, HeadCoach, WinLoss } from "@/lib/core/coach";

/**
 * Each team's head coach(es) this season and every season those coaches have
 * on record (migration 0084). Two reads: who coaches the teams, then their
 * seasons — a few dozen rows for a game page.
 */
export async function getCoaches(
  season: number,
  teamIds: number[],
): Promise<{ byTeam: Map<number, HeadCoach[]>; seasons: CoachSeason[] }> {
  const byTeam = new Map<number, HeadCoach[]>();
  if (teamIds.length === 0) return { byTeam, seasons: [] };
  const supabase = createServerSupabaseClient();
  const assigned = unwrap<DbRow[]>(
    await supabase
      .from("team_coaches")
      .select("team_id, coach_id, hire_date, coach:coaches!coach_id(first_name, last_name)")
      .eq("season", season)
      .in("team_id", teamIds),
    "team_coaches",
  );
  for (const row of assigned) {
    const coach = row.coach as { first_name: string; last_name: string } | null;
    const list = byTeam.get(row.team_id as number) ?? [];
    list.push({
      coachId: row.coach_id as number,
      firstName: coach?.first_name ?? "",
      lastName: coach?.last_name ?? "",
      hireDate: (row.hire_date as string | null) ?? null,
    });
    byTeam.set(row.team_id as number, list);
  }
  const coachIds = [...new Set(assigned.map((r) => r.coach_id as number))];
  if (coachIds.length === 0) return { byTeam, seasons: [] };

  const split = (row: DbRow, prefix: string): WinLoss | null =>
    row[`${prefix}_wins`] === null || row[`${prefix}_wins`] === undefined
      ? null
      : { w: row[`${prefix}_wins`] as number, l: (row[`${prefix}_losses`] as number) ?? 0 };
  const seasons = unwrap<DbRow[]>(
    await supabase
      .from("coach_seasons")
      .select(
        "coach_id, season, school, team_id, wins, losses, conf_wins, conf_losses, " +
          "home_wins, home_losses, away_wins, away_losses, neutral_wins, neutral_losses, " +
          "post_wins, post_losses",
      )
      .in("coach_id", coachIds),
    "coach_seasons",
  ).map((row) => ({
    coachId: row.coach_id as number,
    season: row.season as number,
    school: row.school as string,
    teamId: (row.team_id as number | null) ?? null,
    wins: row.wins as number,
    losses: row.losses as number,
    conference: split(row, "conf"),
    home: split(row, "home"),
    away: split(row, "away"),
    neutral: split(row, "neutral"),
    postseason: split(row, "post"),
  }));
  return { byTeam, seasons };
}
