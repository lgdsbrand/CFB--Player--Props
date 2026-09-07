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
 *
 * ---------------------------------------------------------------------------
 * AMENDED 2026-09-06 — STARTED GAMES ARE KEPT FOR THE REST OF THE SLATE DAY
 * ---------------------------------------------------------------------------
 * The rule above is now a SORT rather than a filter, for the day being played.
 * A game that has kicked off stays on the board until the slate day rolls over,
 * sorted beneath everything still to come; only yesterday's games are dropped.
 *
 * `hasKickedOff` is therefore no longer "should this be hidden" — it is "should
 * this be marked and demoted". Nothing that calls it changed meaning, but the
 * two questions were the same question until today and will not be again.
 *
 * TWO THINGS TO SAY WHEREVER A STARTED ROW IS RENDERED, because both are
 * invisible and both would otherwise read as bugs:
 *   * the odds are the LAST SEEN ones, since books pull player props at
 *     kickoff — they are not live;
 *   * the projection is the PRE-GAME number held on screen. Nothing in this
 *     project models a game in progress.
 */

// Relative and extensioned, matching every other value import inside
// `lib/core`: these modules are run directly by `node --test`, where the `@/`
// alias does not exist. A type-only import can use it because it is erased.
import { slateDayStart } from "./slate-days.ts";

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
 * The instant the reads filter against: the START OF THE CURRENT SLATE DAY.
 *
 * CHANGED 2026-09-06, at the client's request, and the change is one line with
 * a long reason. It used to be "now", so a game left the board at its own
 * kickoff. He wanted the day's projections to stay up until the day's games
 * were over rather than disappearing one at a time through a Saturday
 * afternoon.
 *
 * WHAT KEEPS THE ORIGINAL DEFECT FROM COMING BACK. Hiding started games was not
 * arbitrary: before that rule the board carried 402 rows of already-played
 * games on 2026 week 1, including every one of that week's 66 edges, sorted to
 * the top because the board orders by edge. Widening the window alone would
 * reinstate exactly that. It is safe only because `v_board_rows.has_kicked_off`
 * (migration 0052) now leads the sort chain, so started rows are still on the
 * board but always beneath the ones a reader can act on.
 *
 * WHY NOT ROUNDED-TO-THE-MINUTE ANY MORE. That rounding existed so several
 * reads in one render could not be counted against different instants, and so
 * the timestamp would not behave as a cache-buster. A slate-day boundary is far
 * more stable than a rounded minute — it changes once a day — so it satisfies
 * both properties by construction.
 *
 * The boundary itself is Eastern and rolls over at 04:00, not midnight; see
 * `lib/core/slate-days.ts` for why a late Pacific kickoff makes that necessary.
 */
export function kickoffCutoff(now: Date = new Date()): Date {
  return slateDayStart(now);
}
