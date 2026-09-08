import Link from "next/link";

import { BOARD_PATH } from "@/lib/core/board-params";
import {
  DEFAULT_SPORT,
  SPORTS,
  SPORT_SHORT,
  type Sport,
} from "@/lib/core/sport";

/**
 * The app shell's header.
 *
 * Mirrors the house style in CLAUDE.md §7: gradient wordmark, fully-pill status
 * badge, uppercase wide-tracked nav. Every colour comes from a token in
 * globals.css — nothing here holds a hex value, so the reskin to the client's
 * measured palette stays a one-file change.
 */
export function SiteHeader({
  activeHref = "/",
  sport = DEFAULT_SPORT,
}: {
  activeHref?: string;
  sport?: Sport;
}) {
  /**
   * Sport rides on every nav link.
   *
   * It is a URL parameter (see `BoardParams.sport`), so a nav link that omits
   * it silently returns an NFL reader to the college board. That is worse than
   * a lost filter: sport decides which rows exist at all, so the symptom is a
   * board full of the wrong league rather than an obviously missing control.
   */
  const withSport = (href: string) =>
    sport === DEFAULT_SPORT ? href : `${href}?sport=${sport}`;
  // `/health` IS DELIBERATELY NOT HERE. It is an operator's deploy proof, not a
  // destination: it names internal tables, prints row counts, echoes raw
  // Postgres error text when a check fails, and reports on the RLS posture by
  // name. None of that is for the audience this nav is built for, and it sat
  // between SHEETS and the client's own brand line. The route still works and
  // is still what `docs/deployment.md` step 7 tells you to open after a deploy —
  // it is unlinked and noindexed, not removed. See `app/health/page.tsx`.
  const links = [
    { href: "/", label: "Home" },
    { href: BOARD_PATH, label: "Props" },
    { href: "/games", label: "Games" },
    { href: "/cheat-sheets", label: "Sheets" },
    { href: "/no-vig", label: "No-Vig" },
  ];

  return (
    <header className="border-border-subtle bg-canvas/80 sticky top-0 z-20 border-b backdrop-blur">
      {/* THE NAV IS ALLOWED TO SCROLL, AND THAT IS NOT DECORATION. Adding a
          third link pushed this row 0.7px past a 390px viewport, which made the
          whole PAGE scroll sideways — on every route, not just the new one, and
          only against production team names. A header that grows by one link
          should not be able to do that again, so the gap tightens on small
          screens and the nav absorbs whatever is left over.

          `min-w-0` is what makes the overflow land here rather than on the
          document: without it a flex child refuses to shrink below its content
          and pushes its parent wide instead. */}
      <div className="mx-auto flex w-full max-w-7xl items-center gap-3 px-4 py-3 sm:gap-6 sm:px-6">
        <Link href={withSport("/")} className="flex shrink-0 items-baseline gap-2">
          <span className="gradient-text text-lg font-extrabold tracking-tight">
            {SPORT_SHORT[sport]} PROPS
          </span>
          {/* Hidden on narrow screens so all three nav links fit without
              clipping. It is a status badge, not navigation: the cost of
              dropping it below 640px is nothing, and the alternative was a
              header where HEALTH sat half off the edge. */}
          <span className="pill bg-accent-cyan/15 text-accent-cyan hidden sm:inline-flex">
            Beta
          </span>
        </Link>

        <nav className="no-scrollbar flex min-w-0 items-center gap-1 overflow-x-auto">
          {links.map((link) => {
            const active = link.href === activeHref;
            return (
              <Link
                key={link.href}
                href={withSport(link.href)}
                aria-current={active ? "page" : undefined}
                className={
                  "shrink-0 rounded-full px-2.5 py-1.5 text-xs font-bold uppercase tracking-label transition-colors sm:px-3 " +
                  (active
                    ? "bg-panel text-ink"
                    : "text-muted hover:text-ink hover:bg-panel/60")
                }
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        {/* THE SPORT TOGGLE. `ml-auto` puts it at the right edge, and it is
            allowed to be the thing that survives a narrow viewport while the
            brand line is not: switching league is navigation, and the header
            comment above records what happens when this row grows past a 390px
            screen. Rendered as links, not a control, so it works without
            JavaScript and so each option is a shareable URL. */}
        <div className="ml-auto flex shrink-0 items-center gap-0.5 rounded-full bg-panel/60 p-0.5">
          {SPORTS.map((option) => {
            const active = option === sport;
            const href =
              option === DEFAULT_SPORT ? activeHref : `${activeHref}?sport=${option}`;
            return (
              <Link
                key={option}
                href={href}
                aria-current={active ? "true" : undefined}
                className={
                  "rounded-full px-2 py-1 text-[0.625rem] font-bold uppercase tracking-label transition-colors sm:px-2.5 " +
                  (active
                    ? "bg-accent-cyan/15 text-accent-cyan"
                    : "text-muted hover:text-ink")
                }
              >
                {SPORT_SHORT[option]}
              </Link>
            );
          })}
        </div>

        <span className="text-dim hidden text-[0.625rem] font-semibold uppercase tracking-label lg:inline">
          Legends Sports
        </span>
      </div>
    </header>
  );
}
