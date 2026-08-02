import { formatHitRate } from "@/lib/core/hit-rate";
import type { Split } from "@/lib/core/splits";

/**
 * A row of hit-rate splits (CLAUDE.md §7).
 *
 * EVERY RATE CARRIES ITS DENOMINATOR. A college season is twelve games, so an
 * away split is five of them and a rank band can be two. "67%" from three games
 * is not a fact about the player, and showing the percentage alone invites
 * reading it as one — so the sample sits under every figure, in the same
 * typography, not hidden in a tooltip.
 */
export function SplitGrid({
  splits,
  emptyLabel = "No games to split.",
  note,
}: {
  splits: Split[];
  emptyLabel?: string;
  note?: string;
}) {
  // The note survives an empty grid on purpose: it is usually the thing that
  // EXPLAINS the emptiness ("3 games set aside as unrated"), so dropping it
  // leaves the reader with a bare "no splits" and no reason.
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
            <span
              className={
                "text-base font-extrabold leading-none tabular-nums " +
                toneFor(split.summary.rate)
              }
            >
              {formatHitRate(split.summary.rate)}
            </span>
            <span className="text-dim text-[0.625rem] tabular-nums">
              {split.summary.hits} of {split.summary.decided}
              {split.summary.pushes > 0
                ? ` · ${split.summary.pushes} push`
                : ""}
            </span>
          </div>
        ))}
      </div>
      {note ? <p className="text-dim text-[0.625rem]">{note}</p> : null}
    </div>
  );
}

/**
 * Colour by rate, with a deliberately wide neutral band.
 *
 * Only rates far from a coin flip get a colour. On samples this small, painting
 * 55% green would dress up noise as a signal.
 */
function toneFor(rate: number | null): string {
  if (rate === null) return "text-muted";
  if (rate >= 0.65) return "text-positive";
  if (rate <= 0.35) return "text-negative";
  return "text-ink";
}
