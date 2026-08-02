import Link from "next/link";

import { formatDateRange } from "@/lib/core/format";
import type { SlateWeek } from "@/lib/core/types";

/**
 * The slate selector that sits above the board.
 *
 * CLAUDE.md §7 specifies a date strip running two days back to two days ahead,
 * matching the client's MLB board. That is built for a daily sport. College
 * football is weekly, and the data bears it out: one week's games run Tuesday
 * evening to Saturday night, so five consecutive dates and one week span almost
 * the same window. Selecting a week and labelling it with the dates it covers
 * gives the same affordance without needing to map a bare date back to the week
 * that every model output is keyed by.
 *
 * Navigation is plain links carrying URL state, not client-side state. The
 * board is server-rendered per week, so a selection is a real URL — shareable,
 * linkable, and correct on a hard refresh.
 */
export function WeekStrip({
  weeks,
  active,
}: {
  weeks: SlateWeek[];
  active: SlateWeek | null;
}) {
  if (weeks.length === 0) return null;

  return (
    <nav
      aria-label="Select week"
      className="border-border-subtle flex gap-2 overflow-x-auto border-b pb-3"
    >
      {weeks.map((week) => {
        const isActive =
          active?.season === week.season && active?.week === week.week;
        return (
          <Link
            key={`${week.season}-${week.week}`}
            href={`/?season=${week.season}&week=${week.week}`}
            aria-current={isActive ? "page" : undefined}
            className={
              "flex min-w-30 shrink-0 flex-col gap-0.5 rounded-xl border px-3 py-2 transition-colors " +
              (isActive
                ? "border-accent-cyan/40 bg-accent-cyan/10"
                : "border-border-subtle bg-panel hover:border-border-strong")
            }
          >
            <span
              className={
                "text-[0.625rem] font-semibold uppercase tracking-label " +
                (isActive ? "text-accent-cyan" : "text-dim")
              }
            >
              {week.season} · Week {week.week}
            </span>
            <span className="text-ink text-sm font-bold">
              {formatDateRange(week.firstKickoff, week.lastKickoff)}
            </span>
            <span className="text-muted text-[0.6875rem]">
              {week.games} games
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
