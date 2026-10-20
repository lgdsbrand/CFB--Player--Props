"use client";

import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import { ordinal } from "@/lib/core/team-strength";
import type { RadarPoint } from "@/lib/core/matchup-stats";

/**
 * The two teams' profiles on one radar: each axis is a league percentile, so
 * further out is better on every axis whatever the stat. The exact values and
 * ranks are in the rows beside it; the radar is the at-a-glance shape.
 *
 * Away is indigo and home cyan, the same order as "AWAY @ HOME" everywhere
 * else, with a legend so the colours never have to be guessed.
 */
export function MatchupRadar({
  points,
  away,
  home,
}: {
  points: RadarPoint[];
  away: string;
  home: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="h-64 w-full sm:h-72">
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={points} outerRadius="72%" margin={{ top: 8, right: 24, bottom: 8, left: 24 }}>
            <PolarGrid stroke="var(--color-border-subtle)" />
            <PolarAngleAxis
              dataKey="axis"
              tick={{ fill: "var(--color-muted)", fontSize: 10, fontWeight: 700 }}
            />
            <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} tickCount={5} />
            <Radar
              name={away}
              dataKey="away"
              stroke="var(--color-accent-indigo)"
              fill="var(--color-accent-indigo)"
              fillOpacity={0.25}
              strokeWidth={2}
              isAnimationActive={false}
            />
            <Radar
              name={home}
              dataKey="home"
              stroke="var(--color-accent-cyan)"
              fill="var(--color-accent-cyan)"
              fillOpacity={0.2}
              strokeWidth={2}
              isAnimationActive={false}
            />
            <Tooltip content={<RadarTooltip away={away} home={home} />} />
          </RadarChart>
        </ResponsiveContainer>
      </div>
      <div className="flex items-center justify-center gap-4 text-xs font-bold">
        <Key colour="var(--color-accent-indigo)" label={away} />
        <Key colour="var(--color-accent-cyan)" label={home} />
      </div>
    </div>
  );
}

function Key({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="text-ink flex items-center gap-1.5">
      <span className="h-2.5 w-2.5 rounded-full" style={{ background: colour }} />
      {label}
    </span>
  );
}

/** The rank, not the percentile: "3rd of 32" is what a reader compares. */
function RadarTooltip({
  active,
  payload,
  away,
  home,
}: {
  active?: boolean;
  payload?: { payload?: RadarPoint }[];
  away: string;
  home: string;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  const line = (label: string, value: RadarPoint["awayRank"]) => (
    <div className="flex justify-between gap-3">
      <span className="text-muted">{label}</span>
      <span className="text-ink font-bold tabular-nums">
        {value === null ? "—" : `${ordinal(value.rank)} of ${value.of}`}
      </span>
    </div>
  );
  return (
    <div className="panel-inset px-2.5 py-2 text-xs">
      <div className="text-ink mb-1 font-semibold">{point.axis}</div>
      {line(away, point.awayRank)}
      {line(home, point.homeRank)}
    </div>
  );
}
