/**
 * Which games are still worth showing — the board's "has it kicked off yet"
 * rule.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). It reasons only about kickoff times and
 * touches no database, for the same reason `slate-view.ts` does not: it is a
 * product decision worth testing without a Supabase connection.
 *
 * WHY THIS EXISTS. A CFBD week is ten calendar days wide, so for most of that
 * week the board carried games that had already been played. On 2026 week 1
 * that was the eight opening-Saturday games — 402 rows and every one of the
 * week's 66 edges — sitting at the top of a board a reader opens to decide what
 * to bet next. A settled prop is not a bet, and ordering by edge put the
 * unbettable rows FIRST.
 *
 * WHY KICKOFF AND NOT `completed`. `completed` is written by the stats ingest,
 * which is a cron — Sunday 09:00 UTC for a Saturday game. Keyed on that flag a
 * finished game would stay on the board for up to a day, and a game in progress
 * would stay there for its whole duration. Kickoff needs no ingest and is the
 * moment the pre-game number stops being actionable, which is the thing we
 * actually mean.
 *
 * A GAME WITH NO KICKOFF TIME IS UPCOMING. CFBD publishes early-week schedules
 * with `start_time_tbd`, and a null cannot be proven to be in the past. Hiding
 * those would drop real games off the board on the strength of a missing field
 * — the failure would be silent and would look like an ingest gap. Same
 * reasoning as `slateDays`, which leaves a TBD game out of every day pill but
 * never out of the board.
 *
 * WHAT THIS DELIBERATELY DOES NOT TOUCH. A player's own page still shows every
 * game he has played: history is that page's whole purpose, and the hit-rate
 * chart is built from exactly the games this rule hides. The rule applies where
 * props are OFFERED, not where they are recorded.
 */

/**
 * Has this game kicked off?
 *
 * `now` is injected so the choice is testable. Every caller is a server
 * component or a server-side read, so the server's clock is the only one
 * involved and there is no hydration mismatch to worry about.
 */
export function hasKickedOff(
  startDate: string | null,
  now: Date = new Date(),
): boolean {
  if (!startDate) return false;
  const at = Date.parse(startDate);
  // An unparseable date is treated the same as a missing one: shown, not
  // hidden. Dropping a row because a string failed to parse would be a silent
  // data loss dressed up as a product rule.
  if (!Number.isFinite(at)) return false;
  return at <= now.getTime();
}

/** The games a reader can still bet, in the order they arrived. */
export function upcomingGames<T extends { startDate: string | null }>(
  games: T[],
  now: Date = new Date(),
): T[] {
  return games.filter((game) => !hasKickedOff(game.startDate, now));
}

/** How many of these have already kicked off. Drives the "hidden" note. */
export function playedCount<T extends { startDate: string | null }>(
  games: T[],
  now: Date = new Date(),
): number {
  return games.length - upcomingGames(games, now).length;
}

/**
 * The instant the reads filter against, rounded DOWN to the minute.
 *
 * Rounding keeps the value stable across the several reads one page render
 * fires, so the row count beside the filters cannot be counted against a
 * different instant than the rows themselves — a one-second drift across a
 * kickoff would print a total that the page below it contradicts. It also stops
 * the timestamp behaving as a cache-buster on any read that keys on its
 * arguments.
 */
export function kickoffCutoff(now: Date = new Date()): Date {
  const rounded = new Date(now.getTime());
  rounded.setSeconds(0, 0);
  return rounded;
}
