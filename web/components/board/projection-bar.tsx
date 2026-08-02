import { barWindow, positionInRange } from "@/lib/core/board-view";
import { formatLine } from "@/lib/core/format";

/**
 * The projected range with the line marked on it.
 *
 * THIS IS SECONDARY DETAIL AND MUST STAY THAT WAY (CLAUDE.md §1). The client's
 * pitcher card shows a raw projection next to the line, which can read as
 * plainly wrong in individual cases even when the probability behind it is
 * sound — "Proj 1.5 · Line 4.5" invites an argument about the projection rather
 * than trust in the call. Our card leads with the OVER/UNDER pill and the
 * confidence percentage; this bar sits underneath, smaller, as supporting
 * context.
 *
 * It shows a RANGE rather than a point for the same reason. p10 to p90 is what
 * the model actually believes, and drawing the median alone would imply a
 * precision the distribution does not have — especially early in a season, when
 * `prior_weight` is high and the range is genuinely wide.
 */
export function ProjectionBar({
  median,
  p10,
  p90,
  line,
  side,
}: {
  median: number | null;
  p10: number | null;
  p90: number | null;
  line: number | null;
  side: "over" | "under" | null;
}) {
  const window = barWindow(p10, p90, line);
  if (!window || median === null) {
    return (
      <div className="text-dim text-[0.6875rem]">Projected range unavailable</div>
    );
  }

  const pct = (value: number | null) => {
    const fraction = positionInRange(value, window.low, window.high);
    return fraction === null ? null : fraction * 100;
  };

  const rangeStart = pct(p10) ?? 0;
  const rangeEnd = pct(p90) ?? 100;
  const medianAt = pct(median) ?? 50;
  const lineAt = pct(line);

  return (
    <div className="flex flex-col gap-1">
      <div className="bg-panel-inset relative h-2 w-full overflow-hidden rounded-full">
        {/* p10–p90: where the outcome most likely falls. */}
        <span
          aria-hidden
          className="absolute inset-y-0 rounded-full bg-gradient-to-r from-accent-cyan/50 to-accent-indigo/50"
          style={{
            left: `${rangeStart}%`,
            width: `${Math.max(rangeEnd - rangeStart, 1.5)}%`,
          }}
        />
        {/* The median, as a tick rather than a headline number. */}
        <span
          aria-hidden
          className="bg-ink absolute inset-y-0 w-0.5"
          style={{ left: `${medianAt}%` }}
        />
        {/* The line. Coloured by the called side so the bar and the pill agree. */}
        {lineAt !== null ? (
          <span
            aria-hidden
            className={
              "absolute -inset-y-0.5 w-0.5 " +
              (side === "under" ? "bg-negative" : "bg-positive")
            }
            style={{ left: `${lineAt}%` }}
          />
        ) : null}
      </div>

      <div className="text-dim flex justify-between text-[0.625rem] tabular-nums">
        <span>{formatLine(p10 ?? 0)}</span>
        <span className="text-muted">
          proj {formatLine(median)}
          {line !== null ? (
            <>
              {" · "}
              <span className={side === "under" ? "text-negative" : "text-positive"}>
                line {formatLine(line)}
              </span>
            </>
          ) : null}
        </span>
        <span>{formatLine(p90 ?? 0)}</span>
      </div>
    </div>
  );
}
