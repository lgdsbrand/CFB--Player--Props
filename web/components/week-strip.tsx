"use client";

import { useEffect, useRef, useState } from "react";
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
 *
 * AND WITHIN A SEASON, THE WEEK BEING PLAYED PLUS THE ONE AHEAD. Narrowing the
 * seasons left the live season's own weeks still competing with each other: by
 * November the strip is sixteen cards of which fifteen are settled history, and
 * the scroll-into-view below exists precisely because the card a reader wants
 * had been pushed off screen by them.
 *
 * THE REST STAY ONE TAP AWAY RATHER THAN BEING DROPPED. Past weeks are how
 * anyone checks whether the model was right last Saturday, which is the most
 * reasonable question a reader can ask of a props board — a strip that made
 * them unreachable would answer a layout complaint by removing the evidence.
 * So the archive collapses behind a count instead, and expanding restores the
 * previous behaviour exactly, scroll-into-view included.
 */

/**
 * Weeks shown before the archive collapses: the active week and the next.
 *
 * NOT A WINDOW CENTRED ON THE ACTIVE WEEK. Centring would spend one of two
 * slots on a week already played, and the request this implements was for the
 * week being played and the one coming. Looking backwards is what the expander
 * is for, and it is a deliberate act rather than the default view.
 */
const WEEKS_SHOWN_COLLAPSED = 2;
export function WeekStrip({
  weeks,
  active,
}: {
  weeks: SlateWeek[];
  active: SlateWeek | null;
}) {
  const nav = useRef<HTMLElement>(null);
  const current = useRef<HTMLAnchorElement>(null);
  const [expanded, setExpanded] = useState(false);

  // `expanded` is in the dependency list because the collapsed strip renders
  // two cards and needs no scrolling, while the strip revealed by the expander
  // does — and it is revealed at scrollLeft 0, which is the state this effect
  // exists to correct.
  useEffect(() => {
    const strip = nav.current;
    const card = current.current;
    if (!strip || !card) return;
    const s = strip.getBoundingClientRect();
    const c = card.getBoundingClientRect();
    strip.scrollLeft += c.left - s.left - (s.width - c.width) / 2;
  }, [active?.season, active?.week, expanded]);

  if (weeks.length === 0) return null;

  // Newest first: the season a reader wants is the one being played.
  const seasons = [...new Set(weeks.map((entry) => entry.season))].sort(
    (a, b) => b - a,
  );
  const shownSeason = active?.season ?? seasons[0];
  const inSeason = weeks.filter((entry) => entry.season === shownSeason);

  // Slice forward FROM the active week. `findIndex` returning -1 for a season
  // with no active week (the reader switched seasons and landed on its newest)
  // would slice from the end and show nothing, so it falls back to the start.
  const activeIndex = Math.max(
    inSeason.findIndex(
      (entry) => entry.season === active?.season && entry.week === active?.week,
    ),
    0,
  );
  const collapsed = inSeason.slice(
    activeIndex,
    activeIndex + WEEKS_SHOWN_COLLAPSED,
  );
  const hidden = inSeason.length - collapsed.length;
  const shown = expanded ? inSeason : collapsed;

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

        {/*
          Inside the scroller, after the cards, so it sits where the strip used
          to continue rather than floating above it as a separate control. It
          is the last thing in reading order for the same reason.

          A BUTTON, NOT A LINK. Expanding shows more of the same season and
          changes nothing about which week the board is rendering, so it carries
          no URL state — unlike every other control in this component. Rendering
          it as a link would put a week in the address bar that the reader had
          not chosen.
        */}
        {hidden > 0 ? (
          <button
            type="button"
            onClick={() => setExpanded((open) => !open)}
            aria-expanded={expanded}
            className="border-border-subtle bg-panel text-muted hover:border-border-strong hover:text-ink flex shrink-0 items-center rounded-xl border px-3 py-2 text-[0.6875rem] font-bold uppercase tracking-label transition-colors"
          >
            {expanded ? "Show fewer" : `+ ${hidden} more`}
          </button>
        ) : null}
      </nav>
    </div>
  );
}
