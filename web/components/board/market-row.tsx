import { LastFive } from "@/components/board/last-five";
import { ProjectionBar } from "@/components/board/projection-bar";
import {
  formatAmericanOdds,
  formatConfidence,
  formatEdge,
  meetsEdgeThreshold,
} from "@/lib/core/format";
import type { HitRateSummary } from "@/lib/core/hit-rate";
import type { BoardRow } from "@/lib/core/types";

/**
 * One market's sub-card inside a player card.
 *
 * THE OVER/UNDER CALL AND THE CONFIDENCE ARE THE HEADLINE (CLAUDE.md §1). Both
 * are derived from the projected distribution — the call is the side holding
 * the majority of the mass, the confidence is the mass past the line — and the
 * projection itself sits below as supporting detail. That inversion relative to
 * the client's pitcher card is the client's own stated direction for these
 * models.
 *
 * TWO STATES, AND THE SECOND IS THE COMMON ONE RIGHT NOW. With a line, the card
 * shows the call. Without one it shows the model's lean as a range and says so
 * plainly. College books post props on Thursday or Friday for Saturday games,
 * so for most of a live week most yardage markets have no line — that is the
 * behaviour CLAUDE.md §7 requires, not a degraded state to apologise for.
 */
export function MarketRow({
  row,
  hitRate,
  hitRateWindow,
  edgeThreshold,
}: {
  row: BoardRow;
  hitRate: HitRateSummary | null;
  hitRateWindow: number;
  edgeThreshold: number;
}) {
  const isEdge = meetsEdgeThreshold(row.edge, edgeThreshold);

  return (
    <div className="panel-inset flex flex-col gap-2.5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-muted flex items-center gap-1.5 text-[0.6875rem] font-bold uppercase tracking-label">
          {row.marketEmoji ? <span aria-hidden>{row.marketEmoji}</span> : null}
          {row.marketLabel ?? row.marketName}
        </span>

        {row.isBinary ? (
          <BinaryProbability probability={row.modelProbOver} />
        ) : row.hasCall && row.side && row.confidence !== null ? (
          <span className="flex items-center gap-2">
            <span
              className={
                "pill " +
                (row.side === "over"
                  ? "bg-positive/15 text-positive"
                  : "bg-negative/15 text-negative")
              }
            >
              {row.side}
            </span>
            <span className="gradient-text text-lg font-extrabold leading-none">
              {formatConfidence(row.confidence)}
            </span>
          </span>
        ) : (
          <span className="pill bg-panel text-muted">Lean · no line</span>
        )}
      </div>

      {/*
        A binary market has no meaningful projected RANGE — the outcome is yes
        or no, so p10 and p90 collapse onto the same point and the bar would
        render as "unavailable" on every card. `markets.is_binary` exists as
        exactly this display hint.
      */}
      {row.isBinary ? null : (
        <ProjectionBar
          median={row.projectedMedian}
          p10={row.projectedP10}
          p90={row.projectedP90}
          line={row.line}
          side={row.side}
        />
      )}

      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="text-dim text-[0.6875rem]">
          {row.hasBookLine && row.sportsbookName ? (
            <>
              <span className="text-muted font-semibold">
                {row.sportsbookName}
              </span>{" "}
              <span className="font-mono tabular-nums">
                {formatAmericanOdds(row.overPrice)} /{" "}
                {formatAmericanOdds(row.underPrice)}
              </span>
            </>
          ) : (
            "No book line yet"
          )}
        </span>

        {row.edge !== null ? (
          <span
            className={
              "font-mono text-[0.6875rem] font-bold tabular-nums " +
              (isEdge ? "text-target" : "text-muted")
            }
          >
            {formatEdge(row.edge)} edge
          </span>
        ) : null}
      </div>

      <LastFive
        summary={hitRate}
        side={row.isBinary ? "over" : row.side}
        window={hitRateWindow}
        verb={row.isBinary ? "scored" : undefined}
      />
    </div>
  );
}

/**
 * A binary market's headline: the probability the player DOES it.
 *
 * CLAUDE.md §1 is explicit that touchdowns are "expressed as an anytime-scorer
 * probability, never a projected count" — and §6 says the model outputs a
 * single probability for this market.
 *
 * Rendering the called side here would invert the number on most cards. Most
 * players do not score, so the call is UNDER and the confidence is the chance
 * they FAIL to score: a receiver with a 10% scoring probability would show
 * "under 90%", which is true, useless, and reads as a strong pick. The mass
 * past the line is `model_prob_over` — P(more than 0.5 touchdowns) — which is
 * precisely the anytime-scorer probability.
 */
function BinaryProbability({ probability }: { probability: number | null }) {
  if (probability === null) {
    return <span className="pill bg-panel text-muted">No projection</span>;
  }
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="gradient-text text-lg font-extrabold leading-none">
        {formatConfidence(probability)}
      </span>
      <span className="text-dim text-[0.625rem] font-semibold uppercase tracking-label">
        to score
      </span>
    </span>
  );
}
