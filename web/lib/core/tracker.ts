/**
 * The public tracker (client, 2026-10-08): the game model's picks, graded.
 *
 * SPORT-AGNOSTIC CORE. Rows of `v_tracker_picks` (migration 0090) in, the
 * snapshot out. The grading itself is in the view: each engine's pick
 * standing at kickoff, its result against the final score, and units won at
 * the price it was frozen at on a 1-unit stake.
 *
 * TWO ENGINES, NEVER POOLED. v1 is the shadow test since week 4 (raw
 * probability, 5% edge). v2 is the calibrated table edge at 2%, from
 * 2026-10-08. The client asked to count from the model as it is now; a new
 * version does that without hiding the old one, which stays one tab away.
 */

import { slateDayKey } from "./slate-days.ts";
import { spreadSideLabel, totalSideLabel } from "./game-lines.ts";

export type Engine = "v1" | "v2";
export const ENGINES: readonly Engine[] = ["v2", "v1"];
export const DEFAULT_ENGINE: Engine = "v2";

/** When each engine started, for the page. */
export const ENGINE_SINCE: Record<Engine, string> = {
  v1: "Sep 24, 2026",
  v2: "Oct 8, 2026",
};

export type TrackerResult = "win" | "loss" | "push" | "pending";

export interface TrackerPick {
  id: number;
  engine: Engine;
  gameId: number;
  market: "spreads" | "totals" | "h2h";
  side: "home" | "away" | "over" | "under";
  /** Home-perspective spread, or the total; null for a moneyline. */
  line: number | null;
  price: number;
  modelProb: number;
  edge: number | null;
  sportsbookName: string | null;
  season: number;
  week: number;
  startDate: string;
  homePoints: number | null;
  awayPoints: number | null;
  home: string;
  away: string;
  result: TrackerResult;
  /** Units won on a 1-unit stake; null while pending. */
  units: number | null;
}

export type Period = "all" | "month" | "week" | "yesterday";
export const PERIODS: readonly Period[] = ["all", "month", "week", "yesterday"];
export const PERIOD_LABEL: Record<Period, string> = {
  all: "All time",
  month: "Month",
  week: "Week",
  yesterday: "Yesterday",
};

export function resolveEngine(raw: string | undefined): Engine {
  return raw === "v1" || raw === "v2" ? raw : DEFAULT_ENGINE;
}

export function resolvePeriod(raw: string | undefined): Period {
  return PERIODS.includes(raw as Period) ? (raw as Period) : "all";
}

/**
 * The picks a period covers, by Eastern kickoff day.
 *
 * Month is the calendar month we are in; Week is the football week of the
 * latest graded pick (a slate runs Tuesday to Saturday, so "the last seven
 * days" would split one); Yesterday is the previous Eastern calendar day.
 */
export function inPeriod(picks: TrackerPick[], period: Period, now: Date = new Date()): TrackerPick[] {
  if (period === "all") return picks;
  if (period === "month") {
    const month = slateDayKey(now.toISOString())?.slice(0, 7);
    return picks.filter((p) => slateDayKey(p.startDate)?.slice(0, 7) === month);
  }
  if (period === "yesterday") {
    const yesterday = slateDayKey(new Date(now.getTime() - 86_400_000).toISOString());
    return picks.filter((p) => slateDayKey(p.startDate) === yesterday);
  }
  const graded = picks.filter((p) => p.result !== "pending");
  const latest = (graded.length > 0 ? graded : picks).reduce<TrackerPick | null>(
    (best, p) => (best === null || p.startDate > best.startDate ? p : best),
    null,
  );
  if (!latest) return [];
  return picks.filter((p) => p.season === latest.season && p.week === latest.week);
}

export interface Record_ {
  wins: number;
  losses: number;
  pushes: number;
  pending: number;
  /** Wins over decided picks; null with none decided. Pushes are not losses. */
  winRate: number | null;
  units: number;
}

