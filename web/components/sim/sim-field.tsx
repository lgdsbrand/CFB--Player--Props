"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  ballPath,
  checkpoints,
  clockLabel,
  describePlay,
  downLabel,
  edgeSideLabel,
  fieldX,
  isScoring,
  quarterLabel,
  quarterStarts,
  replayVsLine,
  spotLabel,
  type Checkpoint,
  type SimEdge,
  type SimPlay,
} from "@/lib/core/game-sim";

/** Milliseconds a play stays on screen at 1x; a score holds a little longer. */
const PLAY_MS = 1100;
const SCORE_HOLD_MS = 1400;
const SPEEDS = [1, 2, 4] as const;

const PERIOD_TITLE = { q1: "End of the 1st quarter", h1: "Halftime" } as const;
const MARKET_TITLE = { totals: "Total", spreads: "Spread", h2h: "Moneyline" } as const;

interface Team {
  name: string;
  color: string;
}

/**
 * One of the 2,000 simulated games, played on a field (phase S4): the one
 * whose final score is nearest the average result (`pick_replay`), so the
 * game shown is a typical one, not a highlight reel.
 *
 * It pauses at the end of the first quarter and at the half on a card that
 * sets the replayed score against the book's line, beside our call for that
 * period from all 2,000 games: the client's ask to "highlight each quarter
 * score, half, compare them to the odds" (2026-10-20). One replayed game
 * going over is an illustration; the call underneath is the number.
 */
