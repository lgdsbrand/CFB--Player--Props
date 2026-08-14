import Link from "next/link";

import { SheetRow } from "@/components/cheat-sheet/sheet-row";
import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { WeekStrip } from "@/components/week-strip";
import { BOARD_PATH, parseBoardParams, type RawParams } from "@/lib/core/board-params";
import {
  CHEAT_WINDOWS,
  DEFAULT_CHEAT_WINDOW,
  cheatSections,
  emptyReason,
  minDecidedFor,
  type CheatSection,
} from "@/lib/core/cheat-sheet";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount } from "@/lib/core/format";
import { POSITION_GROUPS, type PositionGroup } from "@/lib/core/types";
import { getCheatSheet, getCheatSheetContext } from "@/lib/data/cheat-sheet";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";

/**
 * Cheat Sheets — props whose recent games have already cleared today's line.
 *
 * The client asked for "a 80% & 100% hit rate list of all props that have hit
 * for those percentages". Two lists, exactly that.
 *
 * WHAT IT IS AND IS NOT, stated on the page as well as here. A hit rate is a
 * fact about games ALREADY PLAYED, graded against the line the book is showing
 * now — the `threshold` basis of CLAUDE.md §9.2. It is not a forecast and it is
 * not an edge: whether the price is worth taking is measured against a de-vigged
 * book probability and lives on the board. A cheat sheet that let a reader infer
 * otherwise would be the most misleading page in this product, which is why the
 * model's own call rides on every row and disagreement is labelled.
 *
 * IT NEEDS GAMES BEHIND IT. There is nothing to grade in week 1 — the season has
 * not happened — and the sheet fills out from about week 4. The empty state says
 * which of the three causes applies rather than shrugging.
 */

