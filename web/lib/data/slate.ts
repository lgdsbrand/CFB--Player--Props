/**
 * Which weeks the board can show, and the games inside one.
 *
 * A NOTE ON THE SELECTOR. CLAUDE.md §7 specifies a date strip running two days
 * back to two days ahead, matching the client's MLB board — a daily sport where
 * every date has a full slate. College football is weekly, and the data agrees:
 * a week's games span Tuesday evening through Saturday night, so five
 * consecutive dates and one week cover almost the same span. The unit here is
 * therefore the WEEK, labelled with the dates it covers, because that is the
 * unit every model output is keyed by. Selecting a bare date would need a
 * mapping back to a week for every read.
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { DEFAULT_SPORT } from "@/lib/core/sport";
import type { SlateWeek } from "@/lib/core/types";
import { type DbRow, unwrap } from "@/lib/data/query";
import { cachedRead } from "@/lib/data/cache";

/**
 * The view groups by sport as well as by week (migration 0036), so without this
 * predicate a second sport's week 3 arrives as a SECOND strip entry for week 3,
 * and `defaultWeek` picks between them by kickoff time.
 */
async function readSlateWeeks(): Promise<SlateWeek[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("v_slate_weeks")
      .select("season, week, games, projections, players, first_kickoff, last_kickoff")
      .eq("sport", DEFAULT_SPORT)
      .order("season")
      .order("week"),
    "v_slate_weeks",
  );

  return rows.map((row) => ({
    season: row.season as number,
    week: row.week as number,
    games: row.games as number,
    projections: row.projections as number,
    players: row.players as number,
    firstKickoff: row.first_kickoff as string | null,
    lastKickoff: row.last_kickoff as string | null,
  }));
}

/**
 * Which week the board opens on now lives in `lib/core/slate-view.ts`.
 *
 * It moved because it is a product decision about kickoff times with no
 * database in it, and because the version that lived here — "the latest week
 * with output" — shipped a bug that only appeared once the model started
 * projecting FORWARD. Re-exported so call sites keep importing it beside the
 * read that feeds it.
 */
export { defaultWeek, findWeek } from "@/lib/core/slate-view";

export type SlateGame = {
  gameId: number;
  startDate: string | null;
  neutralSite: boolean;
  homeTeamId: number;
  awayTeamId: number;
  homeAbbreviation: string | null;
  awayAbbreviation: string | null;
  homeSchool: string;
  awaySchool: string;
};

type EmbeddedTeam = { abbreviation: string | null; school: string } | null;

/**
 * Games in one week, for the game selector.
 *
 * Read from `games` with the two teams embedded — about sixty rows. Deriving
 * the list from the board instead would mean pulling every projection in the
 * week (6,000+, past PostgREST's row cap) to recover sixty distinct game ids.
 *
 * The cost is that a game with no board rows still appears in the selector, and
 * picking it shows an empty board. That is the honest outcome: the game IS on
 * the slate, and "no player here clears the usage threshold" is a real answer
 * rather than a missing option.
 */
export async function getSlateGames(
  season: number,
  week: number,
): Promise<SlateGame[]> {
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("games")
      .select(
        "id, start_date, neutral_site, home_team_id, away_team_id, " +
          "home:teams!home_team_id(abbreviation, school), " +
          "away:teams!away_team_id(abbreviation, school)",
      )
      .eq("sport", DEFAULT_SPORT)
      .eq("season", season)
      .eq("week", week)
      .order("start_date"),
    "games (slate)",
  );

  return rows.map((row) => {
    const home = row.home as EmbeddedTeam;
    const away = row.away as EmbeddedTeam;
    return {
      gameId: row.id as number,
      startDate: row.start_date as string | null,
      neutralSite: row.neutral_site as boolean,
      homeTeamId: row.home_team_id as number,
      awayTeamId: row.away_team_id as number,
      homeAbbreviation: home?.abbreviation ?? null,
      awayAbbreviation: away?.abbreviation ?? null,
      homeSchool: home?.school ?? "",
      awaySchool: away?.school ?? "",
    };
  });
}

/**
 * The weeks with model output, cached.
 *
 * Written by `run_projections`, which is a weekly cron. Read by every page —
 * the week strip is on both — and by `findWeek`, which resolves the week the
 * rest of the page is scoped to, so this is the read every other read waits on.
 */
export const getSlateWeeks = cachedRead("slate-weeks", readSlateWeeks);
