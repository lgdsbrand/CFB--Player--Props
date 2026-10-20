/**
 * Team comparison — two teams' opponent-adjusted offense and defense, ranked.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). Rows in, ranks out. The rows come from
 * `team_strength_ratings` (migration 0077): what each unit would do against an
 * average opponent, fitted only on games BEFORE the week, so a game page shows
 * what was known at kickoff. The league it is ranked within is the caller's
 * to choose (FBS for college), because ranking against FCS opponents that
 * appear only as somebody's week-one visitor would flatter everyone.
 *
 * The client asked for this after the Bettor Odds card: offense and defense
 * as ranked bars (2026-10-06).
 */

/** One team's row at one week, in the units the table stores. */
export type StrengthRow = {
  teamId: number;
  gamesIncluded: number;
  offPointsPg: number | null;
  offPpa: number | null;
  offSuccess: number | null;
  offPlaysPg: number | null;
  defPointsPg: number | null;
  defPpa: number | null;
  defSuccess: number | null;
};

export type StrengthMetricKey = Exclude<keyof StrengthRow, "teamId" | "gamesIncluded">;

export type StrengthMetric = {
  key: StrengthMetricKey;
  side: "offense" | "defense";
  label: string;
  /**
   * Which end is rank 1. Lower is better for a defense's allowed figures;
   * for pace (plays per game) rank 1 is simply the fastest, which is a style
   * rather than a quality, and the label says "most" to keep it neutral.
   */
  higherFirst: boolean;
  format: (value: number) => string;
};

const pct = (value: number) => `${(value * 100).toFixed(1)}%`;

/** Offense first, in the order a reader compares them; then defense. */
export const STRENGTH_METRICS: readonly StrengthMetric[] = [
  { key: "offPointsPg", side: "offense", label: "Points / game", higherFirst: true, format: (v) => v.toFixed(1) },
  { key: "offPpa", side: "offense", label: "Points added / play", higherFirst: true, format: (v) => v.toFixed(2) },
  { key: "offSuccess", side: "offense", label: "Success rate", higherFirst: true, format: pct },
  { key: "offPlaysPg", side: "offense", label: "Plays / game (most first)", higherFirst: true, format: (v) => v.toFixed(1) },
  { key: "defPointsPg", side: "defense", label: "Points allowed / game", higherFirst: false, format: (v) => v.toFixed(1) },
  { key: "defPpa", side: "defense", label: "Points added allowed / play", higherFirst: false, format: (v) => v.toFixed(2) },
  { key: "defSuccess", side: "defense", label: "Success rate allowed", higherFirst: false, format: pct },
];

export type RankedValue = {
  value: number;
  /** 1 is the best (or, for pace, the most). Ties share the better rank. */
  rank: number;
  /** How many teams were ranked on this metric. */
  of: number;
  /** 1 at rank 1, 0 at the last rank: the bar's length. */
  share: number;
};

export type TeamStrength = {
  teamId: number;
  gamesIncluded: number;
  metrics: Partial<Record<StrengthMetricKey, RankedValue>>;
};

/**
 * Rank every team in `rows` on every metric and return the requested teams.
 *
 * Standard competition ranking ("1, 2, 2, 4"): tied teams share the better
 * rank, so a tie never reads as one team being worse than the other. A team
 * missing a value is left out of that metric's ranking rather than ranked
 * last, and gets no entry for it.
 */
export function rankStrength(rows: StrengthRow[], teamIds: number[]): Map<number, TeamStrength> {
  return rankTeams(rows, STRENGTH_METRICS, teamIds);
}

/**
 * The ranking behind `rankStrength`, for any table of per-team numbers: the
 * NFL game page ranks the fantasy model's team inputs the same way
 * (`lib/core/matchup-stats.ts`), so the two panels' ranks and bars mean the
 * same thing.
 */
export function rankTeams<K extends string>(
  rows: ({ teamId: number; gamesIncluded: number } & Record<K, number | null>)[],
  metrics: readonly { key: K; higherFirst: boolean }[],
  teamIds: number[],
): Map<number, { teamId: number; gamesIncluded: number; metrics: Partial<Record<K, RankedValue>> }> {
  const out = new Map<
    number,
    { teamId: number; gamesIncluded: number; metrics: Partial<Record<K, RankedValue>> }
  >();
  for (const id of teamIds) {
    const row = rows.find((r) => r.teamId === id);
    if (row) out.set(id, { teamId: id, gamesIncluded: row.gamesIncluded, metrics: {} });
  }
  for (const metric of metrics) {
    const valueOf = (r: Record<K, number | null> | undefined): number | null | undefined =>
      r?.[metric.key];
    const values = rows
      .map(valueOf)
      .filter((v): v is number => v !== null && v !== undefined && Number.isFinite(v));
    const of = values.length;
    for (const team of out.values()) {
      const value = valueOf(rows.find((r) => r.teamId === team.teamId));
      if (value === null || value === undefined || !Number.isFinite(value)) continue;
      const better = values.filter((v) => (metric.higherFirst ? v > value : v < value)).length;
      const rank = better + 1;
      team.metrics[metric.key] = {
        value,
        rank,
        of,
        share: of <= 1 ? 1 : (of - rank) / (of - 1),
      };
    }
  }
  return out;
}

/** "1st", "2nd", "23rd", "112th". */
export function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}
