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

import { WeekTick } from "@/components/player/hit-rate-chart";
import { formatLine } from "@/lib/core/format";

/**
 * The last-N games when NO BOOK HAS POSTED A LINE.
 *
 * The client's ask, 2026-09-21: "even if theres not a book line it would still
 * show their last 5/10 stats and would make the mark on where the model
 * projects them at." Until now this surface was a sentence explaining that
 * there was nothing to show.
 *
 * WHY A SECOND CHART RATHER THAN A MODE ON `HitRateChart`. That component is
 * built around a line: it re-grades every bar against one, colours by whether
 * the called side won, and carries a stepper for walking the line up and down.
 * None of those apply here, and threading a flag through all three would leave
 * the component's central claim — "colour means did the called side win" — true
 * only half the time. The two share the axis tick, which is the only part where
 * drifting apart would be visible.
 *
 * EVERY BAR IS THE SAME COLOUR, AND THAT IS THE POINT. There is no outcome, so
 * there is nothing to encode. Colouring bars against the model's own projection
 * would invent a hit rate out of the model's opinion of itself — see
 * `lib/core/form.ts` for why that number is the most misleading one available.
 *
 * THE MARK IS CYAN, NOT AMBER. Amber is the book's line everywhere else in this
 * product; this is the model, and the model gets the model's colour. A reader
 * who has learned that a dashed amber line is what a book posted must not meet
 * a dashed amber line that no book posted.
 */

export type FormChartPoint = {
  gameId: number;
  season: number;
  week: number;
  value: number;
  opponent: string;
  isHome: boolean;
  neutralSite: boolean;
};

export function FormChart({
  points,
  projected,
  unit,
  season,
}: {
  points: FormChartPoint[];
  /**
   * The model's projected median, drawn as the reference mark. Null where the
   * market withholds it, in which case the bars stand alone — still the last-N
   * stats the client asked for, just with nothing to compare them to.
   */
  projected: number | null;
  unit: string | null;
  /** The season on screen; earlier games draw faded and dashed, as in the
   *  graded chart, so a borrowed week cannot pass for one not yet played. */
  season: number;
}) {
  if (points.length === 0) {
    return (
      <div className="text-dim flex h-40 items-center justify-center text-xs">
        No completed games to chart yet.
      </div>
    );
  }

  // Chronological, like the graded chart: the caller works most-recent-first
  // everywhere else, so the reversal happens here rather than at every caller.
  const data = [...points].reverse().map((point) => {
    const prior = point.season < season;
    return {
      ...point,
      prior,
      label: prior
        ? `W${point.week} '${String(point.season).slice(-2)}`
        : `W${point.week}`,
      venue: point.neutralSite ? "N" : point.isHome ? "vs" : "@",
    };
  });
  const hasPrior = data.some((point) => point.prior);

  return (
    <div className="flex flex-col gap-2">
      <div className="h-44 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            // Left margin 0, not negative: a negative one pulls the y-axis
            // partly outside the plot area and clips the leading digit, so a
            // 140-yard game renders its tick as "40". Same trap as the graded
            // chart, which carries the same note.
            margin={{ top: 8, right: 4, bottom: 0, left: 0 }}
            barCategoryGap="22%"
          >
            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={{ stroke: "var(--color-border-subtle)" }}
              tick={<WeekTick />}
              height={hasPrior ? 38 : 30}
              interval={0}
            />
            <YAxis
              tickLine={false}
              axisLine={false}
              tick={{ fill: "var(--color-dim)", fontSize: 10 }}
              width={38}
            />
            {projected !== null ? (
              <ReferenceLine
                y={projected}
                ifOverflow="extendDomain"
                stroke="var(--color-accent-cyan)"
                strokeDasharray="4 3"
                strokeWidth={1.5}
                label={{
                  // "model", never "line". The word "line" on this chart would
                  // claim a book had posted something.
                  value: `model ${formatLine(projected)}`,
                  position: "insideTopRight",
                  fill: "var(--color-accent-cyan)",
                  fontSize: 10,
                }}
              />
            ) : null}
            <Tooltip
              cursor={{ fill: "rgba(255,255,255,0.04)" }}
              content={<FormTooltip unit={unit} projected={projected} />}
            />
            <Bar dataKey="value" radius={[3, 3, 0, 0]} isAnimationActive={false}>
              {data.map((point) => (
                <Cell
                  key={point.gameId}
                  fill="var(--color-accent-indigo)"
                  fillOpacity={point.prior ? 0.35 : 0.85}
                  stroke={point.prior ? "var(--color-accent-indigo)" : undefined}
                  strokeDasharray={point.prior ? "3 2" : undefined}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {hasPrior ? (
        <p className="text-dim text-[0.625rem]">
          Faded, dashed bars are from {season - 1}, filling in while {season}{" "}
          has too few games. They drop out as this season&rsquo;s arrive.
        </p>
      ) : null}
    </div>
  );
}

/**
 * The hover card. States the value and the matchup, and nothing about whether
 * the game was good — there is no line, so there is no such thing here.
 */
function FormTooltip({
  active,
  payload,
  unit,
  projected,
}: {
  active?: boolean;
  payload?: {
    payload?: {
      value: number;
      opponent: string;
      venue: string;
      week: number;
      season: number;
      prior: boolean;
    };
  }[];
  unit: string | null;
  projected: number | null;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;

  const suffix = unit ? ` ${unit}` : "";

  return (
    <div className="panel-inset px-2.5 py-2 text-xs">
      <div className="text-ink font-semibold">
        {point.venue} {point.opponent}
        <span className="text-dim font-normal">
          {" "}
          · W{point.week}
          {point.prior ? ` '${String(point.season).slice(-2)}` : ""}
        </span>
      </div>
      <div className="text-ink mt-0.5 font-mono tabular-nums">
        {formatLine(point.value)}
        {suffix}
      </div>
      {projected !== null ? (
        <div className="text-dim mt-0.5 font-mono text-[0.625rem] tabular-nums">
          model {formatLine(projected)}
        </div>
      ) : null}
    </div>
  );
}
