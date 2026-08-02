import { formatLine } from "@/lib/core/format";
import type { GradedGame } from "@/lib/core/hit-rate";

/**
 * The game log, graded against the line now showing.
 *
 * The chart above it is the same data; this is the version you can read a
 * number off. Both are needed — the chart makes the shape obvious, the table
 * settles "what exactly did he do in week 6".
 *
 * GRADED AGAINST TODAY'S LINE, NOT THAT WEEK'S. That is the `threshold` basis
 * settled in CLAUDE.md §9.2: every past game is measured against the one line
 * on the board now. The alternative — each game against the line it closed at —
 * is truer and needs a paid historical-odds backfill we do not have. The column
 * header says which, because the two answer different questions.
 */
export function GameLogTable({
  games,
  unit,
  rankByGameId,
}: {
  games: GradedGame[];
  unit: string | null;
  /** Opponent rank vs the position AS IT STOOD that week; 1 allows the most. */
  rankByGameId: Map<number, number>;
}) {
  if (games.length === 0) {
    return (
      <p className="text-dim text-xs">
        No completed games this season before this week.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[26rem] border-collapse text-left text-xs">
        <thead>
          <tr className="text-dim [&>th]:px-2 [&>th]:py-1.5 [&>th]:font-semibold [&>th]:uppercase [&>th]:tracking-label [&>th]:text-[0.625rem]">
            <th>Wk</th>
            <th>Opp</th>
            <th className="text-right" title="Opponent rank vs this position entering that week — 1 allows the most">
              Rk
            </th>
            <th className="text-right">{unit ?? "Value"}</th>
            <th className="text-right">vs line</th>
          </tr>
        </thead>
        <tbody>
          {games.map((game) => {
            const rank = rankByGameId.get(game.gameId);
            return (
              <tr
                key={game.gameId}
                className="border-border-subtle border-t [&>td]:px-2 [&>td]:py-1.5"
              >
                <td className="text-muted tabular-nums">{game.week}</td>
                <td>
                  <span className="text-dim mr-1">
                    {game.neutralSite ? "N" : game.isHome ? "vs" : "@"}
                  </span>
                  {game.opponentAbbreviation ?? "—"}
                </td>
                <td className="text-muted text-right tabular-nums">
                  {rank ?? "—"}
                </td>
                <td className="text-right font-semibold tabular-nums">
                  {game.value}
                </td>
                <td className="text-right">
                  <span
                    className={
                      "pill " +
                      (game.hit === null
                        ? "bg-panel text-muted"
                        : game.hit
                          ? "bg-positive/15 text-positive"
                          : "bg-negative/15 text-negative")
                    }
                  >
                    {game.outcome === "push"
                      ? "push"
                      : `${game.outcome} ${formatLine(game.line)}`}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
