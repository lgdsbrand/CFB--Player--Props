"use client";

import { useState } from "react";
import {
  Area,
  AreaChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useLiveProjections } from "@/components/live/use-live-model";
import {
  asOfLabel,
  chartPoints,
  favourite,
  marginRangeLabel,
  minutesSince,
  projectedScore,
  spreadLabel,
  waitingState,
  type LivePoint,
  type LiveProjection,
} from "@/lib/core/live-model";

/**
 * The live model on the game page (phase L3): the client's "updated
 * prediction based off how the game's going", with no odds behind it.
 *
 * While the game is on: each side's win %, the projected final score, the
 * model's fair spread and total with their 80% ranges, and the home win %
 * over the game so far. After the final the chart stays, as how the model saw
 * the game; the numbers it projected are history by then and are not shown
 * as if they still meant something.
 *
 * Refreshed from the browser every minute (`useLiveProjections`). The worker
 * writes a new projection every two minutes, and every minute for the NFL
 * while a game is on. "Refresh now" (the client's button for betting games
 * live, 2026-10-20) asks at once instead of waiting out the minute, and the
 * "as of" line names the score and clock the numbers were worked out from.
 */
export function LiveModelPanel({
  gameId,
  startDate,
  home,
  away,
  initial,
  initialHistory,
}: {
  gameId: number;
  startDate: string | null;
  home: string;
  away: string;
  initial: LiveProjection | null;
  initialHistory: LivePoint[];
}) {
  const [checked, setChecked] = useState(false);
  const { projections, history, refresh, refreshing } = useLiveProjections(
    [{ gameId, startDate }],
    initial ? [initial] : [],
    { historyFor: gameId, initialHistory },
  );
  const p = projections.find((row) => row.gameId === gameId);
  if (!p) {
    const state = waitingState(startDate);
    return state ? <WaitingPanel state={state} /> : null;
  }
  const final = p.status === "completed";
  const overtime = !final && p.elapsed >= 3600;
  const fav = favourite(p, home, away);
  const score = projectedScore(p);
  // 1-99 while a play is left: 99.6% is not certainty, and the cards say 99% too.
  const homePct = Math.min(99, Math.max(1, Math.round(p.pHomeWin * 100)));
  const updated = minutesSince(p.updatedAt);

  // A click always says it landed, even when the worker has not run since and
  // the numbers on screen were already the latest.
  const refreshNow = async () => {
    await refresh();
    setChecked(true);
    window.setTimeout(() => setChecked(false), 4000);
  };

  return (
    <section className="panel flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="section-header">📡 Live model</h2>
          {final ? (
            <span className="pill bg-panel-inset text-muted">Final</span>
          ) : (
            <span className="pill bg-negative/15 text-negative">● Live</span>
          )}
        </div>
        {!final && !overtime ? (
          <div className="flex items-center gap-2">
            <span className="text-dim text-[0.6875rem]">
              {checked ? "Latest numbers loaded" : updated <= 1 ? "Updated just now" : `Updated ${updated} min ago`}
            </span>
            <button
              type="button"
              onClick={refreshNow}
              disabled={refreshing}
              className="pill border-border-subtle text-accent-cyan hover:border-accent-cyan border bg-transparent disabled:opacity-60"
            >
              {refreshing ? "Checking…" : "↻ Refresh now"}
            </button>
          </div>
        ) : null}
      </div>

      {!final && !overtime ? (
        <p className="text-muted -mt-2 text-xs tabular-nums">{`As of ${asOfLabel(p, home, away)}`}</p>
      ) : null}

      {final ? (
        <p className="text-muted text-xs">
          How the model saw the game, from kickoff to the final. Its win % for {home} at each
          moment it updated.
        </p>
      ) : overtime ? (
        <p className="text-muted text-xs">
          Overtime. The model makes no call past regulation; the score decides it from here.
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between text-sm font-extrabold tabular-nums">
              <span className={p.pHomeWin < 0.5 ? "text-ink" : "text-muted"}>
                {away} {100 - homePct}%
              </span>
              <span className="label-caption">
                {fav.team === null ? "Even" : `${fav.team} favoured`}
              </span>
              <span className={p.pHomeWin > 0.5 ? "text-ink" : "text-muted"}>
                {home} {homePct}%
              </span>
            </div>
            <div className="bg-panel-inset flex h-2.5 overflow-hidden rounded-full" aria-hidden>
              <span className="bg-accent-indigo h-full" style={{ width: `${100 - homePct}%` }} />
              <span className="bg-accent-cyan h-full" style={{ width: `${homePct}%` }} />
            </div>
          </div>

          <div className="border-border-subtle grid gap-3 border-t pt-3 sm:grid-cols-3">
            <Cell label="Projected final">
              <span className="text-ink text-sm font-extrabold tabular-nums">
                {away} {score.away.toFixed(1)} · {home} {score.home.toFixed(1)}
              </span>
            </Cell>
            <Cell label="Fair spread">
              <span className="text-ink text-sm font-extrabold tabular-nums">
                {spreadLabel(p.marginMean, home, away)}
              </span>
              <span className="text-dim text-[0.6875rem]">
                80%: {marginRangeLabel(p.marginLo, p.marginHi, home, away)}
              </span>
            </Cell>
            <Cell label="Fair total">
              <span className="text-ink text-sm font-extrabold tabular-nums">
                {p.totalMean.toFixed(1)}
              </span>
              <span className="text-dim text-[0.6875rem]">
                80%: {p.totalLo.toFixed(1)} to {p.totalHi.toFixed(1)}
              </span>
            </Cell>
          </div>
        </>
      )}

      <WinChart points={history} home={home} away={away} />

      <p className="text-dim text-[0.6875rem]">
        {`Pregame it was ${spreadLabel(p.priorMargin, home, away)}, total ${p.priorTotal.toFixed(1)} ` +
          `(the ${p.priorSource === "fantasy" ? "fantasy" : "game"} model). It updates from the ` +
          "score, the clock, who has the ball and where. No odds are used, so there is no edge " +
          "here: set these numbers against your book’s live line yourself. Tested on past " +
          "games, its win % came true about as often as it said."}
      </p>
    </section>
  );
}

