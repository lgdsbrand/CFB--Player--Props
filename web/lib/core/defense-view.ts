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

/**
 * What a defense's `rank_vs_position` actually measures, per position.
 *
 * MIRRORS `RANK_METRICS` IN `worker/core/splits.py`, which is where the rank is
 * computed. RB and QB rank on adjusted rushing yards allowed; WR and TE on
 * adjusted receiving yards. Anything displaying a rank should say which, because
 * "softest vs QB" reads as a passing claim and is not one.
 *
 * THE QB CAVEAT IS LOAD-BEARING, not a footnote. QB position splits are rushing
 * only by construction (see `defenseStatsFor`), so a QB rank is a rushing rank —
 * it says nothing about pass yards, completions or attempts, which are four of
 * the five QB markets. Ranking QB defenses on the receiving column instead was a
 * real bug that survived two phases precisely because nothing on screen ever
 * stated what the number was built from.
 */
export type RankBasis = {
  /** Which adjusted per-game column the rank orders on. */
  key: "rush" | "rec";
  /** Compact column heading. */
  short: string;
  /** What the rank is built from, for a caption. */
  label: string;
  /** Set where the rank measures less than a reader would assume. */
  caveat: string | null;
};

export function rankBasis(position: PositionGroup): RankBasis {
  switch (position) {
    case "QB":
      return {
        key: "rush",
        short: "Rush yds/g",
        label: "opponent-adjusted rushing yards allowed to QBs",
        caveat:
          "QB ranks are rushing only — the position split cannot measure " +
          "passing, so this says nothing about pass yards or completions.",
      };
    case "RB":
      return {
        key: "rush",
        short: "Rush yds/g",
        label: "opponent-adjusted rushing yards allowed to RBs",
        caveat: null,
      };
    default:
      return {
        key: "rec",
        short: "Rec yds/g",
        label: `opponent-adjusted receiving yards allowed to ${position}s`,
        caveat: null,
      };
  }
}

/** The adjusted figure a rank was built from, given a rating row. */
export function rankedValue(
  rating: {
    adjRushYardsAllowedPg: number | null;
    adjRecYardsAllowedPg: number | null;
  },
  position: PositionGroup,
): number | null {
  return rankBasis(position).key === "rush"
    ? rating.adjRushYardsAllowedPg
    : rating.adjRecYardsAllowedPg;
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
