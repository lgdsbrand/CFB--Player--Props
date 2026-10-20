"use client";

import Link from "next/link";

import { TeamChip } from "@/components/board/team-chip";
import { liveStateLabel, orderLive, type LiveScore } from "@/lib/core/live";
import { winLabel, type LiveProjection } from "@/lib/core/live-model";
import { useLiveProjections } from "@/components/live/use-live-model";
import { useLiveScores } from "@/components/live/use-live-scores";

export interface LiveGame {
  gameId: number;
  startDate: string | null;
  home: { abbreviation: string | null; school: string; color: string | null; altColor: string | null };
  away: { abbreviation: string | null; school: string; color: string | null; altColor: string | null };
}

/**
 * Live scores across the slate on screen (migration 0088), refreshed every
 * minute from the browser. Renders nothing until a game on the page has a
 * live or final row, so a Monday page carries no empty "Live" box.
 *
 * A game in progress also shows the live model's win % (phase L3), the
 * favoured side and its chance, from the same minute-by-minute refresh.
 */
export function LiveStrip({
  games,
  initial,
  initialProjections = [],
}: {
  games: LiveGame[];
  initial: LiveScore[];
  initialProjections?: LiveProjection[];
}) {
  const scores = useLiveScores(games, initial);
  const { projections } = useLiveProjections(games, initialProjections);
  const model = new Map(projections.map((p) => [p.gameId, p]));
  const byId = new Map(games.map((g) => [g.gameId, g]));
  const shown = orderLive(scores.filter((s) => s.status !== "scheduled" && byId.has(s.gameId)));
  if (shown.length === 0) return null;
  const live = shown.filter((s) => s.status === "in_progress").length;

  return (
    <section className="panel flex flex-col gap-2 p-3" aria-label="Live scores">
      <div className="flex items-center gap-2">
        {live > 0 ? (
          <span className="pill bg-negative/15 text-negative">● Live {live}</span>
        ) : (
          <span className="label-caption">Scores</span>
        )}
        <span className="text-dim text-[0.6875rem]">Updates every minute</span>
      </div>
      <ul className="flex gap-2 overflow-x-auto pb-1">
        {shown.map((s) => {
          const g = byId.get(s.gameId)!;
          const p = s.status === "in_progress" ? model.get(s.gameId) : undefined;
          const homeName = g.home.abbreviation ?? g.home.school;
          const awayName = g.away.abbreviation ?? g.away.school;
          return (
            <li key={s.gameId} className="shrink-0">
              <Link
                href={`/games/${s.gameId}`}
                className="bg-panel-inset hover:bg-panel-inset/70 flex min-w-40 flex-col gap-1 rounded-xl px-3 py-2 transition-colors"
              >
                <Row team={g.away} points={s.awayPoints} ball={s.possession === "away"} lead={lead(s, "away")} />
                <Row team={g.home} points={s.homePoints} ball={s.possession === "home"} lead={lead(s, "home")} />
                <span
                  className={
                    "text-[0.6875rem] font-bold " +
                    (s.status === "in_progress" ? "text-negative" : "text-muted")
                  }
                >
                  {liveStateLabel(s)}
                </span>
                {p && p.elapsed < 3600 ? (
                  <span className="text-accent-cyan text-[0.6875rem] font-bold" title="The live model's win chance">
                    {`Model: ${winLabel(p, homeName, awayName)}`}
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function lead(s: LiveScore, side: "home" | "away"): boolean {
  if (s.homePoints === null || s.awayPoints === null) return false;
  return side === "home" ? s.homePoints > s.awayPoints : s.awayPoints > s.homePoints;
}

function Row({
  team,
  points,
  ball,
  lead,
}: {
  team: LiveGame["home"];
  points: number | null;
  ball: boolean;
  lead: boolean;
}) {
  return (
    <span className="flex items-center gap-2 text-sm">
      <TeamChip
        abbreviation={team.abbreviation}
        color={team.color}
        altColor={team.altColor}
        title={team.school}
      />
      <span
        className={"bg-target size-1.5 rounded-full " + (ball ? "" : "invisible")}
        title={ball ? "Has the ball" : undefined}
      />
      <span
        className={"ml-auto font-extrabold tabular-nums " + (lead ? "text-ink" : "text-muted")}
      >
        {points ?? "—"}
      </span>
    </span>
  );
}