export default async function CheatSheets({
  searchParams,
}: {
  searchParams: Promise<RawParams>;
}) {
  if (!isSupabaseConfigured()) {
    return (
      <Shell>
        <NotConfigured />
      </Shell>
    );
  }

  const raw = await searchParams;
  // The board's parser, not a second one. Season, week and position mean the
  // same thing here and are validated the same way; a parallel parser is how
  // two surfaces come to disagree about what `position=WR` selects.
  const params = parseBoardParams(raw, { edgesOnlyDefault: false });
  const weeks = await getSlateWeeks();
  const active = findWeek(weeks, params.season, params.week);

  if (!active) {
    return (
      <Shell>
        <Header />
        <div className="panel p-6">
          <h2 className="section-header mb-2">No slate yet</h2>
          <p className="text-muted max-w-prose text-sm">
            No week has model output, so there is nothing to grade.
          </p>
        </div>
      </Shell>
    );
  }

  // `window` is shared with the board's hit-rate control, so an out-of-range
  // value arriving from a hand-edited URL falls back rather than querying for a
  // window the view does not emit.
  const windowSize = CHEAT_WINDOWS.includes(params.hitRateWindow)
    ? params.hitRateWindow
    : DEFAULT_CHEAT_WINDOW;

  const page = await getCheatSheet({
    season: active.season,
    week: active.week,
    windowSize,
    positionGroup: params.position,
  });

  const sections = cheatSections(page.rows);
  const empty = page.rows.length === 0;

  // Only when there is nothing to show. Each of these counts is another full
  // grading pass over the week, and on a populated sheet nothing needs them.
  const context = empty
    ? await getCheatSheetContext(active.season, active.week)
    : null;

  const href = (changes: {
    window?: number;
    position?: PositionGroup | null;
  }) => {
    const search = new URLSearchParams();
    search.set("season", String(active.season));
    search.set("week", String(active.week));
    const nextWindow = changes.window ?? windowSize;
    if (nextWindow !== DEFAULT_CHEAT_WINDOW) {
      search.set("window", String(nextWindow));
    }
    const nextPosition =
      changes.position === undefined ? params.position : changes.position;
    if (nextPosition) search.set("position", nextPosition);
    return `/cheat-sheets?${search.toString()}`;
  };

  return (
    <Shell>
      <Header />

      <WeekStrip weeks={weeks} active={active} basePath="/cheat-sheets" />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex items-center gap-2">
          <span className="label-caption">Window</span>
          {CHEAT_WINDOWS.map((size) => (
            <Pill
              key={size}
              href={href({ window: size })}
              active={size === windowSize}
              label={`L${size}`}
            />
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="label-caption">Position</span>
          <Pill
            href={href({ position: null })}
            active={params.position === undefined}
            label="All"
          />
          {POSITION_GROUPS.map((position) => (
            <Pill
              key={position}
              href={href({ position })}
              active={params.position === position}
              label={position}
            />
          ))}
        </div>
      </div>

      {/*
        THE CAVEAT SITS ABOVE THE LISTS, NOT UNDER THEM. Everything below is a
        percentage next to a player's name, which is the shape of a tout sheet;
        a reader who scrolls to the first entry and stops must already have been
        told what the number is. See `model-is-not-profitable-vs-closing-lines`:
        nothing in this product may imply the edge has been shown to make money.
      */}
      <p className="border-border-subtle bg-panel/60 text-muted rounded-xl border px-3 py-2 text-xs">
        <span className="text-ink font-bold uppercase tracking-label">
          What this measures
        </span>{" "}
        — how often each player has already finished on one side of{" "}
        <strong className="text-ink">the line showing today</strong>, over his
        last {windowSize} games with a box score. It is history, not a
        projection, and a streak is not an edge: whether the price is worth
        taking is the{" "}
        <Link href={BOARD_PATH} className="text-accent-cyan hover:underline">
          board&rsquo;s
        </Link>{" "}
        question. Entries need at least {minDecidedFor(windowSize)} decided games
        — ties push and are left out of the record rather than counted as losses.
      </p>

      {page.truncated ? (
        <p className="border-negative/40 bg-negative/5 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-negative font-bold uppercase tracking-label">
            Partial sheet
          </span>{" "}
          — {formatCount(page.total)} props qualify and{" "}
          {formatCount(page.rows.length)} are shown. Narrow by position to see
          the rest.
        </p>
      ) : null}

      {empty ? (
        <EmptySheet
          reason={emptyReason({
            pricedProps: context?.pricedProps ?? 0,
            playedGames: context?.playedGames ?? 0,
            // Weeks of this season behind the slate on screen.
            weeksPlayed: active.week - 1,
            windowSize,
          })}
          season={active.season}
          week={active.week}
          windowSize={windowSize}
          position={params.position}
          clearedHref={href({ position: null })}
        />
      ) : (
        sections.map((section) => <Section key={section.tier.key} section={section} />)
      )}
    </Shell>
  );
}

function Section({ section }: { section: CheatSection }) {
  return (
    <section className="panel flex flex-col">
      <header className="border-border-subtle flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-4 py-3">
        <h2 className="section-header">
          {section.tier.key === "perfect" ? "🔥 " : "⚡ "}
          {section.tier.title}
        </h2>
        <span
          className={
            "text-sm font-extrabold tabular-nums " +
            (section.rows.length > 0 ? "gradient-text" : "text-dim")
          }
        >
          {formatCount(section.rows.length)}
        </span>
        <span className="text-muted min-w-0 flex-1 text-xs">
          {section.tier.blurb}
        </span>
      </header>

      {section.rows.length === 0 ? (
        <p className="text-dim px-4 py-3 text-xs">
          Nothing on this slate is at{" "}
          {section.tier.min >= 1
            ? "a clean sweep"
            : `${Math.round(section.tier.min * 100)}%`}{" "}
          with enough games behind it.
        </p>
      ) : (
        <div className="flex flex-col">
          {section.rows.map((row) => (
            <SheetRow key={`${row.projectionId}-${row.windowSize}`} row={row} />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * Nothing on the sheet, and WHY — never a bare "no results".
 *
 * The rule from `home-view.ts`: this product has three times shipped a surface
 * that led nowhere and said nothing about it. Two of the three causes here are
 * structural and no filter change will fix them, so telling the reader to widen
 * their search would be advice that cannot work.
 */
function EmptySheet({
  reason,
  season,
  week,
  windowSize,
  position,
  clearedHref,
}: {
  reason: ReturnType<typeof emptyReason>;
  season: number;
  week: number;
  windowSize: number;
  position: PositionGroup | undefined;
  clearedHref: string;
}) {
  if (reason === "too-early") {
    return (
      <div className="panel p-6">
        <h2 className="section-header mb-2">Too early in the season</h2>
        <p className="text-muted max-w-prose text-sm">
          {season} is {week - 1} week{week === 2 ? "" : "s"} old, so no player
          has the {minDecidedFor(windowSize)} decided games an entry needs yet.
          This is not an empty week — it is a week the question cannot be asked
          of. The sheet starts to fill from about week{" "}
          {minDecidedFor(windowSize) + 1}. Until then the model&rsquo;s calls are
          on the{" "}
          <Link href={BOARD_PATH} className="text-accent-cyan hover:underline">
            board
          </Link>
          , and they lean on priors rather than on this season&rsquo;s games by
          design.
        </p>
      </div>
    );
  }

  if (reason === "no-games") {
    return (
      <div className="panel p-6">
        <h2 className="section-header mb-2">No games to grade yet</h2>
        <p className="text-muted max-w-prose text-sm">
          A hit rate needs games already played, and nobody on the {season} week{" "}
          {week} slate has played one this season. The sheet fills in as results
          land — a first entry needs {minDecidedFor(windowSize)} decided games,
          so it starts to be worth reading around week{" "}
          {minDecidedFor(windowSize) + 1}. Until then the model&rsquo;s leans are
          on the{" "}
          <Link href={BOARD_PATH} className="text-accent-cyan hover:underline">
            board
          </Link>
          .
        </p>
      </div>
    );
  }

  if (reason === "no-lines") {
    return (
      <div className="panel p-6">
        <h2 className="section-header mb-2">Nothing priced yet</h2>
        <p className="text-muted max-w-prose text-sm">
          A hit rate is measured against a line, and no prop on this slate
          carries one. College books post player props late — usually Thursday or
          Friday for Saturday games (CLAUDE.md §7) — so this fills in with the
          market rather than on a schedule of ours. The projections are already
          on the{" "}
          <Link href={BOARD_PATH} className="text-accent-cyan hover:underline">
            board
          </Link>
          .
        </p>
      </div>
    );
  }

  return (
    <div className="panel p-6">
      <h2 className="section-header mb-2">Nothing clears the bar</h2>
      <p className="text-muted max-w-prose text-sm">
        No prop{position ? ` at ${position}` : ""} on this slate has hit 80% or
        better over its last {windowSize} games with at least{" "}
        {minDecidedFor(windowSize)} decided. That is an ordinary week, not a
        fault.{" "}
        {position ? (
          <Link href={clearedHref} className="text-accent-cyan hover:underline">
            Try every position
          </Link>
        ) : (
          <>Try the other window.</>
        )}
      </p>
    </div>
  );
}

function Pill({
  href,
  active,
  label,
}: {
  href: string;
  active: boolean;
  label: string;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "true" : undefined}
      className={
        "rounded-full border px-2.5 py-1 text-[0.6875rem] font-bold uppercase tracking-label transition-colors " +
        (active
          ? "border-accent-cyan/40 bg-accent-cyan/10 text-accent-cyan"
          : "border-border-subtle bg-panel text-muted hover:border-border-strong")
      }
    >
      {label}
    </Link>
  );
}

function Header() {
  return (
    <div className="flex flex-col gap-1">
      <span className="label-caption">Legends Sports · College Football</span>
      <h1 className="text-2xl font-extrabold tracking-tight">Cheat Sheets</h1>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader activeHref="/cheat-sheets" />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}