export function SimField({
  replay,
  edges,
  home,
  away,
}: {
  replay: SimPlay[];
  edges: SimEdge[];
  home: Team;
  away: Team;
}) {
  const names = useMemo(() => ({ home: home.name, away: away.name }), [home.name, away.name]);
  const stops = useMemo(() => checkpoints(replay), [replay]);
  const starts = useMemo(() => quarterStarts(replay), [replay]);

  // -1: before kickoff. `pausedAt`: the checkpoint card on screen, if any.
  const [index, setIndex] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [pausedAt, setPausedAt] = useState<Checkpoint | null>(null);
  const seen = useRef<Set<string>>(new Set());

  const done = index >= replay.length - 1;
  const play = index >= 0 ? replay[index] : null;
  const before = index > 0 ? replay[index - 1] : { home: 0, away: 0 };
  const scoring = play ? isScoring(play, before) : false;

  // The ball: drawn at the snap without a transition, then carried to where
  // the play ended once the browser has painted it there, so every play reads
  // as a movement and not a jump. `movedFor` is the play the ball has been
  // carried for; until it catches up with `index`, the ball sits at the snap.
  const [movedFor, setMovedFor] = useState(-1);
  useEffect(() => {
    if (index < 0) return;
    let inner = 0;
    const outer = window.requestAnimationFrame(() => {
      inner = window.requestAnimationFrame(() => setMovedFor(index));
    });
    return () => {
      window.cancelAnimationFrame(outer);
      window.cancelAnimationFrame(inner);
    };
  }, [index]);
  const path = play ? ballPath(play) : null;
  const ball =
    path === null
      ? { x: fieldX("home", 65), animate: false }
      : movedFor === index
        ? { x: path.to, animate: true }
        : { x: path.from, animate: false };

  // Playing stops by itself at the final whistle.
  const running = playing && !done;
  useEffect(() => {
    if (!running || pausedAt) return;
    const wait = (PLAY_MS + (scoring ? SCORE_HOLD_MS : 0)) / speed;
    const t = window.setTimeout(() => {
      const next = index + 1;
      setIndex(next);
      const stop = stops.find((s) => s.index === next);
      if (stop && !seen.current.has(stop.period)) {
        seen.current.add(stop.period);
        setPausedAt(stop);
      }
    }, index < 0 ? 300 : wait);
    return () => window.clearTimeout(t);
  }, [running, pausedAt, index, speed, scoring, stops]);

  const skipTo = (q: number) => {
    const at = starts.get(q);
    if (at === undefined) return;
    // Skipping past a stop counts as having seen it.
    for (const s of stops) if (s.index < at) seen.current.add(s.period);
    setPausedAt(null);
    setIndex(at);
  };
  const restart = () => {
    seen.current = new Set();
    setPausedAt(null);
    setIndex(-1);
    setPlaying(true);
  };

  const score = play ? { home: play.home, away: play.away } : { home: 0, away: 0 };
  const q = play?.q ?? 1;
  const clock = play ? clockLabel(play.clock) : "15:00";
  const situation =
    play && play.down !== null && play.ytg !== null
      ? `${downLabel(play.down, play.togo, play.ytg)} at ${spotLabel(play.off, play.ytg, names)}`
      : null;
  const scrimmage = play && play.ytg !== null && play.down !== null ? fieldX(play.off, play.ytg) : null;
  const firstDown =
    play && play.ytg !== null && play.down !== null && play.togo !== null && play.togo < play.ytg
      ? fieldX(play.off, play.ytg - play.togo)
      : null;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
      {/* Scoreboard */}
      <div className="panel-inset grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-3 py-2">
        <TeamScore team={away} points={score.away} align="left" />
        <div className="flex flex-col items-center gap-0.5 tabular-nums">
          <span className="text-ink text-sm font-extrabold">
            {done ? "FINAL" : `${quarterLabel(q)} ${clock}`}
          </span>
          <span className="text-muted min-h-[1em] text-[0.6875rem]">{done ? "" : (situation ?? "")}</span>
        </div>
        <TeamScore team={home} points={score.home} align="right" />
      </div>

      {/* Field */}
      <div className="relative">
        <svg
          viewBox="0 0 120 53.3"
          className="block w-full rounded-lg"
          role="img"
          aria-label={`Simulated field: ${away.name} at ${home.name}`}
        >
          <rect x="0" y="0" width="120" height="53.3" fill="#14532d" />
          {Array.from({ length: 10 }, (_, i) => (
            <rect key={i} x={10 + i * 10} y="0" width="5" height="53.3" fill="#166534" opacity="0.55" />
          ))}
          <rect x="0" y="0" width="10" height="53.3" style={{ fill: home.color }} opacity="0.85" />
          <rect x="110" y="0" width="10" height="53.3" style={{ fill: away.color }} opacity="0.85" />
          <text x="5" y="26.65" fill="#fff" fontSize="4" fontWeight="900" textAnchor="middle"
            dominantBaseline="middle" transform="rotate(-90 5 26.65)">{home.name}</text>
          <text x="115" y="26.65" fill="#fff" fontSize="4" fontWeight="900" textAnchor="middle"
            dominantBaseline="middle" transform="rotate(90 115 26.65)">{away.name}</text>
          {Array.from({ length: 11 }, (_, i) => (
            <line key={i} x1={10 + i * 10} y1="0" x2={10 + i * 10} y2="53.3" stroke="#fff"
              strokeWidth={i === 0 || i === 10 ? 0.5 : 0.2} opacity="0.7" />
          ))}
          {[10, 20, 30, 40, 50, 40, 30, 20, 10].map((label, i) => (
            <text key={i} x={20 + i * 10} y="49" fill="#fff" fontSize="3.2" fontWeight="700"
              textAnchor="middle" opacity="0.75">{label}</text>
          ))}
          {scrimmage !== null && !done ? (
            <line x1={10 + scrimmage} y1="0" x2={10 + scrimmage} y2="53.3" stroke="#38bdf8" strokeWidth="0.5" />
          ) : null}
          {firstDown !== null && !done ? (
            <line x1={10 + firstDown} y1="0" x2={10 + firstDown} y2="53.3" stroke="#facc15" strokeWidth="0.5" />
          ) : null}
          <g
            style={{
              transform: `translateX(${10 + ball.x}px)`,
              transition: ball.animate ? `transform ${Math.round(700 / speed)}ms ease-out` : "none",
            }}
          >
            <ellipse cx="0" cy="26.65" rx="1.6" ry="1" fill="#92400e" stroke="#fff" strokeWidth="0.25" />
          </g>
        </svg>

        {index < 0 && !running ? (
          <button
            type="button"
            onClick={() => setPlaying(true)}
            className="cta absolute inset-0 m-auto h-fit w-fit px-4 py-2.5"
          >
            ▶ Play the simulated game
          </button>
        ) : null}
      </div>

      {/* Under the field, not over it: on a phone the field is ~140px tall. */}
      {pausedAt ? (
        <StopCard
          stop={pausedAt}
          edges={edges.filter((e) => e.period === pausedAt.period)}
          names={names}
          onContinue={() => setPausedAt(null)}
        />
      ) : null}

      {/* The play, read out */}
      <p
        aria-live="polite"
        className={`min-h-[1.25rem] text-xs font-semibold ${scoring ? "text-target" : "text-ink"}`}
      >
        {play ? describePlay(play, names) : `${away.name} at ${home.name}, kickoff to come.`}
      </p>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => (done ? restart() : setPlaying((p) => !p))}
          className="pill border-border-subtle text-ink hover:border-accent-cyan border bg-transparent"
          aria-label={done ? "Replay from kickoff" : running ? "Pause" : "Play"}
        >
          {done ? "↺ Replay" : running ? "❚❚ Pause" : "▶ Play"}
        </button>
        <div className="flex items-center gap-1" role="group" aria-label="Speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSpeed(s)}
              aria-pressed={speed === s}
              className={`pill border ${speed === s ? "border-accent-cyan text-accent-cyan" : "border-border-subtle text-muted"} bg-transparent`}
            >
              {s}×
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Skip to a quarter">
          {[1, 2, 3, 4].filter((n) => starts.has(n)).map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => skipTo(n)}
              className="pill border-border-subtle text-muted hover:text-ink border bg-transparent"
            >
              Q{n}
            </button>
          ))}
        </div>
        {index >= 0 && !done ? (
          <button
            type="button"
            onClick={restart}
            className="pill border-border-subtle text-muted hover:text-ink border bg-transparent"
          >
            ↺ Restart
          </button>
        ) : null}
      </div>
    </div>
  );
}

