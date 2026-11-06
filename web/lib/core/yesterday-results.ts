/**
 * Yesterday's football results, for the client's home page (his "Yesterday's
 * Results" ribbon, client 2026-11-06: "put each sports records from the
 * previous days ... so that's not empty"). Served at /football/api/yesterday.
 *
 * Only what the tracker page already shows publicly: college's game model
 * (v2) and rule plays, the NFL's game model (fantasy). v1, the original NFL
 * sheet and the props record stay private, as on /tracker.
 *
 * "Yesterday" is the tracker's: the previous Eastern calendar day, by
 * kickoff. Every engine gets a record; only the rule plays and the NFL get
 * one card per pick, because the college game model makes ~50 picks on a
 * Saturday and the ribbon is one card per pick.
 */

import type { Sport } from "./sport.ts";
import { inPeriod, pickLabel, record, type Engine, type TrackerPick } from "./tracker.ts";

export interface PublicEngine {
  engine: Engine;
  label: string;
  /** One ribbon card per pick, or the record only. */
  cards: boolean;
}

export const PUBLIC_ENGINES: Record<Sport, readonly PublicEngine[]> = {
  cfb: [
    { engine: "v2", label: "Game model", cards: false },
    { engine: "rules", label: "Rule plays", cards: true },
  ],
  nfl: [{ engine: "fantasy", label: "Game model", cards: true }],
};

/** A day's cards are capped so a heavy slate cannot flood his ribbon. */
export const MAX_CARDS = 20;

export interface YesterdayRecord {
  engine: Engine;
  label: string;
  wins: number;
  losses: number;
  pushes: number;
  units: number;
}

export interface YesterdayCard {
  engine: Engine;
  label: string;
  away: string;
  home: string;
  awayPoints: number | null;
  homePoints: number | null;
  /** "LIB -14", "Over 52.5", "LIB ML". */
  pick: string;
  result: "win" | "loss" | "push";
  units: number | null;
}

export interface YesterdaySport {
  records: YesterdayRecord[];
  cards: YesterdayCard[];
}

/**
 * One sport's graded picks from yesterday, or null with none: a sport that
 * did not play yesterday is left out rather than shown as 0-0. Pending picks
 * (a late game not graded yet) count in neither.
 */
export function yesterdayResults(
  sport: Sport,
  picks: TrackerPick[],
  now: Date = new Date(),
): YesterdaySport | null {
  const graded = inPeriod(picks, "yesterday", now).filter((p) => p.result !== "pending");
  const records: YesterdayRecord[] = [];
  const cards: YesterdayCard[] = [];
  for (const { engine, label, cards: withCards } of PUBLIC_ENGINES[sport]) {
    const mine = graded.filter((p) => p.engine === engine);
    if (mine.length === 0) continue;
    const r = record(mine);
    records.push({ engine, label, wins: r.wins, losses: r.losses, pushes: r.pushes, units: r.units });
    if (!withCards) continue;
    for (const p of [...mine].sort((a, b) => a.startDate.localeCompare(b.startDate) || a.id - b.id)) {
      cards.push({
        engine,
        label,
        away: p.away,
        home: p.home,
        awayPoints: p.awayPoints,
        homePoints: p.homePoints,
        pick: pickLabel(p),
        result: p.result as YesterdayCard["result"],
        units: p.units,
      });
    }
  }
  if (records.length === 0) return null;
  return { records, cards: cards.slice(0, MAX_CARDS) };
}

/** The engines one sport's read needs. */
export function publicEngines(sport: Sport): Engine[] {
  return PUBLIC_ENGINES[sport].map((e) => e.engine);
}
