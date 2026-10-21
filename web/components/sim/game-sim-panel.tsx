"use client";

import { useEffect, useState } from "react";

import { SimField } from "@/components/sim/sim-field";
import { useLiveProjections } from "@/components/live/use-live-model";
import { formatAmericanOdds, formatEdge } from "@/lib/core/format";
import {
  GAME_SIM_COLUMNS,
  edgeSideLabel,
  histRange,
  segmentFor,
  sideNumbers,
  toGameSim,
  type GameSim,
  type SimEdge,
  type SimPeriod,
} from "@/lib/core/game-sim";
import { LIVE_REFRESH_MS } from "@/lib/core/live";
import type { LiveProjection } from "@/lib/core/live-model";
import { getBrowserSupabaseClient } from "@/lib/supabase/client";

const PERIODS: { key: SimPeriod; title: string }[] = [
  { key: "q1", title: "1st quarter" },
  { key: "h1", title: "1st half" },
];
const MARKETS = [
  { key: "totals", title: "Total" },
  { key: "spreads", title: "Spread" },
  { key: "h2h", title: "Moneyline" },
] as const;

const RESULT_STYLE = {
  won: "bg-positive/15 text-positive",
  lost: "bg-negative/15 text-negative",
  push: "bg-panel-inset text-muted",
} as const;

/**
 * The NFL game simulator on the game page (phase S4).
 *
 * The client asked for a 2D simulation that sets itself apart by highlighting
 * each quarter's and half's score against the odds (2026-10-20). Three parts:
 *
 * - the replay (`SimField`): one simulated game on a field, pausing at the
 *   first quarter and the half against the book's lines;
 * - a card per period: the 2,000 games' average score and range, and for each
 *   market our side, our % and the book's, and the edge, from
 *   `v_game_sim_edge_results`. THE EDGE IS SHRUNK toward the book (CLAUDE.md
 *   §11, 2026-10-21) and the page says so. The call is the pregame one, frozen
 *   at kickoff, and graded once the period is played;
 * - once the game is on, the live simulation's numbers for each period
 *   (refreshed every minute) and the LIVE MODEL's win % as the win bar: the
 *   simulator tied the live model on the full game in the S1 backtest, so the
 *   full-game number stays the live model's.
 */
