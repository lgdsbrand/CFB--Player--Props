import type { PlayerGameLogRow, PositionGroup } from "@/lib/core/types";
import {
  formatUsageShare,
  summariseUsage,
  usageStatsFor,
} from "@/lib/core/usage-view";

/**
 * How much of his own offence this player is, over the same windows as the hit
 * rate (migration 0071).
 *
 * IT DOES NOT NEED A LINE, which is what separates it from every other panel on
 * this page. The hit-rate splits and the chart go empty until a book posts,
 * because there is nothing to grade against; usage is a fact about the games
 * themselves, so it is at its most useful exactly when the props are not up yet
 * — which is most of the week (CLAUDE.md §7).
 *
 * EVERY FIGURE CARRIES ITS SAMPLE, because the shares behind it can be absent
 * one game at a time: college target attribution is withheld on about one
 * team-game in seven, and college has no snap source at all. A five-game window
 * resting on three games says so under the number.
 */
export function UsagePanel({
  games,
  position,
  windows,
  season,
}: {
  games: PlayerGameLogRow[];
  position: PositionGroup;
  windows: number[];
  season: number;
}) {
  const stats = usageStatsFor(position);
  const rows = stats.map((stat) => ({
    stat,
    cells: windows.map((window) => ({
      window,
      summary: summariseUsage(games, stat, window),
    })),
  }));

  // A row with nothing in any window is a share this sport does not have —
  // college snaps — and is dropped rather than printed as a line of dashes.
  const shown = rows.filter((row) =>
    row.cells.some((cell) => cell.summary !== null),
  );

  if (games.length === 0) {
    return (
      <p className="text-dim text-xs">
        No completed games this season to measure usage over.
      </p>
    );
  }

  if (shown.length === 0) {
    return (
      <p className="text-dim text-xs">
        No usage share on record for these games.{" "}
        {position === "QB"
          ? "Snap counts are NFL-only, and college has no source for them."
          : "Target share is withheld where a box score's target attribution is incomplete, which is common in college."}
      </p>
    );
  }

  const longest = Math.max(...windows);
  const borrowed = games
    .slice(0, longest)
    .filter((game) => game.season !== season).length;
  const dropped = rows.length - shown.length;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[18rem] border-collapse text-left">
          <thead>
            <tr>
              <th className="label-caption pb-1 pr-3 font-normal">Share</th>
              {windows.map((window) => (
                <th
                  key={window}
                  className="label-caption pb-1 pr-3 text-right font-normal"
                >
                  L{window}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map(({ stat, cells }) => (
              <tr key={stat.kind} title={stat.hint}>
                <td className="text-muted py-1 pr-3 text-xs">{stat.label}</td>
                {cells.map((cell) => (
                  <td
                    key={cell.window}
                    className="py-1.5 pr-3 text-right align-top tabular-nums"
                  >
                    {/*
                      THE SAMPLE SITS UNDER THE FIGURE, not beside it — the same
                      treatment `SplitGrid` gives a hit rate, and for the same
                      reason. Beside it, the denominator pushes the percentage
                      off its own column heading and reads as part of the number.
                    */}
                    <span className="block text-base font-extrabold leading-none">
                      {formatUsageShare(cell.summary?.share ?? null)}
                    </span>
                    <span className="text-dim block text-[0.625rem] leading-tight">
                      {cell.summary === null
                        ? "no data"
                        : `${cell.summary.games} gm`}
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-dim text-[0.625rem]">
        A share of his team&apos;s total in each game, averaged over the window —
        not a share of the window&apos;s totals.
        {dropped > 0
          ? " Shares with no data for these games are not listed."
          : ""}
        {borrowed > 0
          ? ` ${borrowed} of these game${borrowed === 1 ? " is" : "s are"} from ${season - 1}, when his role may have been different.`
          : ""}
      </p>
    </div>
  );
}
