import { formatFormMean, type FormSplit } from "@/lib/core/form";

/**
 * Per-game averages, for the markets no book has priced.
 *
 * THE SIBLING OF `SplitGrid`, NOT A REPLACEMENT FOR IT. That one shows hit
 * rates and colours them; this shows averages and deliberately does not.
 *
 * NOTHING HERE IS COLOURED, AND THAT IS THE DESIGN. `SplitGrid` tints a rate
 * green above 65% because a hit rate has a good direction. An average does not:
 * 62.4 receiving yards is high for one player and low for another, and there is
 * no line here to compare it to — that is the whole reason this component
 * exists. Painting it would invent a verdict out of nothing.
 *
 * EVERY AVERAGE CARRIES ITS SAMPLE AND ITS RANGE. Same instinct as the hit-rate
 * grid carrying its denominator: a 60-yard average over two games, from games
 * of 12 and 108, is not a fact about the player. The games count and the
 * low-high sit under the figure in the same typography rather than in a
 * tooltip.
 */
export function FormGrid({
  splits,
  unit,
  emptyLabel = "No completed games yet.",
  note,
}: {
  splits: FormSplit[];
  /** `markets.unit`, e.g. "yds". Appended to the range, not to the headline
   *  figure, which stays the biggest thing in the cell. */
  unit: string | null;
  emptyLabel?: string;
  note?: string;
}) {
  // The note survives an empty grid, as in `SplitGrid`: it is usually the thing
  // that explains the emptiness.
  if (splits.length === 0) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-dim text-xs">{emptyLabel}</p>
        {note ? <p className="text-dim text-[0.625rem]">{note}</p> : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-4">
        {splits.map((split) => (
          <div
            key={split.key}
            title={split.hint}
            className="panel-inset flex flex-col gap-0.5 px-2.5 py-2"
          >
            <span className="label-caption">{split.label}</span>
            {/* `text-ink`, never a semantic colour — see the note above. */}
            <span className="text-ink text-base font-extrabold leading-none tabular-nums">
              {formatFormMean(split.summary.mean)}
            </span>
            <span className="text-dim text-[0.625rem] tabular-nums">
              {split.summary.played} game
              {split.summary.played === 1 ? "" : "s"}
              {split.summary.min !== null && split.summary.max !== null
                ? ` · ${formatFormMean(split.summary.min)}–${formatFormMean(split.summary.max)}${unit ? ` ${unit}` : ""}`
                : ""}
            </span>
          </div>
        ))}
      </div>
      {note ? <p className="text-dim text-[0.625rem]">{note}</p> : null}
    </div>
  );
}
