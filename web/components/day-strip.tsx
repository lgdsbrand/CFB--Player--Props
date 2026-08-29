import Link from "next/link";

import { boardHref, type BoardParams } from "@/lib/core/board-params";
import type { SlateDay } from "@/lib/core/slate-days";

/**
 * The day selector, one level finer than the week strip above it.
 *
 * WHY IT EXISTS. A CFBD week is not a day. 2026 week 1 ran **ten calendar days
 * over six game days**, and opening Saturday's eight games were 402 of its
 * 4,676 board rows while the following Saturday alone was 3,125 — so a reader
 * opening the board was mostly looking at games a week away. The week strip's
 * own note explains why a week was the right primary unit; this is the missing
 * second half of CLAUDE.md §7's date strip, asked for by the client once he had
 * a ten-day week in front of him.
 *
 * NO CLIENT COMPONENT, unlike the week strip. That one scrolls the selection
 * into view because it renders a whole season; a week has at most seven days,
 * which fit without scrolling on a laptop and scroll trivially on a phone. Every
 * control here is a link carrying URL state, so a day is part of a shared
 * address like every other filter.
 *
 * IT RENDERS NOTHING FOR A SINGLE-DAY WEEK. A control offering one choice plus
 * "all" is a control that cannot change anything — the board has learned twice
 * that a widget with nothing behind it is worse than no widget.
 *
 * ALL DAYS IS ALWAYS PRESENT AND IS THE DEFAULT. A game whose kickoff is still
 * TBD belongs to no day (CFBD publishes those early in the week), so it is
 * reachable only from the unfiltered board. Making a day the default would make
 * those games unreachable without the reader knowing they existed.
 */
export function DayStrip({
  days,
  activeDay,
  params,
  basePath,
}: {
  days: SlateDay[];
  activeDay: string | undefined;
  params: BoardParams;
  /**
   * Where a day pill links. The strip sits above the board and above Analyze
   * Games, and each has to keep the reader on the page they are already on —
   * picking a day is narrowing what you are looking at, not switching to a
   * different view of it. Defaults to the board.
   */
  basePath?: string;
}) {
  if (days.length < 2) return null;

  return (
    <nav
      aria-label="Select day"
      className="border-border-subtle scroll-fade-x flex gap-2 overflow-x-auto border-b pb-3"
    >
      <DayPill
        href={boardHref(params, { day: undefined }, basePath)}
        active={activeDay === undefined}
        caption="All days"
        value={`${days.length} days`}
        // NOT a game total. The week card directly above counts games that have
        // PROJECTIONS (`v_slate_weeks`, 98 on 2026 week 1) while the days below
        // count games that EXIST (`v_slate_games`, 99) — both correct, and
        // adjacent they read as an off-by-one bug rather than as two different
        // questions. The per-day counts stay because nobody sums them by eye.
        detail="Every game"
      />

      {days.map((day) => {
        const count = day.gameIds.length;
        return (
          <DayPill
            key={day.key}
            href={boardHref(params, { day: day.key }, basePath)}
            active={activeDay === day.key}
            caption={day.weekday}
            value={day.label.replace(`${day.weekday} `, "")}
            detail={`${count} ${count === 1 ? "game" : "games"}`}
          />
        );
      })}
    </nav>
  );
}

function DayPill({
  href,
  active,
  caption,
  value,
  detail,
}: {
  href: string;
  active: boolean;
  caption: string;
  value: string;
  detail: string;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={
        // `min-w-22` keeps the pills a uniform width so the strip reads as a
        // scale of days rather than as boxes of varying size; `shrink-0` stops
        // a narrow viewport compressing them instead of scrolling.
        "flex min-w-22 shrink-0 flex-col gap-0.5 rounded-xl border px-3 py-1.5 transition-colors " +
        (active
          ? "border-accent-cyan/40 bg-accent-cyan/10"
          : "border-border-subtle bg-panel hover:border-border-strong")
      }
    >
      <span
        className={
          "text-[0.625rem] font-semibold uppercase tracking-label " +
          (active ? "text-accent-cyan" : "text-dim")
        }
      >
        {caption}
      </span>
      {/* nowrap for the same reason the week card does it: the date is the
          pill's identity, and wrapping it makes neighbours different heights. */}
      <span className="text-ink whitespace-nowrap text-sm font-bold">
        {value}
      </span>
      <span className="text-muted text-[0.6875rem]">{detail}</span>
    </Link>
  );
}
