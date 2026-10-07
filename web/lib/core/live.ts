/**
 * Live scores (migration 0088): what the scoreboard shows right now.
 *
 * SPORT-AGNOSTIC CORE. Today only college games have rows (CFBD's
 * scoreboard, polled every two minutes while a game is on). Not the score of
 * record: `games` keeps the final, from the daily results ingest.
 */

import type { Sport } from "./sport.ts";

export type LiveStatus = "scheduled" | "in_progress" | "completed";

/**
 * Sports with a live-score feed. College only (CFBD's scoreboard, free); NFL
 * scores would be bought from the Odds API, which the user declined
 * (2026-10-06). A page for any other sport renders no live UI and polls nothing.
 */
export const LIVE_SPORTS: readonly Sport[] = ["cfb"];

export interface LiveScore {
  gameId: number;
  status: LiveStatus;
  period: number | null;
  clock: string | null;
  homePoints: number | null;
  awayPoints: number | null;
  homeLineScores: number[] | null;
  awayLineScores: number[] | null;
  possession: "home" | "away" | null;
  situation: string | null;
  lastPlay: string | null;
  updatedAt: string;
}

/** The columns every live read selects, browser and server alike. */
export const LIVE_COLUMNS =
  "game_id, status, period, clock, home_points, away_points, home_line_scores, " +
  "away_line_scores, possession, situation, last_play, updated_at";

/** How often the page asks for fresh rows. The poller writes every two minutes. */
export const LIVE_REFRESH_MS = 60_000;

export function toLiveScore(row: Record<string, unknown>): LiveScore {
  const num = (v: unknown) => (v === null || v === undefined ? null : Number(v));
  const nums = (v: unknown) => (Array.isArray(v) ? v.map(Number) : null);
  return {
    gameId: Number(row.game_id),
    status: row.status as LiveStatus,
    period: num(row.period),
    clock: (row.clock as string | null) ?? null,
    homePoints: num(row.home_points),
    awayPoints: num(row.away_points),
    homeLineScores: nums(row.home_line_scores),
    awayLineScores: nums(row.away_line_scores),
    possession: (row.possession as "home" | "away" | null) ?? null,
    situation: (row.situation as string | null) ?? null,
    lastPlay: (row.last_play as string | null) ?? null,
    updatedAt: row.updated_at as string,
  };
}

/** "1st", "OT", "2OT": a college game's fifth period is overtime. */
export function periodLabel(period: number): string {
  if (period <= 4) return ["1st", "2nd", "3rd", "4th"][period - 1] ?? `Q${period}`;
  const ot = period - 4;
  return ot === 1 ? "OT" : `${ot}OT`;
}

const isZeroClock = (clock: string | null) => clock === null || /^0?0:00$/.test(clock.trim());

/**
 * The state line: "Final", "Final/OT", "Half", "End 1st", "3rd 8:42", "Live".
 *
 * A clock of zero or none in the 2nd is halftime, and elsewhere the end of
 * that period, because that is how the scoreboard reads between periods.
 */
export function liveStateLabel(score: LiveScore): string {
  if (score.status === "completed") {
    return score.period !== null && score.period > 4 ? `Final/${periodLabel(score.period)}` : "Final";
  }
  if (score.status === "scheduled") return "Soon";
  if (score.period === null) return "Live";
  if (isZeroClock(score.clock)) {
    return score.period === 2 ? "Half" : `End ${periodLabel(score.period)}`;
  }
  return `${periodLabel(score.period)} ${score.clock}`;
}

/** Live first, then finals; within each, the order given (kickoff). */
export function orderLive(scores: LiveScore[]): LiveScore[] {
  const rank = (s: LiveScore) => (s.status === "in_progress" ? 0 : s.status === "completed" ? 1 : 2);
  return scores
    .map((s, i) => ({ s, i }))
    .sort((a, b) => rank(a.s) - rank(b.s) || a.i - b.i)
    .map(({ s }) => s);
}
