import Link from "next/link";

import { Cell, EdgeCell } from "@/components/games/lines-table";
import { formatDateShort } from "@/lib/core/format";
import {
  formatPoints,
  modelNumber,
  modelSpreadLabel,
  spreadLabel,
  spreadSideLabel,
  tableSideResult,
  totalSideLabel,
  type GameProjection,
  type PricedMarket,
} from "@/lib/core/game-lines";
import { scopedHref } from "@/lib/core/board-params";
import type { GameSummary } from "@/lib/core/types";

/**
 * Games already played this week, at the bottom of the Games page (client,
 * 2026-10-08: "move the games that have played to the bottom ... so we could
 * go back and look"). A button, collapsed, so the slate still leads.
 *
 * Each row is the games table as it stood at kickoff: `run_game_model` never
 * re-projects a started game, so the stored line, model numbers and edge are
 * the last ones shown before the game. Beside them, the final score and
 * whether the side our number pointed to won (`tableSide`), or "Pass" when
 * our number was within a point of the book's, counted in neither tally
 * (client, 2026-10-09). The model's frozen picks, graded, are on the tracker.
 */
export function CompletedGames({
  games,
  projections,
  edgeThreshold,
}: {
  games: GameSummary[];
  projections: Map<number, GameProjection>;
  edgeThreshold: number;
}) {
  if (games.length === 0) return null;
  const tally = { spreads: [0, 0], totals: [0, 0] };
  let passes = 0;
  for (const game of games) {
    const projection = projections.get(game.gameId);
    if (!projection || projection.missingPriorSeason) continue;
    for (const kind of ["spreads", "totals"] as const) {
      const market = kind === "spreads" ? projection.spread : projection.total;
      if (!market) continue;
      const result = tableSideResult(
        market, kind, modelNumber(projection, kind), game.homePoints!, game.awayPoints!,
      );
      if (result === "win") tally[kind][0] += 1;
      if (result === "loss") tally[kind][1] += 1;
      if (result === "pass") passes += 1;
    }
  }

  return (
    <details className="panel group">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 [&::-webkit-details-marker]:hidden">
        <h2 className="section-header">✅ Completed games ({games.length})</h2>
        <span className="text-muted text-xs tabular-nums">
          {`Our side ${tally.spreads[0]}-${tally.spreads[1]} ATS · ${tally.totals[0]}-${tally.totals[1]} O/U` +
            (passes > 0 ? ` · ${passes} pass` : "")}
        </span>
        <span className="text-accent-cyan ml-auto text-xs font-bold uppercase tracking-label">
          <span className="group-open:hidden">Show ▾</span>
          <span className="hidden group-open:inline">Hide ▴</span>
        </span>
      </summary>
      <div className="border-border-subtle overflow-x-auto border-t px-4 pb-3">
        <table className="w-full min-w-240 border-collapse text-sm">
          <thead>
            <tr className="border-border-subtle border-b">
              {["Game", "Final", "Book spread", "Our spread", "Spread edge", "Result", "Book total", "Our total", "Total edge", "Result"].map(
                (label, i) => (
                  <th key={i} scope="col" className="label-caption py-2 pr-3 text-left whitespace-nowrap">
                    {label}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {games.map((game) => (
              <Row
                key={game.gameId}
                game={game}
                projection={projections.get(game.gameId)}
                edgeThreshold={edgeThreshold}
              />
            ))}
          </tbody>
        </table>
        <p className="text-dim mt-2 text-[0.6875rem]">
          Lines, model numbers and edges as they stood at kickoff. Result is whether the side our number
          pointed to won; within a point of the book&rsquo;s line is a pass, counted in neither record. The
          model&rsquo;s locked picks are graded on the{" "}
          <Link href={scopedHref("/tracker", { sport: "cfb" })} className="text-accent-cyan hover:underline">
            tracker
          </Link>
          .
        </p>
      </div>
    </details>
  );
}

function Row({
  game,
  projection,
  edgeThreshold,
}: {
  game: GameSummary;
  projection?: GameProjection;
  edgeThreshold: number;
}) {
  const home = game.homeAbbreviation ?? game.homeSchool;
  const away = game.awayAbbreviation ?? game.awaySchool;
  const model = projection && !projection.missingPriorSeason ? projection : null;
  const spread = model?.spread ?? null;
  const total = model?.total ?? null;
  const result = (market: PricedMarket | null, kind: "spreads" | "totals") =>
    market && model
      ? tableSideResult(market, kind, modelNumber(model, kind), game.homePoints!, game.awayPoints!)
      : null;

  return (
    <tr className="border-border-subtle/60 border-b last:border-0">
      <td className="py-2.5 pr-3">
        <Link href={`/games/${game.gameId}`} className="flex flex-col gap-0.5">
          <span className="text-ink hover:text-accent-cyan text-xs font-bold whitespace-nowrap">
            {away} {game.neutralSite ? "vs" : "@"} {home}
          </span>
          <span className="text-dim text-[0.6875rem]">{formatDateShort(game.startDate)}</span>
        </Link>
      </td>
      <td className="text-ink py-2.5 pr-3 text-xs font-bold whitespace-nowrap tabular-nums">
        {`${away} ${game.awayPoints} · ${home} ${game.homePoints}`}
      </td>
      <td className="py-2.5 pr-3">
        <Cell value={spread ? spreadLabel(spread.line, home, away) : null} note={spread?.bookName} />
      </td>
      <td className="py-2.5 pr-3">
        <Cell value={model ? modelSpreadLabel(model.periods.full.margin.mean, home, away) : null} />
      </td>
      <td className="py-2.5 pr-3">
        <EdgeCell
          market={spread}
          kind="spreads"
          model={model ? modelNumber(model, "spreads") : null}
          label={(side, line) => spreadSideLabel(line, side, home, away)}
          edgeThreshold={edgeThreshold}
        />
      </td>
      <td className="py-2.5 pr-3">
        <ResultBadge result={result(spread, "spreads")} />
      </td>
      <td className="py-2.5 pr-3">
        <Cell value={total ? formatPoints(total.line) : null} note={total?.bookName} />
      </td>
      <td className="py-2.5 pr-3">
        <Cell value={model ? model.periods.full.total.mean.toFixed(1) : null} />
      </td>
      <td className="py-2.5 pr-3">
        <EdgeCell
          market={total}
          kind="totals"
          model={model ? modelNumber(model, "totals") : null}
          label={(side, line) => totalSideLabel(line, side)}
          edgeThreshold={edgeThreshold}
        />
      </td>
      <td className="py-2.5 pr-3">
        <ResultBadge result={result(total, "totals")} />
      </td>
    </tr>
  );
}

function ResultBadge({ result }: { result: "win" | "loss" | "push" | "pass" | null }) {
  if (result === null) return <span className="text-dim text-xs">—</span>;
  if (result === "pass") {
    return (
      <span
        title="Our number was within a point of the book's: no lean, counted in neither record."
        className="border-border-subtle text-dim rounded-full border px-2 py-0.5 text-[0.625rem] font-extrabold uppercase tracking-label"
      >
        Pass
      </span>
    );
  }
  const style =
    result === "win"
      ? "border-positive/40 bg-positive/10 text-positive"
      : result === "loss"
        ? "border-negative/40 bg-negative/10 text-negative"
        : "border-border-strong text-muted";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[0.625rem] font-extrabold uppercase tracking-label ${style}`}>
      {result === "win" ? "✓ Hit" : result === "loss" ? "✗ Miss" : "Push"}
    </span>
  );
}
