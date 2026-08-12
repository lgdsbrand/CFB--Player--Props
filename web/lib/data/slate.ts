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

/**
 * The week's games moved to `lib/data/games.ts`.
 *
 * They read `v_slate_games` now (migration 0037), which the Analyze Games view
 * also reads — so the board's game selector and the games index cannot disagree
 * about which games a week holds. Re-exported here because every caller wants
 * the games beside the weeks.
 */
export { getSlateGames } from "@/lib/data/games";

/**
 * The weeks with model output, cached.
 *
 * Written by `run_projections`, which is a weekly cron. Read by every page —
 * the week strip is on both — and by `findWeek`, which resolves the week the
 * rest of the page is scoped to, so this is the read every other read waits on.
 */
export const getSlateWeeks = cachedRead("slate-weeks", readSlateWeeks);
