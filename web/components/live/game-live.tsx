"use client";

import { liveStateLabel, periodLabel, type LiveScore } from "@/lib/core/live";
import { useLiveScores } from "@/components/live/use-live-scores";

/**
 * One game's live state in its page header (migration 0088): score, clock,
 * down and distance, the last play and the score by quarter, refreshed every
 * minute from the browser. Nothing before kickoff, and nothing once `games`
 * holds the final: the header prints that score of record itself.
 */
export function GameLive({
  gameId,
  startDate,
  home,
  away,
  initial,
}: {
  gameId: number;
  startDate: string | null;
  home: string;
  away: string;
  initial: LiveScore | null;
}) {
  const [score] = useLiveScores([{ gameId, startDate }], initial ? [initial] : []);
  if (!score || score.status === "scheduled") return null;
  const live = score.status === "in_progress";
  const periods = Math.max(score.homeLineScores?.length ?? 0, score.awayLineScores?.length ?? 0);

  return (
    <div className="bg-panel-inset flex flex-col gap-2 rounded-xl p-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className={"pill " + (live ? "bg-negative/15 text-negative" : "bg-panel text-muted")}>
          {live ? "● Live" : "Final"}
        </span>
        <span className="text-ink text-lg font-extrabold tabular-nums">
          {`${away} ${score.awayPoints ?? "—"} · ${home} ${score.homePoints ?? "—"}`}
        </span>
        {live ? (
          <span className="text-negative text-sm font-bold">{liveStateLabel(score)}</span>
        ) : null}
        {live && score.possession ? (
          <span className="text-muted text-xs">
            {`${score.possession === "home" ? home : away} ball`}
            {score.situation ? ` · ${score.situation}` : ""}
          </span>
        ) : null}
      </div>
      {score.lastPlay && live ? (
        <p className="text-muted text-xs">{`Last play: ${score.lastPlay}`}</p>
      ) : null}
      {periods > 0 ? (
        <table className="w-fit text-xs tabular-nums">
          <thead>
            <tr className="text-dim">
              <th className="pr-3 text-left font-normal" />
              {Array.from({ length: periods }, (_, i) => (
                <th key={i} className="label-caption w-9 text-right font-normal">
                  {periodLabel(i + 1)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[
              [away, score.awayLineScores],
              [home, score.homeLineScores],
            ].map(([label, lines]) => (
              <tr key={label as string}>
                <td className="text-ink pr-3 font-bold">{label as string}</td>
                {Array.from({ length: periods }, (_, i) => (
                  <td key={i} className="text-muted w-9 text-right">
                    {(lines as number[] | null)?.[i] ?? "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <span className="text-dim text-[0.6875rem]">
        From CollegeFootballData&rsquo;s scoreboard, refreshed every minute.
      </span>
    </div>
  );
}
