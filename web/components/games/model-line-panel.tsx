import { formatEdge, formatKickoff, meetsEdgeThreshold } from "@/lib/core/format";
import {
  MODEL_PERIOD_LABELS,
  formatFair,
  marginRangeLabel,
  marketEdge,
  modelFavourite,
  modelSpreadLabel,
  spreadSideLabel,
  totalRangeLabel,
  totalSideLabel,
  type GameProjection,
  type ModelPeriod,
  type PricedMarket,
} from "@/lib/core/game-lines";

/**
 * The game model's own numbers for one game (CLAUDE.md §11).
 *
 * The fair line by period, the win chance, and since 2026-10-06 the full-game
 * EDGE against one book, shown at the user's instruction (§11 records it; it
 * overrides the G4 decision to show the number only). The edges are the same
 * ones the slate's Lines table shows, from the same `game_projections` row, so
 * the two pages cannot disagree. The model's picks are still a private shadow
 * test (migration 0078) and are not read here.
 */

const PERIODS: ModelPeriod[] = ["full", "h1", "q1"];

export function ModelLinePanel({
  projection,
  home,
  away,
  completed,
  edgeThreshold,
}: {
  projection: GameProjection | null;
  home: string;
  away: string;
  completed: boolean;
  edgeThreshold: number;
}) {
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">🧮 Model fair line</h2>
        <p className="text-muted text-xs">
          What the game model thinks this game is worth, from each team&rsquo;s
          opponent-adjusted play this season and last season&rsquo;s rating.
          The range is where 8 in 10 results should land. The edge is the
          model&rsquo;s probability of the side shown minus the book&rsquo;s,
          with the vig removed from the book&rsquo;s two prices.
        </p>
      </div>

      {projection === null ? (
        <p className="text-muted text-xs">
          {completed
            ? "The model did not project this game."
            : "Not projected yet. A week's games are projected once the previous week has been played."}
        </p>
      ) : projection.missingPriorSeason ? (
        // User decision 2026-10-06: the book's lines only (see
        // `GameProjection.missingPriorSeason`). Said in full here; the slate
        // table has room for two words.
        <p className="text-muted text-xs">
          One of these teams is new to FBS this season, so the model has no
          previous season for it and its numbers for this game are not shown.
          The book&rsquo;s lines are on this page as usual.
        </p>
      ) : (
        <>
          {/* Value over its range in one cell: three columns fit a phone,
              five pushed the total off-screen at 390px. */}
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-border-subtle border-b">
                <Th>Period</Th>
                <Th>Fair spread</Th>
                <Th>Fair total</Th>
              </tr>
            </thead>
            <tbody>
              {PERIODS.map((period) => {
                const { margin, total } = projection.periods[period];
                return (
                  <tr
                    key={period}
                    className="border-border-subtle/60 border-b last:border-0"
                  >
                    <td className="label-caption py-2 pr-3 whitespace-nowrap">
                      {MODEL_PERIOD_LABELS[period]}
                    </td>
                    <td className="py-2 pr-3">
                      <Value
                        value={modelSpreadLabel(margin.mean, home, away)}
                        range={marginRangeLabel(margin, home, away)}
                      />
                    </td>
                    <td className="py-2">
                      <Value value={total.mean.toFixed(1)} range={totalRangeLabel(total)} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
            <span className="flex flex-col">
              <span className="label-caption">Win chance</span>
              <span className="text-ink text-sm font-extrabold tabular-nums">
                {(() => {
                  const fav = modelFavourite(projection.pHomeWin, home, away);
                  return `${fav.team} ${formatFair(fav.p)}`;
                })()}
              </span>
            </span>
            <Edge
              label="Spread edge"
              market={projection.spread}
              side={(side, line) => spreadSideLabel(line, side, home, away)}
              edgeThreshold={edgeThreshold}
            />
            <Edge
              label="Total edge"
              market={projection.total}
              side={(side, line) => totalSideLabel(line, side)}
              edgeThreshold={edgeThreshold}
            />
            <span className="text-dim text-[0.6875rem]">
              Updated {formatKickoff(projection.madeAt)}.
              {projection.evidencePhase === "early"
                ? " One side has played two games or fewer this season, so this leans on last season and is less reliable than usual."
                : ""}
            </span>
          </div>
        </>
      )}
    </section>
  );
}

/** "+16.2%" over "TROY -10.0 · Pinnacle", or a dash when no book priced it. */
function Edge({
  label,
  market,
  side,
  edgeThreshold,
}: {
  label: string;
  market: PricedMarket | null;
  side: (side: "first" | "second", line: number) => string;
  edgeThreshold: number;
}) {
  const edge = market ? marketEdge(market) : null;
  return (
    <span className="flex flex-col">
      <span className="label-caption">{label}</span>
      {market && edge ? (
        <>
          <span
            className={
              "font-mono text-sm font-extrabold tabular-nums " +
              (meetsEdgeThreshold(edge.edge, edgeThreshold) ? "text-target" : "text-muted")
            }
          >
            {formatEdge(edge.edge)}
          </span>
          <span className="text-dim text-[0.6875rem] whitespace-nowrap">
            {side(edge.side, market.line)}
            {market.bookName ? ` · ${market.bookName}` : ""}
          </span>
        </>
      ) : (
        <span className="text-dim text-sm">—</span>
      )}
    </span>
  );
}

function Value({ value, range }: { value: string; range: string }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="text-ink font-extrabold whitespace-nowrap tabular-nums">{value}</span>
      <span className="text-dim text-[0.6875rem] tabular-nums">{range}</span>
    </span>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th scope="col" className="label-caption py-2 pr-3 text-left whitespace-nowrap">
      {children}
    </th>
  );
}
