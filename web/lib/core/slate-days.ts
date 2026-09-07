/**
 * Splitting a slate week into the days actually played.
 *
 * WHY THIS EXISTS. A CFBD week is not a day and is not even close to one:
 * 2026 week 1 spans **ten calendar days and six distinct game days**, and the
 * eight games of opening Saturday are 402 of its 4,676 board rows while the
 * following Saturday alone is 3,125. A reader opening the board is therefore
 * mostly looking at games a week away. CLAUDE.md §7 asked for a date selector
 * strip from the start; only the week strip was built.
 *
 * WHY EASTERN AND NOT UTC. Late kickoffs cross midnight UTC — Memphis at UNLV
 * kicks 2026-08-30T02:00:00Z, which is Saturday **29 August** at 22:00 in the
 * US. Grouping by the UTC date would scatter a single Saturday night across two
 * "days" and file the late West Coast window under Sunday, which is both wrong
 * and the exact thing a day filter is supposed to fix. Eastern is the league's
 * own reference clock, and `Intl` resolves its DST shift — the season crosses
 * the November boundary, so a fixed −4 or −5 offset would be wrong for half of
 * it.
 *
 * WHY GAME IDS RATHER THAN A DATE RANGE. The board filters by `game_id`
 * already, and a list of ids is exact. Converting an Eastern calendar day back
 * into a UTC instant range to hand to PostgREST would reintroduce the DST
 * arithmetic this module exists to avoid, and would do it in the one place
 * (the query) where it could not be unit tested.
 */

import type { GameSummary } from "@/lib/core/types";

/** The league's reference clock. Not the viewer's — see the note above. */
export const SLATE_TIME_ZONE = "America/New_York";

