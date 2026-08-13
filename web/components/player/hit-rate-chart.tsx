"use client";

import { useState } from "react";
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
import {
  offsetBounds,
  regrade,
  steppedLine,
  tally,
} from "@/lib/core/line-stepper";

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
  step,
}: {
  points: HitRateChartPoint[];
  line: number;
  unit: string | null;
  side: "over" | "under";
  /**
   * The market's rung step, from `markets.ladder_step`. Null disables the
   * stepper — binary markets have no alternate lines, exactly as they have no
   * ladder, because anytime TD is one probability and a ladder of it would be
   * that number repeated.
   */
  step: number | null;
}) {
  const [offset, setOffset] = useState(0);

  if (points.length === 0) {
    return (
      <div className="text-dim flex h-40 items-center justify-center text-xs">
        No completed games to chart yet.
      </div>
    );
  }

  const bounds = step ? offsetBounds(line, step) : { min: 0, max: 0 };
  const active = step ? steppedLine(line, offset, step) : line;
  const shifted = active !== line;

  // Re-graded against whatever line is showing. At offset 0 this reproduces the
  // server's grading exactly — `regrade` and `gradeGames` share `outcomeFor`,
  // and a test asserts the two agree rather than trusting that they do.
  const graded = regrade(points, active, side);
  const counts = tally(graded);

  // Chronological. The caller works most-recent-first everywhere else, so the
  // reversal happens here rather than being assumed of every caller.
  const data = [...graded].reverse().map((point) => ({
    ...point,
    label: `W${point.week}`,
    venue: point.neutralSite ? "N" : point.isHome ? "vs" : "@",
  }));

  return (
    <div className="flex flex-col gap-2">
      {step ? (
        <Stepper
          line={line}
          active={active}
          offset={offset}
          bounds={bounds}
          shifted={shifted}
          counts={counts}
          side={side}
          onChange={setOffset}
        />
      ) : null}

      <div className="h-44 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          // NO NEGATIVE LEFT MARGIN. It used to be -18, which pulled the y-axis
          // partly outside the plot area and CLIPPED THE LEADING DIGIT: a
          // 140-yard game rendered its axis tick as "40". Not a cosmetic
          // problem — a chart that prints the wrong number is worse than one
          // with a wider gutter.
          margin={{ top: 8, right: 4, bottom: 0, left: 0 }}
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
            y={active}
            ifOverflow="extendDomain"
            stroke="var(--color-target)"
            strokeDasharray="4 3"
            strokeWidth={1.5}
            label={{
              value: shifted
                ? `alt ${formatLine(active)}`
                : `line ${formatLine(active)}`,
              position: "insideTopRight",
              fill: "var(--color-target)",
              fontSize: 10,
            }}
          />
          {/* The POSTED line stays drawn once the reader steps away from it, so
              the chart never stops showing the number a book actually offered.
              Fainter and solid, so the two are not mistaken for each other. */}
          {shifted ? (
            <ReferenceLine
              y={line}
              ifOverflow="extendDomain"
              stroke="var(--color-muted)"
              strokeWidth={1}
              strokeOpacity={0.5}
              // LEFT, so it cannot collide with the alt label. Both sat on the
              // right at first and overlapped into an unreadable smear at
              // 390px whenever the two lines were close together.
              label={{
                value: `posted ${formatLine(line)}`,
                position: "insideBottomLeft",
                fill: "var(--color-muted)",
                fontSize: 9,
              }}
            />
          ) : null}
          <Tooltip
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
            content={<ChartTooltip line={active} unit={unit} side={side} />}
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
    </div>
  );
}

/**
 * The +/- control, and what the past says at the line it lands on.
 *
 * THE LADDER'S SIBLING. The alternate-line panel answers "what does the model
 * think at 80.5"; this answers "what would his last ten games have done at
 * 80.5". Both were asked for together and they belong together, but they are
 * different claims and the copy keeps them apart.
 *
 * IT SAYS WHAT IT IS, PLAINLY. Ten games re-graded at a line nobody posted is a
 * small sample, and stepping until the number flatters is the definition of
 * cherry-picking. Saying so costs one line of text and is the difference
 * between a looking glass and an implied recommendation.
 */
function Stepper({
  line,
  active,
  offset,
  bounds,
  shifted,
  counts,
  side,
  onChange,
}: {
  line: number;
  active: number;
  offset: number;
  bounds: { min: number; max: number };
  shifted: boolean;
  counts: { hits: number; decided: number; pushes: number; rate: number | null };
  side: "over" | "under";
  onChange: (next: number) => void;
}) {
  const canDown = offset > bounds.min;
  const canUp = offset < bounds.max;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <StepButton
            label="Lower the line"
            symbol="−"
            disabled={!canDown}
            onClick={() => onChange(offset - 1)}
          />
          <span className="min-w-20 text-center text-sm font-extrabold tabular-nums">
            {formatLine(active)}
          </span>
          <StepButton
            label="Raise the line"
            symbol="+"
            disabled={!canUp}
            onClick={() => onChange(offset + 1)}
          />
          {shifted ? (
            <button
              type="button"
              onClick={() => onChange(0)}
              className="text-accent-cyan ml-1 text-[0.625rem] hover:underline"
            >
              reset to {formatLine(line)}
            </button>
          ) : null}
        </div>

        <span className="text-muted text-xs tabular-nums">
          {counts.decided === 0 ? (
            "no games decided at this line"
          ) : (
            <>
              <span className="text-ink font-bold">
                {counts.hits} of {counts.decided}
              </span>{" "}
              {side} {formatLine(active)}
              {counts.pushes > 0 ? ` · ${counts.pushes} push` : ""}
            </>
          )}
        </span>
      </div>

      <p className="text-dim text-[0.625rem]">
        {shifted
          ? "Past games re-graded at a line no book posted, and the LAST 5 row below stays at the posted one. Ten games is a small sample and stepping until the number improves will always find one, so read this as history rather than as a signal — the model's own view at other lines is under Alternate lines."
          : "Step the line to see how these games would have graded against it. The called side does not move with it."}
      </p>
    </div>
  );
}

function StepButton({
  label,
  symbol,
  disabled,
  onClick,
}: {
  label: string;
  symbol: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={
        "border-border-subtle bg-panel flex h-7 w-7 items-center justify-center " +
        "rounded-lg border text-sm font-bold transition-colors " +
        (disabled
          ? "text-dim cursor-not-allowed opacity-40"
          : "text-ink hover:border-accent-cyan/50 hover:bg-accent-indigo/10")
      }
    >
      {symbol}
    </button>
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
