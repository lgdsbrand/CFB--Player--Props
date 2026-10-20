/**
 * Matchup stats — the fantasy model's team numbers for one NFL game, ranked
 * across the league and laid side by side (client, 2026-10-19: "a comparison
 * chart of key stats").
 *
 * DISPLAY ONLY. The rows are `fantasy_team_inputs` (migration 0093), the exact
 * numbers the fantasy formula read for this week, so the panel explains the
 * projection and changes nothing in it. Raw season to date, as the client's
 * sheet uses them, and point in time: `as_of_week = N` holds games before
 * week N only (CLAUDE.md §4), so a past game shows what was known at kickoff.
 *
 * The NFL has no `team_strength_ratings` rows, so the opponent-adjusted Team
 * comparison panel never renders on an NFL page; this is its NFL counterpart,
 * ranked by the same `rankTeams` so a bar means the same thing in both.
 */

import { rankTeams, type RankedValue } from "./team-strength.ts";

/** One team's row at one week, in the units the table stores (rates are fractions). */
export type MatchupInputRow = {
  teamId: number;
  gamesIncluded: number;
  masseyOff: number | null;
  masseyDef: number | null;
  srs: number | null;
  offEpaPlay: number | null;
  thirdDownPct: number | null;
  redZonePct: number | null;
  playsPg: number | null;
  defEpaPlay: number | null;
  oppThirdDownPct: number | null;
  oppRedZonePct: number | null;
};

export type MatchupMetricKey = Exclude<keyof MatchupInputRow, "teamId" | "gamesIncluded">;

export type MatchupMetric = {
  key: MatchupMetricKey;
  group: "ratings" | "offense" | "defense";
  label: string;
  /** The radar's axis label; null keeps the metric off the radar. */
  short: string | null;
  /** Lower is better for what a defense allows. Pace ranks most first: a style, not a quality. */
  higherFirst: boolean;
  format: (value: number) => string;
};

const signed = (digits: number) => (v: number) =>
  `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`;
// Rounded before formatting: 0.5385 * 100 is 53.8499..., which toFixed would show as 53.8.
const pct = (v: number) => `${(Math.round(v * 1000) / 10).toFixed(1)}%`;

/**
 * Ratings, then offense, then defense, in the order a reader compares them.
 * Labels are short enough not to wrap on a phone; the panel's footnote says
 * what each rating is.
 * Pace stays off the radar: a fast team is not a better one, and a radar
 * reads "further out" as "better".
 */
export const MATCHUP_METRICS: readonly MatchupMetric[] = [
  { key: "masseyOff", group: "ratings", label: "Offense rating", short: "Off rating", higherFirst: true, format: (v) => v.toFixed(1) },
  { key: "masseyDef", group: "ratings", label: "Defense rating", short: "Def rating", higherFirst: true, format: signed(1) },
  { key: "srs", group: "ratings", label: "SRS", short: null, higherFirst: true, format: signed(1) },
  { key: "offEpaPlay", group: "offense", label: "EPA / play", short: "EPA", higherFirst: true, format: signed(2) },
  { key: "thirdDownPct", group: "offense", label: "3rd down %", short: "3rd down", higherFirst: true, format: pct },
  { key: "redZonePct", group: "offense", label: "Red zone TD %", short: "Red zone", higherFirst: true, format: pct },
  { key: "playsPg", group: "offense", label: "Plays / game", short: null, higherFirst: true, format: (v) => v.toFixed(1) },
  { key: "defEpaPlay", group: "defense", label: "EPA / play allowed", short: "EPA D", higherFirst: false, format: signed(2) },
  { key: "oppThirdDownPct", group: "defense", label: "3rd down % allowed", short: "3rd down D", higherFirst: false, format: pct },
  { key: "oppRedZonePct", group: "defense", label: "Red zone TD % allowed", short: "Red zone D", higherFirst: false, format: pct },
];

export const MATCHUP_GROUPS = [
  { key: "ratings", label: "Ratings" },
  { key: "offense", label: "Offense" },
  { key: "defense", label: "Defense" },
] as const;

export type TeamMatchup = {
  teamId: number;
  gamesIncluded: number;
  metrics: Partial<Record<MatchupMetricKey, RankedValue>>;
};

/** Rank every team in `rows` on every metric and return the two asked for. */
export function rankMatchup(rows: MatchupInputRow[], teamIds: number[]): Map<number, TeamMatchup> {
  return rankTeams(rows, MATCHUP_METRICS, teamIds);
}

export type RadarPoint = {
  axis: string;
  /** League percentile, 100 at 1st and 0 at last: the radius. */
  away: number | null;
  home: number | null;
  /** The ranks behind them, for the tooltip ("3rd of 32"). */
  awayRank: RankedValue | null;
  homeRank: RankedValue | null;
};

/**
 * The radar's points: each team's league percentile on each radar metric,
 * 100 at 1st and 0 at last, so every axis reads "further out is better"
 * whatever the stat's own scale or direction. A team missing a value plots
 * nothing on that axis rather than a fake zero.
 */
export function radarPoints(
  away: TeamMatchup | undefined,
  home: TeamMatchup | undefined,
): RadarPoint[] {
  return MATCHUP_METRICS.filter((m) => m.short !== null).map((m) => {
    const a = away?.metrics[m.key] ?? null;
    const h = home?.metrics[m.key] ?? null;
    return {
      axis: m.short as string,
      away: a ? Math.round(a.share * 100) : null,
      home: h ? Math.round(h.share * 100) : null,
      awayRank: a,
      homeRank: h,
    };
  });
}

/** "2 out · 1 doubtful · 3 questionable", or null for a clean report. */
export function injurySummary(statuses: string[]): string | null {
  const parts = (["Out", "Doubtful", "Questionable"] as const)
    .map((status) => [statuses.filter((s) => s === status).length, status.toLowerCase()] as const)
    .filter(([count]) => count > 0)
    .map(([count, word]) => `${count} ${word}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}
