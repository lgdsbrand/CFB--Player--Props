/**
 * The props record (migration 0097): the board's prop calls, locked at kickoff
 * and graded against the box score, summed into the tiers the page shows.
 *
 * The database summarises by EDGE BAND, so each pick is counted once; a tier
 * is every band at or above its floor. "All calls" also takes the picks with
 * no edge (a one-sided price) and the ones whose side the book priced richer.
 *
 * 5%+ LEADS (user, 2026-10-16): the headline and the default list, the
 * threshold the client's MLB pitcher model uses (CLAUDE.md §6). Every tier is
 * still on the page beside it.
 *
 * NOT YET LINKED (user, 2026-10-16): the page is at /tracker?view=props for
 * review, and nothing links to it until PROPS_RECORD_PUBLIC is true.
 */

import { slateDayKey } from "./slate-days.ts";

export const PROPS_RECORD_PUBLIC = false;

/** A row of v_prop_record. `band` null is a pick with no edge. */
export interface PropRecordRow {
  season: number;
  week: number;
  marketKey: string;
  marketLabel: string;
  band: number | null;
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
  pending: number;
  units: number;
  unpricedWins: number;
}

export type TierKey = "all" | "2" | "5" | "10";
export const TIERS: readonly { key: TierKey; label: string; floor: number | null }[] = [
  { key: "5", label: "Edge 5%+", floor: 5 },
  { key: "all", label: "All calls", floor: null },
  { key: "2", label: "Edge 2%+", floor: 2 },
  { key: "10", label: "Edge 10%+", floor: 10 },
];
export const DEFAULT_TIER: TierKey = "5";

export function resolveTier(raw: string | undefined): TierKey {
  return TIERS.some((t) => t.key === raw) ? (raw as TierKey) : DEFAULT_TIER;
}

/** The edge floor of a tier as a fraction, for the picks list; null = no floor. */
export function tierMinEdge(tier: TierKey): number | null {
  const floor = TIERS.find((t) => t.key === tier)?.floor ?? null;
  return floor === null ? null : floor / 100;
}

function inTier(band: number | null, tier: TierKey): boolean {
  const floor = TIERS.find((t) => t.key === tier)?.floor ?? null;
  return floor === null || (band !== null && band >= floor);
}

export interface Totals {
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
  pending: number;
  units: number;
  unpricedWins: number;
  /** Wins over wins + losses; null with none decided. */
  winRate: number | null;
  /** Units over decided stakes (pushes and voids return the stake); null with none. */
  roi: number | null;
}

export function totals(rows: PropRecordRow[]): Totals {
  const t = rows.reduce(
    (acc, r) => ({
      wins: acc.wins + r.wins,
      losses: acc.losses + r.losses,
      pushes: acc.pushes + r.pushes,
      voids: acc.voids + r.voids,
      pending: acc.pending + r.pending,
      units: acc.units + r.units,
      unpricedWins: acc.unpricedWins + r.unpricedWins,
    }),
    { wins: 0, losses: 0, pushes: 0, voids: 0, pending: 0, units: 0, unpricedWins: 0 },
  );
  const decided = t.wins + t.losses;
  return {
    ...t,
    units: Math.round(t.units * 100) / 100,
    winRate: decided > 0 ? t.wins / decided : null,
    roi: decided > 0 ? t.units / decided : null,
  };
}

export type WeekKey = { season: number; week: number };

export function inScope(rows: PropRecordRow[], week: WeekKey | null, tier: TierKey, market?: string | null) {
  return rows.filter(
    (r) =>
      (week === null || (r.season === week.season && r.week === week.week)) &&
      inTier(r.band, tier) &&
      (!market || r.marketKey === market),
  );
}

/** One card per tier, in display order (5%+ first). */
export function tierCards(rows: PropRecordRow[], week: WeekKey | null) {
  return TIERS.map((t) => ({ ...t, totals: totals(inScope(rows, week, t.key)) }));
}

/** Each market's record within a tier, the busiest first. */
export function marketBreakdown(rows: PropRecordRow[], week: WeekKey | null, tier: TierKey) {
  const byMarket = new Map<string, { label: string; rows: PropRecordRow[] }>();
  for (const r of inScope(rows, week, tier)) {
    const entry = byMarket.get(r.marketKey) ?? { label: r.marketLabel, rows: [] };
    entry.rows.push(r);
    byMarket.set(r.marketKey, entry);
  }
  return [...byMarket.entries()]
    .map(([key, { label, rows: marketRows }]) => ({ key, label, totals: totals(marketRows) }))
    .sort(
      (a, b) =>
        b.totals.wins + b.totals.losses - (a.totals.wins + a.totals.losses) || a.label.localeCompare(b.label),
    );
}

/** The weeks with any locked pick, newest first. */
export function recordWeeks(rows: PropRecordRow[]): WeekKey[] {
  const seen = new Map<string, WeekKey>();
  for (const r of rows) seen.set(`${r.season}-${r.week}`, { season: r.season, week: r.week });
  return [...seen.values()].sort((a, b) => b.season - a.season || b.week - a.week);
}

/** `?week=` against the weeks that exist; anything else is all weeks. */
export function resolveRecordWeek(raw: string | undefined, weeks: WeekKey[]): WeekKey | null {
  const n = Number.parseInt(raw ?? "", 10);
  return weeks.find((w) => w.week === n) ?? null;
}

/** A graded pick as the list shows it (v_prop_pick_results). */
export interface PropPick {
  id: number;
  startDate: string;
  playerName: string;
  team: string | null;
  opponent: string | null;
  marketLabel: string;
  side: "over" | "under";
  line: number;
  price: number | null;
  sportsbookName: string | null;
  edge: number | null;
  actual: number | null;
  result: "win" | "loss" | "push" | "void" | "pending";
  units: number | null;
}

/** Picks in Eastern-day folds, newest day first, keeping the order given. */
export function byPickDay(picks: PropPick[]): { day: string; picks: PropPick[] }[] {
  const days = new Map<string, PropPick[]>();
  for (const p of picks) {
    const day = slateDayKey(p.startDate) ?? "unknown";
    days.set(day, [...(days.get(day) ?? []), p]);
  }
  return [...days.entries()]
    .sort(([a], [b]) => (a < b ? 1 : a > b ? -1 : 0))
    .map(([day, dayPicks]) => ({ day, picks: dayPicks }));
}
