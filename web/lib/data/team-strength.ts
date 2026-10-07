import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";
import type { Sport } from "@/lib/core/sport";
import type { StrengthRow } from "@/lib/core/team-strength";

/**
 * The league a team is ranked within, per sport. College ranks among FBS:
 * the table also carries the FCS sides of FBS games, and ranking a team "40th
 * of 235" against opponents seen once in week one would flatter everyone. The
 * NFL has no classification and every team is in the league.
 */
const RANKED_CLASSIFICATION: Record<Sport, string | null> = { cfb: "fbs", nfl: null };

/**
 * Every ranked team's opponent-adjusted strength ENTERING a week.
 *
 * `as_of_week = week` is fitted on games before that week (migration 0077),
 * so a game page reads exactly what was known at kickoff. About 235 rows a
 * week, well inside PostgREST's 1,000.
 *
 * SPORT IS REQUIRED. `team_strength_ratings` has no sport column, so without
 * it an NFL game page read that week's COLLEGE rows, found neither of its
 * teams and rendered an empty panel — the sport-blind read this build keeps
 * producing (see `lib/core/sport.ts`). The league is taken from `team_seasons`
 * joined to `teams.sport`. The NFL has no rows today, so its page simply has
 * no panel.
 */
export async function getTeamStrength(
  season: number,
  week: number,
  sport: Sport,
): Promise<StrengthRow[]> {
  const supabase = createServerSupabaseClient();
  let league = supabase
    .from("team_seasons")
    .select("team_id, teams!inner(sport)")
    .eq("season", season)
    .eq("teams.sport", sport);
  const classification = RANKED_CLASSIFICATION[sport];
  if (classification) league = league.eq("classification", classification);

  const [rows, members] = await Promise.all([
    supabase
      .from("team_strength_ratings")
      .select(
        "team_id, games_included, off_points_pg, off_ppa, off_success, off_plays_pg, " +
          "def_points_pg, def_ppa, def_success",
      )
      .eq("season", season)
      .eq("as_of_week", week),
    league,
  ]);
  const ids = new Set(
    unwrap<DbRow[]>(members, "team_seasons (league)").map((r) => r.team_id as number),
  );
  const num = (value: unknown) =>
    value === null || value === undefined ? null : Number(value);
  return unwrap<DbRow[]>(rows, "team_strength_ratings")
    .filter((r) => ids.has(r.team_id as number))
    .map((r) => ({
      teamId: r.team_id as number,
      gamesIncluded: r.games_included as number,
      offPointsPg: num(r.off_points_pg),
      offPpa: num(r.off_ppa),
      offSuccess: num(r.off_success),
      offPlaysPg: num(r.off_plays_pg),
      defPointsPg: num(r.def_points_pg),
      defPpa: num(r.def_ppa),
      defSuccess: num(r.def_success),
    }));
}
