/**
 * Which week the board opens on.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). It reasons only about kickoff times and
 * needs no database, which is why it lives here rather than beside the read in
 * `lib/data/slate.ts` where it started — it is a product decision worth testing
 * without a Supabase connection.
 *
 * THE RULE IT REPLACES WAS "THE LATEST WEEK WITH OUTPUT", and that was correct
 * for exactly as long as every projected week was in the past. Its docstring
 * defended the off-season case and was right to: on 2026-08-01 the newest data
 * was 2025 week 12, "the week containing today" pointed at nothing, and opening
 * on an empty board to be literally correct about the date is a worse product
 * than opening on the most recent real slate.
 *
 * What changed is that the model now projects FORWARD. The moment 2026 week 2
 * was published the board stopped opening on the opening weekend and opened on
 * a slate three weeks out instead, with week 1 pushed behind the strip's
 * expander. And it would have got worse rather than settling: the Tuesday cron
 * runs `run_projections --all-weeks`, so by September every remaining week of
 * the season exists and "the latest" is the last week in DECEMBER. The board
 * would have opened on the final week of the season, every day, all season.
 *
 * So the rule is now the next week to KICK OFF, with the old rule kept as the
 * fallback for when there is no such week. Both halves are load-bearing:
 * forward-looking while a season is live, backward-looking in the off-season.
 */

import type { SlateWeek } from "@/lib/core/types";

/**
 * The week the board opens on: the earliest one still to finish, else the last.
 *
 * MEASURED ON `lastKickoff`, NOT `firstKickoff`. A week's games run Tuesday
 * evening through Saturday night, so a week is still the one you want until its
 * OWN last game has kicked. Keying on the first kickoff would flip the board to
 * next week on Tuesday night, in the middle of the slate a reader is watching,
 * and would do it while most of that week's games were still unplayed.
 *
 * A week with no kickoff time at all cannot be compared and is never chosen as
 * the upcoming one; it stays reachable through the strip. `start_time_tbd` is a
 * real state in the schedule feed, so this is a case rather than a defect.
 *
 * `now` is injected so the choice is testable. Every caller is a server
 * component, so the server's clock is the only one involved and there is no
 * hydration mismatch to worry about.
 */
export function defaultWeek(
  weeks: SlateWeek[],
  now: Date = new Date(),
): SlateWeek | null {
  if (weeks.length === 0) return null;

  const millis = now.getTime();
  let upcoming: SlateWeek | null = null;
  let upcomingEnd = Number.POSITIVE_INFINITY;

  for (const week of weeks) {
    if (!week.lastKickoff) continue;
    const end = Date.parse(week.lastKickoff);
    if (!Number.isFinite(end) || end < millis) continue;
    if (end < upcomingEnd) {
      upcoming = week;
      upcomingEnd = end;
    }
  }

  // The off-season fallback, preserved deliberately: with every week behind us
  // the most recent real slate beats an empty board.
  return upcoming ?? weeks[weeks.length - 1];
}

/** Resolve a requested week, falling back to `defaultWeek` when it is unknown. */
export function findWeek(
  weeks: SlateWeek[],
  season: number | undefined,
  week: number | undefined,
  now: Date = new Date(),
): SlateWeek | null {
  if (season === undefined || week === undefined) return defaultWeek(weeks, now);
  return (
    weeks.find((entry) => entry.season === season && entry.week === week) ??
    defaultWeek(weeks, now)
  );
}
