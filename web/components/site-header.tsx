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
      <div className="mx-auto flex w-full max-w-7xl items-center gap-6 px-4 py-3 sm:px-6">
        <Link href="/" className="flex items-baseline gap-2">
          <span className="gradient-text text-lg font-extrabold tracking-tight">
            CFB PROPS
          </span>
          <span className="pill bg-accent-cyan/15 text-accent-cyan">Beta</span>
        </Link>

        <nav className="flex items-center gap-1">
          {links.map((link) => {
            const active = link.href === activeHref;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={
                  "rounded-full px-3 py-1.5 text-xs font-bold uppercase tracking-label transition-colors " +
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
