import { formatKickoff } from "@/lib/core/format";
import {
  MODEL_PERIOD_LABELS,
  formatFair,
  marginRangeLabel,
  modelFavourite,
  modelSpreadLabel,
  totalRangeLabel,
  type GameProjection,
  type ModelPeriod,
} from "@/lib/core/game-lines";

/**
 * The game model's own numbers for one game (CLAUDE.md §11, G4).
 *
 * A FAIR LINE, NOT A PICK. The G3 backtest found the model less accurate than
 * the closing line, so this panel states what the model thinks the game is
 * worth and never sets it against a book: no OVER/UNDER, no cover call, no
 * "edge". The caption says why, in the reader's terms. The model's picks exist
 * only as a private shadow test (migration 0078) and are not read here.
 */

const PERIODS: ModelPeriod[] = ["full", "h1", "q1"];

export function ModelLinePanel({
  projection,
  home,
  away,
  completed,
}: {
  projection: GameProjection | null;
  home: string;
  away: string;
  completed: boolean;
}) {
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">🧮 Model fair line</h2>
        <p className="text-muted text-xs">
          What the game model thinks this game is worth, from each team&rsquo;s
          opponent-adjusted play this season and last season&rsquo;s rating.{" "}
          <strong className="text-ink">This is not a pick.</strong>
          {/* Explicit: a space opening a line after an element is dropped. */}
          {" "}Tested on 2023 to 2025, the model was about a point less
          accurate than the closing line, so it does not say which side to
          take. The range is where 8 in 10 results should land.
        </p>
      </div>

      {projection === null ? (
        <p className="text-muted text-xs">
          {completed
            ? "The model did not project this game."
            : "Not projected yet. A week's games are projected once the previous week has been played."}
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