export function record(picks: TrackerPick[]): Record_ {
  let wins = 0;
  let losses = 0;
  let pushes = 0;
  let pending = 0;
  let units = 0;
  for (const p of picks) {
    if (p.result === "win") wins += 1;
    else if (p.result === "loss") losses += 1;
    else if (p.result === "push") pushes += 1;
    else pending += 1;
    units += p.units ?? 0;
  }
  const decided = wins + losses;
  return {
    wins,
    losses,
    pushes,
    pending,
    winRate: decided > 0 ? wins / decided : null,
    units: Math.round(units * 100) / 100,
  };
}

export type CardKey = "spreads" | "totals" | "edge" | "h2h";

export interface Card {
  key: CardKey;
  title: string;
  tag: string;
  emoji: string;
  /** What qualifies, in one line. */
  rule: string;
  record: Record_;
}

/**
 * Each engine's PRE-REGISTERED strongest tier, fixed before it was graded:
 * v1's totals at 64%+ raw probability (2026-10-03), v2's 3%+ calibrated edge
 * (2026-10-08, `V2_EDGE_PLAY` in worker/core/game_picks.py).
 */
export function isEdgePlay(p: TrackerPick): boolean {
  if (p.engine === "v1") return p.market === "totals" && p.modelProb >= 0.64;
  return p.edge !== null && p.edge >= 0.03;
}

export function cards(picks: TrackerPick[], engine: Engine): Card[] {
  const of = (filter: (p: TrackerPick) => boolean) => record(picks.filter(filter));
  const out: Card[] = [
    {
      key: "spreads",
      title: "Spread",
      tag: "ATS",
      emoji: "📊",
      rule: "Against the spread",
      record: of((p) => p.market === "spreads"),
    },
    {
      key: "totals",
      title: "Over/Under",
      tag: "O/U",
      emoji: "🎯",
      rule: "Game totals",
      record: of((p) => p.market === "totals"),
    },
    {
      key: "edge",
      title: "Edge plays",
      tag: "EDGE",
      emoji: "⚡",
      rule: engine === "v1" ? "Totals the model gave 64%+" : "Picks at a 3%+ edge",
      record: of(isEdgePlay),
    },
  ];
  // v1 made moneyline picks until 2026-10-03; v2 never does.
  if (engine === "v1") {
    out.push({
      key: "h2h",
      title: "Moneyline",
      tag: "ML",
      emoji: "💰",
      rule: "Retired Oct 3, 2026",
      record: of((p) => p.market === "h2h"),
    });
  }
  return out;
}

/** "LIB -14", "Over 52.5", "LIB ML". */
export function pickLabel(p: Pick<TrackerPick, "market" | "side" | "line" | "home" | "away">): string {
  if (p.market === "h2h") return `${p.side === "home" ? p.home : p.away} ML`;
  if (p.line === null) return p.side;
  if (p.market === "totals") return totalSideLabel(p.line, p.side === "over" ? "first" : "second");
  return spreadSideLabel(p.line, p.side === "home" ? "first" : "second", p.home, p.away);
}

/** Picks by Eastern kickoff day, newest day first, kickoff order within it. */
export function byDay(picks: TrackerPick[]): { day: string; picks: TrackerPick[] }[] {
  const days = new Map<string, TrackerPick[]>();
  for (const p of [...picks].sort((a, b) => a.startDate.localeCompare(b.startDate) || a.id - b.id)) {
    const key = slateDayKey(p.startDate) ?? "";
    days.set(key, [...(days.get(key) ?? []), p]);
  }
  return [...days.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([day, list]) => ({ day, picks: list }));
}

/** "+1.52u", "-3.40u", "0.00u". */
export function formatUnits(units: number): string {
  const sign = units > 0 ? "+" : units < 0 ? "-" : "";
  return `${sign}${Math.abs(units).toFixed(2)}u`;
}
