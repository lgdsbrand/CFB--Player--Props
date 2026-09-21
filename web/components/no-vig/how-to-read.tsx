import Link from "next/link";

import { NO_VIG_COLUMNS } from "@/components/no-vig/columns";

/**
 * "How to read this" for the no-vig page — the client's ask, 2026-09-21:
 * "is there anyway we could put like a expandable box or question mark people
 * could hover over that would explain what to look for by chance?"
 *
 * TWO AFFORDANCES, NOT ONE, AND THAT IS DELIBERATE. He asked for either an
 * expandable box or a hover; this ships both because they fail on different
 * devices. A `title` hover does nothing at all on a touch screen — there is no
 * pointer to rest — so a phone reader given only tooltips gets no explanation
 * whatsoever. The expandable box is the one that always works, and the column
 * hovers are the shortcut for a reader already looking at the header.
 *
 * Both are driven by `NO_VIG_COLUMNS` (`./columns.ts`) so the two cannot drift
 * apart. A glossary that disagrees with its own tooltips is worse than having
 * neither of them.
 *
 * NO JAVASCRIPT. `<details>` is native, so this stays a server component and
 * costs the page nothing. An `open` prop with React state would have made the
 * whole no-vig route a client component to hold one boolean.
 *
 * WHAT THIS DOES NOT DO: it never tells the reader to place a bet. Every
 * sentence here describes what a number IS, or what it lets you compare. The
 * page carries no model output at all (see `app/no-vig/page.tsx`), so the one
 * thing this explainer must not do is smuggle a recommendation into a page
 * whose whole value is that it makes no claims — `model-is-not-profitable-vs-
 * closing-lines` is why that line matters here.
 */

/**
 * The expandable explainer, shown above the table.
 *
 * THE COLLAPSED STATE STILL SAYS WHAT THE PAGE IS. The paragraph this replaced
 * defined hold and fair and said the numbers are the market's and not ours, and
 * every one of those sentences is in the `<summary>` — so the reader who never
 * expands anything is no worse off than before. Putting "this is not a
 * recommendation" behind a click would have been a regression dressed up as a
 * feature. Only the inline board link moved inside, and the site header carries
 * that link on every page.
 */
export function HowToReadNoVig({ boardLink }: { boardLink: string }) {
  return (
    <details className="group border-border-subtle bg-panel/60 rounded-xl border">
      <summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2 text-xs [&::-webkit-details-marker]:hidden">
        {/* The question mark he asked for. `aria-hidden` because the summary's
            own text already says what expanding does — a screen reader
            announcing "question mark" before it adds nothing. */}
        <span
          aria-hidden
          className="border-border-subtle text-muted group-hover:text-accent-cyan group-hover:border-accent-cyan mt-px flex size-4 shrink-0 items-center justify-center rounded-full border text-[0.625rem] font-bold transition-colors"
        >
          ?
        </span>
        <span className="text-muted">
          <span className="text-ink font-bold uppercase tracking-label">
            What this shows
          </span>{" "}
          {/* `{" "}` after the closing tag, not a plain space. A space that
              follows an element OPENING a line is dropped by the build and only
              the rendered HTML shows it — this page shipped "fairis the price"
              once already. */}
          — every two-way price on the slate with the book&rsquo;s margin
          removed. <strong className="text-ink">Hold</strong> is what the book
          keeps; <strong className="text-ink">fair</strong>{" "}
          is the price the same probability would carry at no margin. This is
          the market&rsquo;s number, not ours.{" "}
          <span className="text-accent-cyan group-open:hidden font-semibold">
            How to read it →
          </span>
          <span className="text-accent-cyan hidden font-semibold group-open:inline">
            Hide ↑
          </span>
        </span>
      </summary>

      <div className="border-border-subtle/60 space-y-3 border-t px-3 py-3 text-xs">
        <p className="text-muted max-w-prose">
          Every number here is arithmetic on a price a sportsbook actually
          posted. Nothing on this page reads our projections, so it is true
          whether or not the model is any good. For what the model thinks, see
          the{" "}
          <Link href={boardLink} className="text-accent-cyan hover:underline">
            board
          </Link>
          .
        </p>

        <div>
          <h3 className="label-caption mb-1.5">What to look for</h3>
          <ol className="text-muted max-w-prose list-decimal space-y-1.5 pl-4">
            <li>
              <strong className="text-ink">How keen the price is.</strong> Hold
              is what the book keeps. The same prop at 4.8% at one book and 7.9%
              at another is the same bet at two different costs, and the colour
              on that column tells you which end you are at.
            </li>
            <li>
              <strong className="text-ink">
                A better price on the same number.
              </strong>{" "}
              Where two or more books post the same line, the best price on each
              side is starred, and <strong className="text-ink">vs Mkt</strong>{" "}
              says how far this book sits from the others there. A book several
              points out on the over is pricing that side more generously than
              its peers.
            </li>
          </ol>
        </div>

        {/* NO "why is anytime touchdown missing" SECTION HERE, though it is the
            obvious question to answer in a box like this. The page already says
            it once at the bottom, in a note whose own comment says "said once,
            plainly". Two statements of the same fact on one page is how they
            start to disagree. */}

        <div>
          <h3 className="label-caption mb-1.5">The columns</h3>
          <dl className="grid gap-x-4 gap-y-1.5 sm:grid-cols-[auto_1fr]">
            {NO_VIG_COLUMNS.map((column) => (
              <div key={column.label} className="sm:contents">
                <dt className="text-ink font-bold uppercase tracking-label text-[0.625rem] sm:pt-0.5">
                  {column.label}
                </dt>
                <dd className="text-muted max-w-prose">{column.help}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </details>
  );
}