function TeamScore({ team, points, align }: { team: Team; points: number; align: "left" | "right" }) {
  return (
    <div className={`flex items-center gap-2 ${align === "right" ? "flex-row-reverse text-right" : ""}`}>
      <span
        className="rounded px-1.5 py-0.5 text-[0.6875rem] font-black text-white"
        style={{ backgroundColor: team.color }}
      >
        {team.name}
      </span>
      <span className="text-ink text-2xl font-black tabular-nums">{points}</span>
    </div>
  );
}

/** The pause card: the replayed period against each book line, and our call from all the games. */
function StopCard({
  stop,
  edges,
  names,
  onContinue,
}: {
  stop: Checkpoint;
  edges: SimEdge[];
  names: { home: string; away: string };
  onContinue: () => void;
}) {
  const order = ["totals", "spreads", "h2h"] as const;
  const rows = order
    .map((m) => edges.find((e) => e.market === m))
    .filter((e): e is SimEdge => e !== undefined);
  const score = { home: stop.home, away: stop.away };
  return (
    <div className="panel-inset border-target/60 flex flex-col gap-2 border p-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="label-caption">⚡ {PERIOD_TITLE[stop.period]}</span>
        <span className="text-ink text-sm font-black tabular-nums">
          {names.away} {stop.away} – {names.home} {stop.home}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="text-muted text-xs">No book has priced this period yet.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rows.map((e) => (
            <li key={e.market} className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs">
              <span className="text-muted">
                {MARKET_TITLE[e.market]} <span className="text-ink font-semibold">{bookLine(e, names)}</span>
              </span>
              <span className="text-ink font-bold">{replayWord(e, score, names)}</span>
            </li>
          ))}
        </ul>
      )}
      {rows.length > 0 ? (
        <p className="text-dim text-[0.6875rem]">
          This replay is one of 2,000 games. Our call, from all of them:{" "}
          {rows.map((e) => edgeSideLabel(e, names)).join(" · ")}.
        </p>
      ) : null}
      <button type="button" onClick={onContinue} className="cta mt-auto w-fit self-end px-3 py-1.5">
        Continue →
      </button>
    </div>
  );
}

function bookLine(e: SimEdge, names: { home: string; away: string }): string {
  if (e.market === "totals") return `${e.line?.toFixed(1)}`;
  if (e.market === "h2h") return "";
  const l = e.line ?? 0;
  return l === 0 ? "PK" : l < 0 ? `${names.home} ${l.toFixed(1)}` : `${names.away} ${(-l).toFixed(1)}`;
}

function replayWord(
  e: SimEdge,
  score: { home: number; away: number },
  names: { home: string; away: string },
): string {
  const r = replayVsLine(e, score);
  if (r === null) return "";
  if (e.market === "totals") {
    const pts = score.home + score.away;
    return r === "push" ? `${pts} pts, push` : `${pts} pts, ${r === "first" ? "OVER" : "UNDER"}`;
  }
  if (e.market === "h2h") {
    return r === "push" ? "Tied" : `${names[r === "first" ? "home" : "away"]} lead`;
  }
  return r === "push"
    ? "Push"
    : `${names[r === "first" ? "home" : "away"]} cover`;
}