/** The panel's place on the page before its first numbers, so the client knows where to look. */
function WaitingPanel({ state }: { state: "before" | "waiting" }) {
  return (
    <section className="panel flex flex-col gap-2 p-4">
      <div className="flex items-center gap-2">
        <h2 className="section-header">📡 Live model</h2>
        <span className="pill bg-panel-inset text-muted">
          {state === "before" ? "Starts at kickoff" : "Starting"}
        </span>
      </div>
      <p className="text-muted text-xs">
        {state === "before"
          ? "From kickoff, this panel shows each side’s win %, the projected final score and the " +
            "fair spread and total, updated every minute or two, with a ↻ Refresh now button to " +
            "load the latest at once. It switches on by itself; there is no need to reload the page."
          : "The game has kicked off. The first numbers appear here within a few minutes, " +
            "without reloading the page."}
      </p>
    </section>
  );
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="label-caption">{label}</span>
      {children}
    </span>
  );
}

const QUARTER_TICKS = [0, 15, 30, 45, 60];
const TICK_LABELS: Record<number, string> = { 0: "KO", 15: "Q2", 30: "Half", 45: "Q4", 60: "End" };

function WinChart({ points, home, away }: { points: LivePoint[]; home: string; away: string }) {
  const data = chartPoints(points);
  if (data.length < 2) {
    return (
      <div className="bg-panel-inset text-dim flex h-32 items-center justify-center rounded-xl text-xs">
        The chart draws once the model has updated a few times.
      </div>
    );
  }
  return (
    <div className="bg-panel-inset rounded-xl p-2">
      <div className="h-44 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="liveWinFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--color-accent-cyan)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--color-accent-cyan)" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="minute"
              type="number"
              domain={[0, 60]}
              ticks={QUARTER_TICKS}
              tickFormatter={(m: number) => TICK_LABELS[m] ?? ""}
              tick={{ fill: "var(--color-dim)", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "var(--color-border-subtle)" }}
            />
            <YAxis
              domain={[0, 100]}
              ticks={[0, 50, 100]}
              tickFormatter={(v: number) => (v === 100 ? home : v === 0 ? away : "50%")}
              tick={{ fill: "var(--color-dim)", fontSize: 10, fontWeight: 700 }}
              tickLine={false}
              axisLine={false}
              width={40}
            />
            {[15, 30, 45].map((m) => (
              <ReferenceLine key={m} x={m} stroke="var(--color-border-subtle)" strokeDasharray="2 3" />
            ))}
            <ReferenceLine y={50} stroke="var(--color-border-strong)" strokeDasharray="4 3" />
            <Tooltip
              cursor={{ stroke: "var(--color-border-strong)" }}
              content={<ChartTooltip home={home} away={away} />}
            />
            <Area
              type="stepAfter"
              dataKey="homePct"
              stroke="var(--color-accent-cyan)"
              strokeWidth={2}
              fill="url(#liveWinFill)"
              isAnimationActive={false}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="text-dim px-1 text-[0.625rem]">
        {home}&rsquo;s win % over the game: up is {home}, down is {away}.
      </p>
    </div>
  );
}

function ChartTooltip({
  active,
  payload,
  home,
  away,
}: {
  active?: boolean;
  payload?: { payload?: { minute: number; homePct: number; score: string } }[];
  home: string;
  away: string;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  const homeWins = point.homePct >= 50;
  const pct = homeWins ? point.homePct : 100 - point.homePct;
  return (
    <div className="panel-inset px-2.5 py-2 text-xs">
      <div className="text-ink font-semibold">
        {away} {point.score.split("-")[0]} · {home} {point.score.split("-")[1]}
      </div>
      <div className="text-muted">
        Minute {Math.round(point.minute)} · {homeWins ? home : away} {Math.round(pct)}%
      </div>
    </div>
  );
}
