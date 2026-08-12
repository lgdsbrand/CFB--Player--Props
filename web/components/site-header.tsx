import Link from "next/link";

/**
 * The app shell's header.
 *
 * Mirrors the house style in CLAUDE.md §7: gradient wordmark, fully-pill status
 * badge, uppercase wide-tracked nav. Every colour comes from a token in
 * globals.css — nothing here holds a hex value, so the reskin to the client's
 * measured palette stays a one-file change.
 */
export function SiteHeader({ activeHref = "/" }: { activeHref?: string }) {
  const links = [
    { href: "/", label: "Board" },
    { href: "/games", label: "Games" },
    { href: "/health", label: "Health" },
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
        <Link href="/" className="flex shrink-0 items-baseline gap-2">
          <span className="gradient-text text-lg font-extrabold tracking-tight">
            CFB PROPS
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
                href={link.href}
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

        <span className="text-dim ml-auto hidden text-[0.625rem] font-semibold uppercase tracking-label sm:inline">
          Legends Sports
        </span>
      </div>
    </header>
  );
}
