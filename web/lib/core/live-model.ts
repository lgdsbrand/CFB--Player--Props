import { liveStateLabel } from "./live.ts";

/**
 * The live model on the page (phase L3, migration 0098).
 *
 * SPORT-AGNOSTIC CORE. The worker's poller writes `live_projections` (one row
 * per game in progress, overwritten every two minutes) and
 * `live_projection_history` (one row per distinct clock and score). This
 * module reads those rows and words them. It computes nothing the worker did
 * not: the win %, the projected final and the ranges are all stored.
 *
 * NO PRICE IS INVOLVED, AND THE WORDS SAY SO. The client asked for an updated
 * prediction mid-game "without pulling more odds credits" (2026-10-19). The
 * numbers here are the model's own fair margin and total; comparing them with
 * a live book line is the reader's job, never a claimed edge.
 */

export interface LiveProjection {
  gameId: number;
  status: "in_progress" | "completed";
  period: number | null;
  clock: string | null;
  elapsed: number;
  homePoints: number;
  awayPoints: number;
  possession: "home" | "away" | null;
  pHomeWin: number;
  marginMean: number;
  marginLo: number;
  marginHi: number;
  totalMean: number;
  totalLo: number;
  totalHi: number;
  priorMargin: number;
  priorTotal: number;
  priorSource: "game_model" | "fantasy";
  updatedAt: string;
}

export interface LivePoint {
  elapsed: number;
  homePoints: number;
  awayPoints: number;
  pHomeWin: number;
}

export const LIVE_PROJECTION_COLUMNS =
  "game_id, status, period, clock, elapsed, home_points, away_points, possession, p_home_win, " +
  "margin_mean, margin_lo, margin_hi, total_mean, total_lo, total_hi, prior_margin, prior_total, " +
  "prior_source, updated_at";

export const LIVE_HISTORY_COLUMNS = "elapsed, home_points, away_points, p_home_win";

const n = (v: unknown) => Number(v);

export function toLiveProjection(row: Record<string, unknown>): LiveProjection {
  return {
    gameId: n(row.game_id),
    status: row.status as LiveProjection["status"],
    period: row.period === null || row.period === undefined ? null : n(row.period),
    clock: (row.clock as string | null) ?? null,
    elapsed: n(row.elapsed),
    homePoints: n(row.home_points),
    awayPoints: n(row.away_points),
    possession: (row.possession as LiveProjection["possession"]) ?? null,
    pHomeWin: n(row.p_home_win),
    marginMean: n(row.margin_mean),
    marginLo: n(row.margin_lo),
    marginHi: n(row.margin_hi),
    totalMean: n(row.total_mean),
    totalLo: n(row.total_lo),
    totalHi: n(row.total_hi),
    priorMargin: n(row.prior_margin),
    priorTotal: n(row.prior_total),
    priorSource: row.prior_source as LiveProjection["priorSource"],
    updatedAt: row.updated_at as string,
  };
}

export function toLivePoint(row: Record<string, unknown>): LivePoint {
  return {
    elapsed: n(row.elapsed),
    homePoints: n(row.home_points),
    awayPoints: n(row.away_points),
    pHomeWin: n(row.p_home_win),
  };
}

/** The side the model favours and its win %, 0-100 rounded. A dead heat names nobody. */
export function favourite(
  p: Pick<LiveProjection, "pHomeWin">,
  home: string,
  away: string,
): { team: string | null; pct: number } {
  const pct = Math.round(Math.max(p.pHomeWin, 1 - p.pHomeWin) * 100);
  if (pct === 50) return { team: null, pct };
  return { team: p.pHomeWin > 0.5 ? home : away, pct };
}

/** "SF 74%" or "Even": the short form the cards use. */
export function winLabel(p: Pick<LiveProjection, "pHomeWin">, home: string, away: string): string {
  const fav = favourite(p, home, away);
  if (fav.team === null) return "Even";
  // 99.6% is not certainty while a play is left; the card never says 100%.
  return `${fav.team} ${Math.min(fav.pct, 99)}%`;
}

/** The projected final score, home and away, to one decimal. */
export function projectedScore(p: Pick<LiveProjection, "marginMean" | "totalMean">): {
  home: number;
  away: number;
} {
  const round = (x: number) => Math.round(x * 10) / 10;
  return { home: round((p.totalMean + p.marginMean) / 2), away: round((p.totalMean - p.marginMean) / 2) };
}

/** A home margin as a spread: "SF -7.5", "WAS -3.0", "Pick'em". */
export function spreadLabel(homeMargin: number, home: string, away: string): string {
  const pts = Math.abs(homeMargin);
  if (pts < 0.05) return "Pick'em";
  return `${homeMargin > 0 ? home : away} -${pts.toFixed(1)}`;
}

/** An 80% range of the final home margin in words: "SF by 0.3 to SF by 14.4". */
export function marginRangeLabel(lo: number, hi: number, home: string, away: string): string {
  const side = (m: number) =>
    Math.abs(m) < 0.05 ? "a tie" : `${m > 0 ? home : away} by ${Math.abs(m).toFixed(1)}`;
  return `${side(lo)} to ${side(hi)}`;
}

/**
 * The chart's points: home win % against game minutes. Starts at the first
 * point the history holds and ends at the latest; overtime is drawn at the
 * last regulation minute, since the clock beyond 60 means nothing to the axis.
 */
export function chartPoints(points: LivePoint[]): { minute: number; homePct: number; score: string }[] {
  return [...points]
    .sort((a, b) => a.elapsed - b.elapsed)
    .map((p) => ({
      minute: Math.round((Math.min(p.elapsed, 3600) / 60) * 10) / 10,
      homePct: Math.round(p.pHomeWin * 1000) / 10,
      score: `${p.awayPoints}-${p.homePoints}`,
    }));
}

/**
 * The moment the numbers belong to: "3rd 4:12 · WAS 14, SF 17". A live bettor
 * sets this against the game on their screen; a number is only as good as the
 * score it was worked out from.
 */
export function asOfLabel(
  p: Pick<LiveProjection, "status" | "period" | "clock" | "homePoints" | "awayPoints">,
  home: string,
  away: string,
): string {
  return `${liveStateLabel(p)} · ${away} ${p.awayPoints}, ${home} ${p.homePoints}`;
}

/** Minutes since the row was written, for "updated 2 min ago". */
export function minutesSince(iso: string, now: number = Date.now()): number {
  return Math.max(0, Math.round((now - Date.parse(iso)) / 60_000));
}
