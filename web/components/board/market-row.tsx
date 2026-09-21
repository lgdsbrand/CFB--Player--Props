import { LastFive } from "@/components/board/last-five";
import type { FormSummary } from "@/lib/core/form";
import { ProjectionBar } from "@/components/board/projection-bar";
import { callFor } from "@/lib/core/board-view";
import {
  formatUsageShare,
  type UsageSummary,
} from "@/lib/core/usage-view";
import {
  formatAmericanOdds,
  formatConfidence,
  formatEdge,
  formatLine,
  meetsEdgeThreshold,
} from "@/lib/core/format";
import type { HitRateSummary } from "@/lib/core/hit-rate";
import type { BoardRow, Market } from "@/lib/core/types";

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
 *
 * A THIRD STATE, FOR A MARKET THIS PRODUCT STATES NOTHING ABOUT. First-quarter
 * markets publish no call (`markets.publishes_call`), so the sub-card leads
 * with the BOOK'S LINE where the call would be, and everything below it that
 * derives from the withheld probability — the projected-vs-line bar, the edge —
 * is omitted rather than dimmed. What remains is the line, the book's two-way
 * price and the player's own record against that line, which is exactly the
 * three things the client said he would read (2026-09-17).
 *
 * The omissions are the point. A greyed-out edge still tells a reader an edge
 * was computed and would invite them to wonder what it said.
 */
export function MarketRow({
  row,
  market,
  hitRate,
  form,
  hitRateWindow,
  usage,
  edgeThreshold,
}: {
  row: BoardRow;
  /**
   * The catalogue row, for `publishes_call`. Optional only because the board
   * row carries its own market label and a missing catalogue entry should not
   * blank a card — absent, the sub-card behaves exactly as it always has.
   */
  market: Market | undefined;
  hitRate: HitRateSummary | null;
  /**
   * The same window as raw values, for a market with no posted line. Forwarded
   * straight to `LastFive`, which shows one or the other and never both.
   */
  form: FormSummary | null;
  hitRateWindow: number;
  /** Null where the market has no denominator, or no game in the window had one. */
  usage: UsageSummary | null;
  edgeThreshold: number;
}) {
  const isEdge = meetsEdgeThreshold(row.edge, edgeThreshold);
  // The four states live in the core so the table renders the same ones — the
  // anytime-TD inversion below is too easy to get independently wrong twice.
  const call = callFor(row, market);
  const statesNothing = call.kind === "reference";

  return (
    <div className="panel-inset flex flex-col gap-2.5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-muted flex items-center gap-1.5 text-[0.6875rem] font-bold uppercase tracking-label">
          {row.marketEmoji ? <span aria-hidden>{row.marketEmoji}</span> : null}
          {row.marketLabel ?? row.marketName}
        </span>

        {call.kind === "reference" ? (
          <BookLine line={row.line} unit={market?.unit ?? null} />
        ) : call.kind === "binary" ? (
          <BinaryProbability probability={call.probability} />
        ) : call.kind === "call" ? (
          <span className="flex items-center gap-2">
            <span
              className={
                "pill " +
                (call.side === "over"
                  ? "bg-positive/15 text-positive"
                  : "bg-negative/15 text-negative")
              }
            >
              {call.side}
            </span>
            <span className="gradient-text text-lg font-extrabold leading-none">
              {formatConfidence(call.confidence)}
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
      {row.isBinary || statesNothing ? null : (
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

        {row.edge !== null && !statesNothing ? (
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
        form={form}
        side={row.isBinary || statesNothing ? "over" : row.side}
        window={hitRateWindow}
        verb={row.isBinary ? "scored" : undefined}
        season={row.season}
      />

      {/*
        USAGE SITS UNDER THE LAST-5 ROW, one line, plain.

        It is the only figure on this sub-card that needs neither a line nor a
        call, so it is the one still standing on a market a book has not posted
        — which is most of the week (CLAUDE.md §7). Muted and uncoloured on
        purpose: a high target share is not a good bet, because the book prices
        the role in, and tinting it would turn a description into a tip.
      */}
      {usage ? (
        <p className="text-dim text-[0.625rem]" title={usage.stat.hint}>
          {formatUsageShare(usage.share)} of his team&apos;s {usage.stat.noun}{" "}
          per game over {usage.games} game{usage.games === 1 ? "" : "s"}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The headline for a market that publishes no call: the book's number.
 *
 * It takes the slot the confidence percentage occupies on every other sub-card,
 * and takes it deliberately. The client reads this board line-first — "I'm
 * going to look at books line, their last 5/10 hit rate, what the defense might
 * give up in 1st quarter and then play whatever" — so the line IS the headline
 * here, not a footnote under a number we are not printing.
 *
 * No gradient. The cyan-to-indigo fill marks numbers this product is claiming
 * (CLAUDE.md §7); a book's line is a fact we are relaying, and dressing it in
 * the house accent would read as ours.
 */
function BookLine({ line, unit }: { line: number | null; unit: string | null }) {
  if (line === null) {
    return <span className="pill bg-panel text-muted">No line yet</span>;
  }
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-dim text-[0.625rem] font-semibold uppercase tracking-label">
        Line
      </span>
      <span className="text-lg font-extrabold leading-none tabular-nums">
        {formatLine(line)}
      </span>
      {unit ? (
        <span className="text-dim text-[0.625rem] font-semibold uppercase tracking-label">
          {unit}
        </span>
      ) : null}
    </span>
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
