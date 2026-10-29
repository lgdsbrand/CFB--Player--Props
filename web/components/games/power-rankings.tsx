import { TeamChip } from "@/components/board/team-chip";
import { formatPower, type PowerRanking } from "@/lib/core/power";

/**
 * The power rankings entering the week on screen (client, 2026-10-29), closed
 * by default: a <details>, so it opens without any script. Ratings are points
 * per game better than an average team on a neutral field, fitted on games
 * before this week only (worker/core/power.py).
 */
export function PowerRankings({ rows, league }: { rows: PowerRanking[]; league: string }) {
  if (rows.length === 0) return null;
  return (
    <details className="panel group p-0">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
        <span className="section-header">📈 Power rankings</span>
        <span className="label-caption text-dim">
          <span className="group-open:hidden">Show ▾</span>
          <span className="hidden group-open:inline">Hide ▴</span>
        </span>
      </summary>
      <div className="border-border-subtle border-t px-4 pt-3 pb-4">
        <div className="max-h-[28rem] overflow-y-auto">
          <table className="w-full text-sm tabular-nums">
            <thead className="bg-panel sticky top-0">
              <tr className="label-caption text-left">
                <th className="py-1.5 pr-2 font-bold">#</th>
                <th className="py-1.5 pr-2 font-bold">Team</th>
                <th className="py-1.5 pr-2 text-right font-bold">Power</th>
                <th className="py-1.5 text-right font-bold">Games</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.teamId} className="border-border-subtle border-t">
                  <td className="text-dim py-1.5 pr-2">{row.rank}</td>
                  <td className="py-1.5 pr-2">
                    <span className="flex min-w-0 items-center gap-2">
                      <TeamChip
                        abbreviation={row.abbreviation}
                        color={row.color}
                        altColor={row.altColor}
                      />
                      <span className="text-ink truncate font-bold">{row.school}</span>
                    </span>
                  </td>
                  <td
                    className={
                      "py-1.5 pr-2 text-right font-extrabold " +
                      (row.rating > 0 ? "text-positive" : row.rating < 0 ? "text-negative" : "text-ink")
                    }
                  >
                    {formatPower(row.rating)}
                  </td>
                  <td className="text-muted py-1.5 text-right">{row.games}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-dim mt-3 text-xs">{footnote(league)}</p>
      </div>
    </details>
  );
}

/**
 * One string, not JSX text around `{league}`: the space after that
 * expression was dropped on render ("an average NFLteam").
 */
function footnote(league: string): string {
  return (
    `Points per game better than an average ${league} team on a neutral field, ` +
    "adjusted for the schedule and fitted on games before this week only. Margins " +
    "count up to four touchdowns; last season counts as three games, so it fades as " +
    "this season fills in. The game score on each card is 60% both teams’ power " +
    "rank and 40% how close the game is."
  );
}
