/**
 * Presenting what a defense allows to a position (CLAUDE.md §5, §7).
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * The defense detail panel answers one question — what has this week's opponent
 * allowed to this player's position, game by game — and the columns it shows
 * depend on the market being looked at. A receiving-yards call wants receiving
 * yards conceded; a rushing-attempts call wants carries. This maps one to the
 * other, and is explicit about the markets that have no defensive counterpart.
 */

import type { DefenseGameRow, PositionGroup } from "@/lib/core/types";

export type DefenseStat = {
  key: string;
  label: string;
  value: (row: DefenseGameRow) => number | null;
};

const STATS = {
  rush_attempts: {
    key: "rush_attempts",
    label: "Rush att",
    value: (r) => r.rushAttempts,
  },
  rush_yards: {
    key: "rush_yards",
    label: "Rush yds",
    value: (r) => r.rushYardsAllowed,
  },
  rush_tds: { key: "rush_tds", label: "Rush TD", value: (r) => r.rushTdsAllowed },
  targets: { key: "targets", label: "Targets", value: (r) => r.targets },
  receptions: {
    key: "receptions",
    label: "Rec",
    value: (r) => r.receptionsAllowed,
  },
  rec_yards: {
    key: "rec_yards",
    label: "Rec yds",
    value: (r) => r.recYardsAllowed,
  },
  rec_tds: { key: "rec_tds", label: "Rec TD", value: (r) => r.recTdsAllowed },
  total_tds: {
    key: "total_tds",
    label: "TD",
    value: (r) => sumOrNull(r.rushTdsAllowed, r.recTdsAllowed),
  },
} as const satisfies Record<string, DefenseStat>;

/**
 * The columns worth showing for a position.
 *
 * QB IS DELIBERATELY RUSHING-ONLY, AND THAT IS A REAL LIMIT WORTH STATING. The
 * position-split engine disaggregates a defense by the position it conceded to;
 * a quarterback is the only player who throws, so "pass yards allowed to QBs"
 * would just be team pass defense — the one number the splits exist to break
 * apart. What the splits DO carry for a quarterback is what he gained on the
 * ground, which is genuinely a position split. Any surface showing this has to
 * say so rather than let a reader infer that a defense allows 60 yards a game
 * to quarterbacks.
 */
export function defenseStatsFor(position: PositionGroup): DefenseStat[] {
  switch (position) {
    case "QB":
      return [STATS.rush_attempts, STATS.rush_yards, STATS.rush_tds];
    case "RB":
      return [
        STATS.rush_attempts,
        STATS.rush_yards,
        STATS.receptions,
        STATS.rec_yards,
        STATS.total_tds,
      ];
    default:
      return [STATS.targets, STATS.receptions, STATS.rec_yards, STATS.rec_tds];
  }
}

/**
 * The defensive column a market's stat corresponds to, if there is one.
 *
 * Returns null rather than a near-miss for the passing markets, for the reason
 * above. A panel that quietly showed rushing yards under a "pass yards" heading
 * would be worse than one that says the split does not exist.
 */
export function defenseStatForMarket(statColumn: string): DefenseStat | null {
  switch (statColumn) {
    case "rush_attempts":
      return STATS.rush_attempts;
    case "rush_yards":
      return STATS.rush_yards;
    case "targets":
      return STATS.targets;
    case "receptions":
      return STATS.receptions;
    case "rec_yards":
      return STATS.rec_yards;
    case "rec_tds":
      return STATS.rec_tds;
    case "rush_tds":
      return STATS.rush_tds;
    case "offensive_tds":
      return STATS.total_tds;
    default:
      return null;
  }
}

/** Per-game mean of a column across the games shown, or null if none carry it. */
export function perGame(
  rows: DefenseGameRow[],
  stat: DefenseStat,
): number | null {
  const values = rows
    .map((row) => stat.value(row))
    .filter((value): value is number => value !== null);
  if (values.length === 0) return null;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

/**
 * Where a rank sits in the field, as a 0..1 fraction — 0 is the softest.
 *
 * Rank 1 allows the MOST, so this does NOT invert: a small rank gives a small
 * fraction, and a bar drawn from it fills from the soft end.
 */
export function rankFraction(rank: number, ranked: number): number | null {
  if (ranked <= 1 || rank < 1) return null;
  return Math.min(Math.max((rank - 1) / (ranked - 1), 0), 1);
}

function sumOrNull(a: number | null, b: number | null): number | null {
  if (a === null && b === null) return null;
  return (a ?? 0) + (b ?? 0);
}
