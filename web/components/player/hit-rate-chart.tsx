"use client";

import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatLine } from "@/lib/core/format";

/**
 * The last-N games as bars against the line (CLAUDE.md §7).
 *
 * THE ONLY CLIENT COMPONENT IN THE APP. Recharts measures the DOM to lay a
 * chart out, so it cannot render on the server. Everything it needs arrives as
 * plain serialisable props — no Supabase client, no row types — which keeps the
 * read layer server-only and the client bundle to the chart itself.
 *
 * BARS RUN OLDEST TO NEWEST, LEFT TO RIGHT. That is the opposite of the last-5
 * dots on the board, which read most-recent-first because the client's pitcher
 * card does. Both conventions are defensible and neither is guessable, so the
 * axis is labelled by week and the two never appear side by side.
 *
 * COLOUR MEANS "DID THE CALLED SIDE WIN", NOT "WAS THE NUMBER BIG". A green bar
 * on an UNDER call is a low bar. Encoding it the other way would make a chart
 * that contradicts the pill above it.
 */

export type HitRateChartPoint = {
  gameId: number;
  week: number;
  value: number;
  opponent: string;
  isHome: boolean;
  neutralSite: boolean;
  /** Null on a push — the line was met exactly. */
  hit: boolean | null;
};

export function HitRateChart({
  points,
  line,
  unit,
  side,
}: {
  points: HitRateChartPoint[];
  line: number;
  unit: string | null;
  side: "over" | "under";
}) {
  if (points.length === 0) {
    return (
      <div className="text-dim flex h-40 items-center justify-center text-xs">
        No completed games to chart yet.
      </div>
    );
  }

  // Chronological. The caller works most-recent-first everywhere else, so the
  // reversal happens here rather than being assumed of every caller.
  const data = [...points].reverse().map((point) => ({
    ...point,
    label: `W${point.week}`,
    venue: point.neutralSite ? "N" : point.isHome ? "vs" : "@",
  }));

  return (
    <div className="h-44 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          margin={{ top: 8, right: 4, bottom: 0, left: -18 }}
          barCategoryGap="22%"
        >
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={{ stroke: "var(--color-border-subtle)" }}
            tick={{ fill: "var(--color-dim)", fontSize: 10 }}
            interval={0}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--color-dim)", fontSize: 10 }}
            width={38}
          />
          {/*
            The line itself. Drawn ON TOP of the bars (ifOverflow="extendDomain"
            keeps it inside the axis) because it is the thing every bar is being
            compared against — a reference line hidden behind a tall bar defeats
            the chart.
          */}
          <ReferenceLine
            y={line}
            ifOverflow="extendDomain"
            stroke="var(--color-target)"
            strokeDasharray="4 3"
            strokeWidth={1.5}
            label={{
              value: `line ${formatLine(line)}`,
              position: "insideTopRight",
              fill: "var(--color-target)",
              fontSize: 10,
            }}
          />
          <Tooltip
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
            content={<ChartTooltip line={line} unit={unit} side={side} />}
          />
          <Bar dataKey="value" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {data.map((point) => (
              <Cell
                key={point.gameId}
                fill={
                  point.hit === null
                    ? "var(--color-muted)"
                    : point.hit
                      ? "var(--color-positive)"
                      : "var(--color-negative)"
                }
                fillOpacity={point.hit === null ? 0.45 : 0.85}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** What each bar carries. Recharts types the tooltip payload loosely. */
type ChartDatum = HitRateChartPoint & { label: string; venue: string };

function ChartTooltip({
  active,
  payload,
  line,
  unit,
  side,
}: {
  active?: boolean;
  payload?: { payload?: ChartDatum }[];
  line: number;
  unit: string | null;
  side: "over" | "under";
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;

  const outcome =
    point.hit === null ? "push" : point.hit ? `hit ${side}` : `missed ${side}`;

  return (
    <div className="panel px-2.5 py-1.5 text-[0.6875rem] shadow-lg">
      <div className="font-bold">
        Week {point.week} {point.venue} {point.opponent}
      </div>
      <div className="text-muted tabular-nums">
        {point.value}
        {unit ? ` ${unit}` : ""} · line {formatLine(line)}
      </div>
      <div
        className={
          "font-semibold uppercase tracking-label " +
          (point.hit === null
            ? "text-muted"
            : point.hit
              ? "text-positive"
              : "text-negative")
        }
      >
        {outcome}
      </div>
    </div>
  );
}
