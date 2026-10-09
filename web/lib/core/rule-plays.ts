/**
 * Rule plays (client, 2026-10-09): the game model against the book on the
 * client's own rules, listed ahead of the games and graded on the tracker.
 *
 * SPORT-AGNOSTIC CORE. Rows of `v_rule_plays` (migration 0091) in, labels and
 * the day/week split out. The rules themselves live in the worker
 * (`evaluate_rules`, worker/core/game_picks.py); this file only describes them.
 */

import { formatFair, modelSpreadLabel, spreadSideLabel, totalSideLabel } from "./game-lines.ts";
import { slateDayKey, slateDayStart } from "./slate-days.ts";

export interface RulePlay {
  id: number;
  gameId: number;
  market: "spreads" | "totals" | "h2h";
  side: "home" | "away" | "over" | "under";
  /** Home-perspective spread, or the total; null for a moneyline. */
  line: number | null;
  price: number;
  /** Calibrated cover/over probability, or the book-blended win % (h2h). */
  modelProb: number;
  /** Moneylines: the model's own win %, which fired the rule. */
  rawProb: number | null;
  edge: number | null;
  /** Our number in the line's terms: home fair spread, or fair total. */
  modelLine: number | null;
  /** Points between modelLine and line. */
  gap: number | null;
  sportsbookName: string | null;
  season: number;
  week: number;
  startDate: string;
  neutralSite: boolean;
  homePoints: number | null;
  awayPoints: number | null;
  final: boolean;
  home: string;
  away: string;
  homeSchool: string;
  awaySchool: string;
  homeColor: string | null;
  homeAltColor: string | null;
  awayColor: string | null;
  awayAltColor: string | null;
}

/** The rules, in the client's terms, for every place that names them. */
export const RULES_SUMMARY =
  "Spread 4+ pts off the line · Total 6+ pts · Moneyline: model 60%+ on a favourite at -150 to -101";

/** The pre-registered top tier: totals 7+ points off (RULE_TOTAL_TOP_GAP). */
export const TOP_TIER_GAP = 7;

export function isTopTier(p: Pick<RulePlay, "market" | "gap">): boolean {
  return p.market === "totals" && p.gap !== null && p.gap >= TOP_TIER_GAP;
}

/** "WKU -2.5", "Under 52.5", "WKU ML". */
export function playLabel(p: Pick<RulePlay, "market" | "side" | "line" | "home" | "away">): string {
  if (p.market === "h2h") return `${p.side === "home" ? p.home : p.away} ML`;
  if (p.line === null) return p.side;
  if (p.market === "totals") return totalSideLabel(p.line, p.side === "over" ? "first" : "second");
  return spreadSideLabel(p.line, p.side === "home" ? "first" : "second", p.home, p.away);
}

/**
 * Why it is a play: "Ours WKU -6.9 · 4.4 pts off", "Ours 45.5 · 7.0 pts off",
 * "Model 66% · win chance 56%". A moneyline names both numbers: the model's own
 * fired the rule, and the win chance (blended toward the book) is the honest one.
 */
export function reasonLabel(
  p: Pick<RulePlay, "market" | "modelLine" | "gap" | "modelProb" | "rawProb" | "home" | "away">,
): string {
  if (p.market === "h2h") {
    const chance = formatFair(p.modelProb);
    return p.rawProb === null
      ? `Win chance ${chance}`
      : `Model ${formatFair(p.rawProb)} · win chance ${chance}`;
  }
  if (p.modelLine === null) return "";
  const ours =
    p.market === "spreads" ? modelSpreadLabel(-p.modelLine, p.home, p.away) : p.modelLine.toFixed(1);
  return p.gap === null ? `Ours ${ours}` : `Ours ${ours} · ${p.gap.toFixed(1)} pts off`;
}

export type PlayResult = "win" | "loss" | "push" | "pending";

/** The same grading as `v_tracker_picks`, for the list's finished games. */
export function playResult(
  p: Pick<RulePlay, "market" | "side" | "line" | "final" | "homePoints" | "awayPoints">,
): PlayResult {
  if (!p.final || p.homePoints === null || p.awayPoints === null) return "pending";
  const margin = p.homePoints - p.awayPoints;
  let cover: number;
  if (p.market === "totals") {
    cover = (p.homePoints + p.awayPoints - (p.line ?? 0)) * (p.side === "over" ? 1 : -1);
  } else if (p.market === "spreads") {
    cover = (margin + (p.line ?? 0)) * (p.side === "home" ? 1 : -1);
  } else {
    cover = margin * (p.side === "home" ? 1 : -1);
  }
  return cover > 0 ? "win" : cover < 0 ? "loss" : "push";
}

export type PlaysScope = "today" | "week";

/**
 * Today is the current slate day (Eastern, with the board's 04:00 rollover);
 * the week is every play of the football week on screen. Both in kickoff order.
 */
export function playsFor(plays: RulePlay[], scope: PlaysScope, now: Date = new Date()): RulePlay[] {
  const sorted = [...plays].sort((a, b) => a.startDate.localeCompare(b.startDate) || a.id - b.id);
  if (scope === "week") return sorted;
  const today = slateDayKey(slateDayStart(now).toISOString());
  return sorted.filter((p) => slateDayKey(p.startDate) === today);
}