/** `en-CA` renders as YYYY-MM-DD, which sorts lexicographically. */
const DAY_KEY_FORMAT = new Intl.DateTimeFormat("en-CA", {
  timeZone: SLATE_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const DAY_LABEL_FORMAT = new Intl.DateTimeFormat("en-US", {
  timeZone: SLATE_TIME_ZONE,
  weekday: "short",
  month: "short",
  day: "numeric",
});

export type SlateDay = {
  /** `YYYY-MM-DD` in `SLATE_TIME_ZONE`. The URL value. */
  key: string;
  /** `Sat Aug 29`. */
  label: string;
  /** Just `Sat`, for the narrow strip. */
  weekday: string;
  gameIds: number[];
};

/**
 * The hour, Eastern, at which one slate day gives way to the next.
 *
 * FOUR, NOT MIDNIGHT, and the difference is a real game rather than a margin of
 * comfort. A Hawai'i or late Pacific kickoff goes off around 23:00 Eastern and
 * finishes near 02:00 the next morning. Rolling the day over at midnight would
 * hide it from the board while it was still being played — which is the exact
 * complaint this whole rule exists to answer. By 04:00 Eastern nothing is in
 * progress anywhere in the country.
 */
export const SLATE_DAY_ROLLOVER_HOUR = 4;

/** Wall-clock reading of an instant in `SLATE_TIME_ZONE`. */
const WALL_CLOCK_FORMAT = new Intl.DateTimeFormat("en-CA", {
  timeZone: SLATE_TIME_ZONE,
  hour12: false,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function wallClockParts(at: Date) {
  const parts = WALL_CLOCK_FORMAT.formatToParts(at);
  const value = (type: string) =>
    Number(parts.find((p) => p.type === type)?.value ?? "0");
  return {
    year: value("year"),
    month: value("month"),
    day: value("day"),
    // `en-CA` with hour12:false renders midnight as 24, not 00.
    hour: value("hour") % 24,
    minute: value("minute"),
    second: value("second"),
  };
}

/**
 * How far `SLATE_TIME_ZONE` is from UTC at a given instant, in minutes.
 *
 * Derived by reading the instant's Eastern wall clock and asking what UTC
 * instant that reading would be if it were UTC. The gap is the offset. Doing it
 * this way rather than hardcoding −4/−5 is not pedantry: the season crosses the
 * November DST boundary, so a fixed offset is wrong for a third of it.
 */
function zoneOffsetMinutes(at: Date): number {
  const p = wallClockParts(at);
  const asIfUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return (asIfUtc - at.getTime()) / 60_000;
}

/**
 * The instant the current slate day began — `SLATE_DAY_ROLLOVER_HOUR` Eastern
 * on whichever Eastern date the slate is currently on.
 *
 * This is the board's "still today" boundary. Everything that kicked off after
 * it belongs to the day being played, whether or not it has finished.
 *
 * TWO PASSES, because converting a wall-clock time back into an instant is not
 * a subtraction. The offset depends on the instant, and the instant is what we
 * are solving for — so the first pass uses the offset as it is *now* to get
 * close, and the second corrects it using the offset at the answer. They differ
 * only across a DST transition, which is precisely the weekend nobody would
 * notice the bug on until a Saturday of games sorted wrongly.
 */
export function slateDayStart(now: Date = new Date()): Date {
  const p = wallClockParts(now);
  // Before the rollover hour we are still on yesterday's slate.
  const dayShift = p.hour < SLATE_DAY_ROLLOVER_HOUR ? -1 : 0;

  const wallAsUtc = Date.UTC(
    p.year,
    p.month - 1,
    p.day + dayShift,
    SLATE_DAY_ROLLOVER_HOUR,
    0,
    0,
  );

  let instant = wallAsUtc - zoneOffsetMinutes(now) * 60_000;
  instant = wallAsUtc - zoneOffsetMinutes(new Date(instant)) * 60_000;
  return new Date(instant);
}

/**
 * The Eastern calendar day a kickoff belongs to.
 *
 * Returns undefined for a game with no kickoff time — CFBD publishes those as
 * TBD early in the week. Such a game belongs to no day and is deliberately left
 * out of the strip rather than guessed into one.
 */
export function slateDayKey(startDate: string | null): string | undefined {
  if (!startDate) return undefined;
  const at = new Date(startDate);
  if (Number.isNaN(at.getTime())) return undefined;
  return DAY_KEY_FORMAT.format(at);
}

/**
 * The distinct days of a week, in kickoff order, each carrying its games.
 *
 * Games whose kickoff is TBD appear in no day. That is why the strip always
 * offers an ALL option and defaults to it: filtering to a day must never be
 * the only way to reach the board, or a TBD game would be unreachable.
 */
export function slateDays(games: GameSummary[]): SlateDay[] {
  const byKey = new Map<string, SlateDay>();

  for (const game of games) {
    const key = slateDayKey(game.startDate);
    if (key === undefined) continue;

    let day = byKey.get(key);
    if (!day) {
      // Safe: slateDayKey only returns a key when the date parsed.
      const at = new Date(game.startDate as string);
      const parts = DAY_LABEL_FORMAT.formatToParts(at);
      const part = (type: string) =>
        parts.find((p) => p.type === type)?.value ?? "";
      day = {
        key,
        label: `${part("weekday")} ${part("month")} ${part("day")}`,
        weekday: part("weekday"),
        gameIds: [],
      };
      byKey.set(key, day);
    }
    day.gameIds.push(game.gameId);
  }

  // Lexicographic on YYYY-MM-DD is chronological, and does not depend on the
  // caller having ordered the games it passed in.
  return [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key));
}

/**
 * Narrow a list of games to one day, or leave it whole when no day is selected.
 *
 * THE BOARD CANNOT USE THIS AND THAT IS THE POINT. There the day reaches a
 * database predicate as a list of ids, because the row cap makes filtering in
 * the page incorrect rather than merely slow. Analyze Games already holds the
 * whole slate in memory — 99 games is the largest week measured — so the same
 * filter is a `Set` lookup, and writing it here rather than in the page keeps
 * the two rules that matter testable: no day means EVERY game (including the
 * TBD kickoffs that belong to no day at all), and membership is by id, never
 * by recomputing a date.
 */
export function narrowToDay<T extends { gameId: number }>(
  games: T[],
  day: SlateDay | undefined,
): T[] {
  if (!day) return games;
  const ids = new Set(day.gameIds);
  return games.filter((game) => ids.has(game.gameId));
}

/**
 * Resolve a requested day against the days that exist.
 *
 * An unknown day resolves to undefined — meaning "all days" — rather than to an
 * empty board. A stale link (last week's Saturday, carried over by the week
 * strip preserving filters) must not strand a reader on a board with nothing on
 * it and no indication why.
 */
export function findSlateDay(
  days: SlateDay[],
  key: string | undefined,
): SlateDay | undefined {
  if (!key) return undefined;
  return days.find((day) => day.key === key);
}
