/**
 * Team identity and conference membership for one season.
 *
 * CONFERENCE IS SEASON-SCOPED, NOT A PROPERTY OF A TEAM. Membership lives on
 * `team_seasons`, and in this sport that is not pedantry: realignment moved
 * Texas and Oklahoma to the SEC and most of the Pac-12 elsewhere inside the two
 * seasons this project covers. Reading conference from `teams` would quietly
 * apply today's alignment to a 2024 game.
 *
 * Scoped to a set of team ids rather than the whole season on purpose. A season
 * carries ~680 rows across every classification, which is close enough to
 * PostgREST's 1,000-row cap to be worth not flirting with, and the callers here
 * only ever want the teams on one week's slate — about a hundred.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";

export type TeamInfo = {
  teamId: number;
  school: string;
  abbreviation: string | null;
  color: string | null;
  altColor: string | null;
  /** Null for a team with no conference row that season, e.g. an FCS visitor. */
  conferenceName: string | null;
  conferenceIsDisplayed: boolean;
};

type EmbeddedTeam = {
  school: string;
  abbreviation: string | null;
  color: string | null;
  alt_color: string | null;
} | null;

type EmbeddedConference = { name: string; is_displayed: boolean } | null;

export async function getTeamDirectory(
  season: number,
  teamIds: number[],
): Promise<Map<number, TeamInfo>> {
  const directory = new Map<number, TeamInfo>();
  const wanted = [...new Set(teamIds)];
  if (wanted.length === 0) return directory;

  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("team_seasons")
      .select(
        "team_id, " +
          "team:teams!team_id(school, abbreviation, color, alt_color), " +
          "conference:conferences!conference_id(name, is_displayed)",
      )
      .eq("season", season)
      .in("team_id", wanted),
    "team_seasons (directory)",
  );

  for (const row of rows) {
    const team = row.team as EmbeddedTeam;
    const conference = row.conference as EmbeddedConference;
    if (!team) continue;
    directory.set(row.team_id as number, {
      teamId: row.team_id as number,
      school: team.school,
      abbreviation: team.abbreviation,
      color: team.color,
      altColor: team.alt_color,
      conferenceName: conference?.name ?? null,
      conferenceIsDisplayed: conference?.is_displayed ?? false,
    });
  }

  return directory;
}
