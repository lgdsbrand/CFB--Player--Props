"use client";

import { useEffect, useRef } from "react";
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
 * linkable, and correct on a hard refresh. That is unchanged by the `"use
 * client"` below, which buys exactly one thing: scrolling the selected week
 * into view.
 *
 * WHY THAT IS WORTH A CLIENT COMPONENT. The strip renders every week of the
 * season in one scroller, and the browser opens it at scrollLeft 0. Measured
 * on 2025 week 9, the selected card sat 1,066px to the right of a 358px
 * viewport at 390 and a 720px viewport at 768 — so the selector did not show
 * the selection, on either size, and it would fall off a 1440 laptop too by
 * the back half of the season. A week selector that hides which week you are
 * on is worse than one that scrolls.
 *
 * It sets `scrollLeft` on the nav directly rather than calling
 * `scrollIntoView`, which can also scroll ancestors and would yank the page.
 *
 * ONE SEASON AT A TIME, chosen by the pills above the strip. Every projected
 * week used to render in a single scroller, which put the live week in a
 * minority: at the 2026 opening it was 1 card of 17, and by mid-season it would
 * be one of roughly 24 with the archive crowding the thing a reader came for.
 * Prior seasons are kept rather than dropped — they are the visible evidence
 * the model has a record behind it — but they no longer compete with the slate
 * being played. The pills only appear when there is more than one season, so a
 * single-season deployment (the NFL build's first year) shows no dead control.
 */
export function WeekStrip({
  weeks,
  active,
}: {
  weeks: SlateWeek[];
  active: SlateWeek | null;
}) {
  const nav = useRef<HTMLElement>(null);
  const current = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const strip = nav.current;
    const card = current.current;
    if (!strip || !card) return;
    const s = strip.getBoundingClientRect();
    const c = card.getBoundingClientRect();
    strip.scrollLeft += c.left - s.left - (s.width - c.width) / 2;
  }, [active?.season, active?.week]);

  if (weeks.length === 0) return null;

  // Newest first: the season a reader wants is the one being played.
  const seasons = [...new Set(weeks.map((entry) => entry.season))].sort(
    (a, b) => b - a,
  );
  const shownSeason = active?.season ?? seasons[0];
  const shown = weeks.filter((entry) => entry.season === shownSeason);

  return (
    <div className="flex flex-col gap-2">
      {seasons.length > 1 ? (
        <nav aria-label="Select season" className="flex gap-1.5">
          {seasons.map((season) => {
            // Land on that season's newest week, matching where the board
            // opens by default rather than dropping a reader on week 1 of a
            // finished season.
            const landing = weeks.findLast((entry) => entry.season === season);
            if (!landing) return null;
            const isActive = season === shownSeason;
            return (
              <Link
                key={season}
                href={`/?season=${season}&week=${landing.week}`}
                aria-current={isActive ? "true" : undefined}
                className={
                  "rounded-full border px-2.5 py-1 text-[0.6875rem] font-bold tabular-nums transition-colors " +
                  (isActive
                    ? "border-accent-cyan/40 bg-accent-cyan/10 text-accent-cyan"
                    : "border-border-subtle bg-panel text-muted hover:border-border-strong")
                }
              >
                {season}
              </Link>
            );
          })}
        </nav>
      ) : null}

      <nav
        ref={nav}
        aria-label="Select week"
        className="border-border-subtle scroll-fade-x flex gap-2 overflow-x-auto border-b pb-3"
      >
        {shown.map((week) => {
          const isActive =
            active?.season === week.season && active?.week === week.week;
          return (
            <Link
              key={`${week.season}-${week.week}`}
              ref={isActive ? current : undefined}
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
              {/* nowrap: the date range is the card's identity, and letting it
                  break to two lines makes cards different heights down the
                  strip. The card is `shrink-0`, so it widens instead. */}
              <span className="text-ink whitespace-nowrap text-sm font-bold">
                {formatDateRange(week.firstKickoff, week.lastKickoff)}
              </span>
              <span className="text-muted text-[0.6875rem]">
                {week.games} games
              </span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