export function GameSimPanel({
  gameId,
  startDate,
  kickedOff,
  completed,
  home,
  away,
  homeColor,
  awayColor,
  pregame,
  initialLive,
  edges,
  books,
  initialProjection,
}: {
  gameId: number;
  startDate: string | null;
  kickedOff: boolean;
  completed: boolean;
  home: string;
  away: string;
  homeColor: string | null;
  awayColor: string | null;
  pregame: GameSim;
  initialLive: GameSim | null;
  edges: SimEdge[];
  books: Record<number, string>;
  initialProjection: LiveProjection | null;
}) {
  const names = { home, away };
  const live = useLiveSim(gameId, kickedOff && !completed, initialLive);
  const { projections } = useLiveProjections(
    kickedOff ? [{ gameId, startDate }] : [],
    initialProjection ? [initialProjection] : [],
  );
  const projection = projections.find((p) => p.gameId === gameId) ?? null;

  return (
    <section className="panel flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="section-header">🏈 Game simulation</h2>
          <span className="pill bg-accent-indigo/15 text-accent-cyan">
            {pregame.sims.toLocaleString("en-US")} games
          </span>
        </div>
        {kickedOff && !completed && projection && projection.status === "in_progress" ? (
          <span className="pill bg-negative/15 text-negative">● Live</span>
        ) : null}
      </div>
      <p className="text-muted -mt-2 text-xs">
        Every play of this game simulated {pregame.sims.toLocaleString("en-US")} times from our
        projection, using real NFL plays from the last five seasons. No odds go into it. Below:
        one of those games, then what all of them say about the first quarter and the first half
        against the book.
      </p>

      {projection && projection.status === "in_progress" ? (
        <WinBar projection={projection} home={home} away={away} />
      ) : null}

      {pregame.replay.length > 0 ? (
        <SimField
          replay={pregame.replay}
          edges={edges}
          home={{ name: home, color: homeColor ?? "var(--color-accent-cyan)" }}
          away={{ name: away, color: awayColor ?? "var(--color-accent-indigo)" }}
        />
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        {PERIODS.map(({ key, title }) => (
          <PeriodCard
            key={key}
            title={title}
            period={key}
            pregame={pregame}
            live={live}
            edges={edges.filter((e) => e.period === key)}
            books={books}
            names={names}
          />
        ))}
      </div>

      <p className="text-dim text-[0.6875rem]">
        Our % and edge are shown shrunk toward the book&rsquo;s price: on full games, edges this
        size turned out to be mostly our projection disagreeing with the market, so only a fraction
        of the disagreement is shown (a quarter of it on totals, about a twentieth on spreads and
        moneylines). The call is made before kickoff, locked at kickoff and graded once the period
        is played. The win bar is the live model&rsquo;s.
      </p>
    </section>
  );
}

function PeriodCard({
  title,
  period,
  pregame,
  live,
  edges,
  books,
  names,
}: {
  title: string;
  period: SimPeriod;
  pregame: GameSim;
  live: GameSim | null;
  edges: SimEdge[];
  books: Record<number, string>;
  names: { home: string; away: string };
}) {
  const seg = segmentFor(pregame, period);
  const [lo, hi] = histRange(seg.total);
  const liveSeg = live ? segmentFor(live, period) : null;
  return (
    <div className="panel-inset flex flex-col gap-3 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="label-caption">⏱ {title}</span>
        <span className="text-ink text-sm font-extrabold tabular-nums">
          {names.away} {seg.away.toFixed(1)} – {names.home} {seg.home.toFixed(1)}
        </span>
      </div>
      <p className="text-muted -mt-2 text-[0.6875rem] tabular-nums">
        Average of the simulated games. Total {lo}–{hi} points in 8 of 10.
        {liveSeg ? (
          <>
            {" "}
            <span className="text-negative font-semibold">
              Live: {names.away} {liveSeg.away.toFixed(1)} – {names.home} {liveSeg.home.toFixed(1)}
            </span>
          </>
        ) : null}
      </p>
      {edges.length === 0 ? (
        <p className="text-muted text-xs">No book has priced the {title.toLowerCase()} yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {MARKETS.map(({ key, title: label }) => {
            const e = edges.find((x) => x.market === key);
            return e ? <EdgeRow key={key} label={label} edge={e} books={books} names={names} /> : null;
          })}
        </ul>
      )}
    </div>
  );
}

function EdgeRow({
  label,
  edge,
  books,
  names,
}: {
  label: string;
  edge: SimEdge;
  books: Record<number, string>;
  names: { home: string; away: string };
}) {
  const s = sideNumbers(edge);
  return (
    <li className="border-border-subtle flex flex-col gap-1 border-t pt-2 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-muted text-[0.6875rem] font-semibold uppercase tracking-wider">{label}</span>
        <div className="flex items-center gap-1.5">
          <span className="pill bg-accent-indigo/15 text-accent-cyan">
            {edgeSideLabel(edge, names)} {formatEdge(edge.shownEdge)}
          </span>
          {edge.result ? (
            <span className={`pill ${RESULT_STYLE[edge.result]}`}>{edge.result}</span>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs tabular-nums">
        <span className="text-ink">
          Ours <b>{(s.ours * 100).toFixed(1)}%</b>
          <span className="text-muted"> · Book {(s.book * 100).toFixed(1)}%</span>
        </span>
        <span className="text-dim">
          {books[edge.sportsbookId] ?? "Book"} {formatAmericanOdds(s.price)}
        </span>
      </div>
    </li>
  );
}

function WinBar({ projection, home, away }: { projection: LiveProjection; home: string; away: string }) {
  const homePct = Math.min(99, Math.max(1, Math.round(projection.pHomeWin * 100)));
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between text-sm font-extrabold tabular-nums">
        <span className={homePct < 50 ? "text-ink" : "text-muted"}>
          {away} {100 - homePct}%
        </span>
        <span className="label-caption">Live model win %</span>
        <span className={homePct > 50 ? "text-ink" : "text-muted"}>
          {home} {homePct}%
        </span>
      </div>
      <div className="bg-panel-inset flex h-2.5 overflow-hidden rounded-full" aria-hidden>
        <span className="bg-accent-indigo h-full" style={{ width: `${100 - homePct}%` }} />
        <span className="bg-accent-cyan h-full" style={{ width: `${homePct}%` }} />
      </div>
    </div>
  );
}

/** The live simulation row (no replay), refreshed every minute while the game is on. */
function useLiveSim(gameId: number, active: boolean, initial: GameSim | null): GameSim | null {
  const [sim, setSim] = useState(initial);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const load = async () => {
      if (document.hidden) return;
      const { data, error } = await getBrowserSupabaseClient()
        .from("game_sims")
        .select(GAME_SIM_COLUMNS)
        .eq("game_id", gameId)
        .eq("stage", "live");
      if (cancelled || error || !data || data.length === 0) return;
      setSim(toGameSim(data[0] as unknown as Record<string, unknown>));
    };
    const timer = window.setInterval(() => void load(), LIVE_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [gameId, active]);
  return sim;
}
