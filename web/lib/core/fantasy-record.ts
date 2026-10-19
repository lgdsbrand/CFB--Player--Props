/**
 * The fantasy model's record as a feed for the client's own NFL Records tab
 * (client, 2026-10-19: "i have a records tab already built, would the fantasy
 * model you built be able to be plugged into that?"). He reads it at
 * /football/api/fantasy-record on his own domain.
 *
 * SPORT-AGNOSTIC CORE. Built from the tracker's graded picks (engine
 * `fantasy`, the version the site shows), so this feed, our tracker and his
 * tab cannot disagree. Snake case and `win_rate` because that is the shape
 * his Records components already read (`RecordRow`, `WeeklyRecord`).
 */

import { ENGINE_SINCE, pickLabel, record, type Record_, type TrackerPick } from "./tracker.ts";

export interface FeedRecord {
  wins: number;
  losses: number;
  pushes: number;
  pending: number;
  /** Wins over decided picks; null with none decided. */
  win_rate: number | null;
  units: number;
}

export interface FeedPick {
  season: number;
  week: number;
  kickoff: string;
  matchup: string;
  market: "spread" | "total";
  pick: string;
  price: number;
  book: string | null;
  result: TrackerPick["result"];
  units: number | null;
  /** "AWAY-HOME" once both scores are in, else null. */
  final: string | null;
}

export interface FantasyRecordFeed {
  since: string;
  rule: string;
  overall: FeedRecord;
  spread: FeedRecord;
  total: FeedRecord;
  /** Newest week first. */
  by_week: { season: number; week: number; overall: FeedRecord; spread: FeedRecord; total: FeedRecord }[];
  /** Newest first, at most `RECENT_PICKS`. */
  picks: FeedPick[];
}

export const RECENT_PICKS = 20;

export const FANTASY_RULE =
  "Spread 4+ points or total 6+ points off the closing line; one pick per game and market, locked at kickoff.";

function feedRecord(r: Record_): FeedRecord {
  return {
    wins: r.wins,
    losses: r.losses,
    pushes: r.pushes,
    pending: r.pending,
    win_rate: r.winRate,
    units: r.units,
  };
}

function split(picks: TrackerPick[]) {
  return {
    overall: feedRecord(record(picks)),
    spread: feedRecord(record(picks.filter((p) => p.market === "spreads"))),
    total: feedRecord(record(picks.filter((p) => p.market === "totals"))),
  };
}

export function fantasyRecordFeed(picks: TrackerPick[]): FantasyRecordFeed {
  const shown = picks.filter(
    (p) => p.engine === "fantasy" && (p.market === "spreads" || p.market === "totals"),
  );

  const weeks = new Map<string, TrackerPick[]>();
  for (const p of shown) {
    const key = `${p.season}-${String(p.week).padStart(2, "0")}`;
    weeks.set(key, [...(weeks.get(key) ?? []), p]);
  }

  const newestFirst = [...shown].sort(
    (a, b) => b.startDate.localeCompare(a.startDate) || b.id - a.id,
  );

  return {
    since: ENGINE_SINCE.fantasy,
    rule: FANTASY_RULE,
    ...split(shown),
    by_week: [...weeks.entries()]
      .sort((a, b) => b[0].localeCompare(a[0]))
      .map(([, list]) => ({ season: list[0].season, week: list[0].week, ...split(list) })),
    picks: newestFirst.slice(0, RECENT_PICKS).map((p) => ({
      season: p.season,
      week: p.week,
      kickoff: p.startDate,
      matchup: `${p.away} @ ${p.home}`,
      market: p.market === "spreads" ? "spread" : "total",
      pick: pickLabel(p),
      price: p.price,
      book: p.sportsbookName,
      result: p.result,
      units: p.units,
      final:
        p.awayPoints !== null && p.homePoints !== null ? `${p.awayPoints}-${p.homePoints}` : null,
    })),
  };
}
