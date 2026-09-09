import Link from "next/link";

/**
 * One step of a pager — "Prev" / "Next" — as a link or a dead label.
 *
 * Shared by the board and the no-vig page so the two pagers cannot drift into
 * looking like different controls. A disabled step is a `span`, not a styled
 * anchor: an anchor that goes nowhere is still focusable and still announces
 * itself as a link, which is exactly the thing a keyboard reader should not be
 * offered at the end of a list.
 */
export function PageLink({
  href,
  disabled,
  children,
}: {
  href: string;
  disabled: boolean;
  children: React.ReactNode;
}) {
  if (disabled) {
    return (
      <span className="text-dim px-3 py-1.5 text-[0.6875rem] font-bold uppercase tracking-label">
        {children}
      </span>
    );
  }
  return (
    <Link
      href={href}
      className="border-border-subtle text-muted hover:text-ink hover:border-border-strong rounded-full border px-3 py-1.5 text-[0.6875rem] font-bold uppercase tracking-label transition-colors"
    >
      {children}
    </Link>
  );
}
