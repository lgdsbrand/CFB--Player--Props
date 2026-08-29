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
