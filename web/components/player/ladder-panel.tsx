import { LADDER_CAVEAT, ladderView } from "@/lib/core/ladder-view";
import { formatConfidence, formatLine } from "@/lib/core/format";
import type { LadderRung } from "@/lib/core/types";

/**
 * The alternate-line ladder.
 *
 * The client's ask, verbatim: "RB line 60, model says 90." The card above answers
 * one probability against one posted line; this answers the question he was
 * actually asking, which is how far the line can be pushed before the model stops
 * agreeing.
 *
 * SECONDARY DETAIL, and placed with the projected range for that reason
 * (CLAUDE.md §1). The headline claim on this page is still the OVER/UNDER call and
 * its confidence.
 *
 * ONE QUANTITY DRIVES EVERY ROW: the chance of going over. The first build showed
 * the OVER/UNDER pill and the confidence on the favoured side, as the board does,
 * with the bar drawn as the over share — so a rung deep in under territory had a
 * nearly empty bar sitting beside the number 87%. Two directions for one fact.
 * Confidence-on-the-called-side is right for a card making ONE claim; a ladder is
 * a single monotone quantity read down the column, and it has to be drawn as one.
 * The side flip is marked where it happens instead of restated on every row.
 *
 * THE BOOK'S LINE USUALLY DOES NOT LAND ON A RUNG. Spacing widens to cover a broad
 * distribution — passing yards routinely step to 75 against a nominal 25 — so the
 * marker sits on the first rung at or above the posted line and says "below this
 * rung". A design that looked for equality would mark nothing on almost every row.
 *
 * NO EDGE IS SHOWN AGAINST A RUNG. Books post one line, so a rung has no market to
 * disagree with, and quoting an edge against a line nobody offered would be
 * inventing one. The edge lives on the card, on the posted line.
 */
export function LadderPanel({
  ladder,
  line,
  median,
  unit,
}: {
  // Parsed rungs, not raw jsonb. Typed, so handing this the wrong shape is a
  // compile error rather than a panel that silently renders nothing — which is
  // exactly what an `unknown` here cost once.
  ladder: LadderRung[] | null;
  line: number | null;
  median: number | null;
  unit: string | null;
}) {
  const view = ladderView(ladder, line, median);
  if (!view) return null;

  // Where the model stops favouring the over. Marked once, on the first rung it
  // no longer clears, rather than repeated as a pill on every row.
  const flipIndex = view.rungs.findIndex((rung) => rung.probOver < 0.5);

  return (
    <section className="panel flex flex-col gap-2 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        {/* The caveat is a tooltip on the heading, and the (?) is what tells a
            reader there is one — a title on bare text is undiscoverable. */}
        <h2 className="section-header flex items-center gap-1.5">
          Alternate lines
          <span
            title={LADDER_CAVEAT}
            aria-label={LADDER_CAVEAT}
            className="text-dim border-border-subtle flex h-3.5 w-3.5 cursor-help
                       items-center justify-center rounded-full border
                       text-[0.5rem] font-bold normal-case tracking-normal"
          >
            ?
          </span>
        </h2>
        <span className="text-dim text-[0.625rem]">
          model probability, no book price
        </span>
      </div>

      <div className="flex items-baseline justify-between gap-2">
        <span className="label-caption">
          Line{unit ? ` (${unit})` : ""}
        </span>
        <span className="label-caption">Chance over</span>
      </div>

      <ol className="flex flex-col gap-1">
        {view.rungs.map((rung, index) => {
          const isBook = view.bookIndex === index;
          const isMedian = view.medianIndex === index;
          const isFlip = flipIndex === index && index > 0;
          const note = [
            isBook
              ? `book line ${formatLine(line as number)}${
                  view.bookLineOnRung ? " — this rung" : " sits below this rung"
                }`
              : null,
            isMedian ? "closest rung to the projected median" : null,
            isFlip ? "the model stops favouring the over here" : null,
          ].filter(Boolean);

          return (
            <li
              key={rung.line}
              className={
                "panel-inset grid grid-cols-[3.5rem_1fr_2.5rem] items-center " +
                "gap-2 px-2.5 py-1.5 " +
                (isBook ? "ring-accent-cyan/40 ring-1 ring-inset" : "")
              }
            >
              <span className="text-sm font-bold tabular-nums">
                {formatLine(rung.line)}
              </span>

              {/* Bar and number are the same quantity, so they can never point
                  opposite ways. Green while the model favours the over, red once
                  it does not — the flip is legible from the colour running down
                  the column without a pill on every row. */}
              <span className="bg-panel relative h-1.5 w-full overflow-hidden rounded-full">
                <span
                  aria-hidden
                  className={
                    "absolute inset-y-0 left-0 rounded-full " +
                    (rung.probOver >= 0.5 ? "bg-positive/70" : "bg-negative/70")
                  }
                  style={{ width: `${Math.max(rung.probOver * 100, 1.5)}%` }}
                />
              </span>

              <span
                className={
                  "text-right text-sm font-extrabold tabular-nums " +
                  (rung.probOver >= 0.5 ? "text-positive" : "text-negative")
                }
              >
                {formatConfidence(rung.probOver)}
              </span>

              {note.length > 0 ? (
                <span className="text-dim col-span-3 text-[0.5625rem]">
                  {note.join(" · ")}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>

      <p className="text-dim text-[0.625rem]">
        Every figure is the chance of going OVER that line, so the column falls as
        the line rises.{" "}
        {line === null
          ? "No book line is posted yet, so there is nothing here to compare against — this is the model's view on its own."
          : view.bookIndex === null
            ? `The posted line of ${formatLine(line)} sits above every rung, which are drawn from the middle of the projected distribution.`
            : "Rungs sit on this market's own grid and cover the middle of the projected distribution, so they widen for a player the model is less certain about."}{" "}
        Each is the model&rsquo;s read at that line, not a recommendation — see the
        note on the heading.
      </p>
    </section>
  );
}
