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
 * The same columns narrowed to the first quarter (migration 0069).
 *
 * WRITTEN OUT RATHER THAN DERIVED FROM `STATS` BY A NAME RULE. A generated
 * `q1_` + key would compile and then silently read `undefined` off a row the day
 * a column is named differently from its whole-game sibling — and `q1Plays` vs
 * `plays` is already one such pair. Eight entries, each naming its own field, is
 * the version a reader can check against the schema.
 *
 * THE LABELS SAY "Q1" and the panel says so again in its caption. This table
 * looks exactly like the whole-game one, at about a fifth of the values, and a
 * reader who missed the heading would read a soft defense as an elite one.
 */
const Q1_STATS = {
  rush_attempts: {
    key: "q1_rush_attempts",
    label: "Q1 rush att",
    value: (r) => r.q1RushAttempts,
  },
  rush_yards: {
    key: "q1_rush_yards",
    label: "Q1 rush yds",
    value: (r) => r.q1RushYardsAllowed,
  },
  rush_tds: {
    key: "q1_rush_tds",
    label: "Q1 rush TD",
    value: (r) => r.q1RushTdsAllowed,
  },
  targets: { key: "q1_targets", label: "Q1 targets", value: (r) => r.q1Targets },
  receptions: {
    key: "q1_receptions",
    label: "Q1 rec",
    value: (r) => r.q1ReceptionsAllowed,
  },
  rec_yards: {
    key: "q1_rec_yards",
    label: "Q1 rec yds",
    value: (r) => r.q1RecYardsAllowed,
  },
  rec_tds: {
    key: "q1_rec_tds",
    label: "Q1 rec TD",
    value: (r) => r.q1RecTdsAllowed,
  },
  total_tds: {
    key: "q1_total_tds",
    label: "Q1 TD",
    value: (r) => sumOrNull(r.q1RushTdsAllowed, r.q1RecTdsAllowed),
  },
} as const satisfies Record<keyof typeof STATS, DefenseStat>;

/**
 * Whole games, or the first quarter of them.
 *
 * The panel follows the MARKET on screen: a first-quarter market is read
 * against what the defense concedes in the first quarter, or the panel answers
 * a different question from the rest of the page.
 */
export type DefensePeriod = "game" | "q1";

/**
 * What this opponent has allowed to this position per first quarter, picking
 * the column the position is actually measured on.
 *
 * ONE PLACE DECIDES, and it is the same place the RANK's basis is decided —
 * `rankBasis` below, which mirrors `RANK_METRICS` in `worker/core/splits.py`.
 * Rushing for QB and RB, receiving for WR and TE. The view hands over both
 * columns precisely so this rule is not written a third time in SQL.
 *
 * AN OBSERVATION, NEVER AN ORDERING. There is no adjusted or ranked sibling
 * because a first-quarter rate was measured not to predict itself (migration
 * 0070). A caller may print this number; it must not sort a board by it or
 * colour it good-and-bad, which would restate it as the rank it deliberately
 * is not.
 *
 * Null means the cutoff has no rated games behind it — week 1, or a defense
 * whose games are not derived — and reads as "—", not as zero conceded.
 */
export function opponentQ1Allowed(
  row: {
    positionGroup: PositionGroup | null;
    opponentQ1RushYardsAllowedPg: number | null;
    opponentQ1RecYardsAllowedPg: number | null;
  },
): { value: number; label: string } | null {
  if (!row.positionGroup) return null;
  const basis = rankBasis(row.positionGroup);
  const value =
    basis.key === "rush"
      ? row.opponentQ1RushYardsAllowedPg
      : row.opponentQ1RecYardsAllowedPg;
  if (value === null) return null;
  return {
    value,
    label: basis.key === "rush" ? "rush yds" : "rec yds",
  };
}

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
export function defenseStatsFor(
  position: PositionGroup,
  period: DefensePeriod = "game",
): DefenseStat[] {
  // The SAME columns for either period, picked from the matching table, so a
  // position cannot be shown one set of columns whole-game and another in the
  // quarter. The position rule above stays the single place it is decided.
  const s = period === "q1" ? Q1_STATS : STATS;
  switch (position) {
    case "QB":
      return [s.rush_attempts, s.rush_yards, s.rush_tds];
    case "RB":
      return [
        s.rush_attempts,
        s.rush_yards,
        s.receptions,
        s.rec_yards,
        s.total_tds,
      ];
    default:
      return [s.targets, s.receptions, s.rec_yards, s.rec_tds];
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
  // A first-quarter market highlights the first-quarter column. Stripping the
  // prefix and re-entering the same switch keeps ONE mapping from market to
  // defensive stat, so a market that has no defensive counterpart whole-game —
  // the passing ones — still has none in the quarter.
  if (statColumn.startsWith("q1_")) {
    const parent = defenseStatForMarket(statColumn.slice(3));
    if (!parent) return null;
    return (
      Object.values(Q1_STATS).find((stat) => stat.key === `q1_${parent.key}`) ??
      null
    );
  }

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

/** A matchup's difficulty, as one word plus how to colour it. */
export type MatchupBand = {
  key: "tough" | "middling" | "soft";
  label: string;
  /** Semantic tone from the reader's point of view, not the defense's. */
  tone: "positive" | "negative" | "neutral";
};

/**
 * Turn a rank into soft / middling / tough, ONCE.
 *
 * THIS EXISTS BECAUSE DUPLICATING IT PRODUCED A WRONG BADGE. The thirds test
 * lived inline in both the defense-detail panel and the weekly-targets row,
 * each deciding independently which end of the scale was soft. When the rank
 * convention flipped, `matchupSoftness` and the copy were updated and these two
 * were not — so a defense ranked 104 of 136 rendered as "104 of 136" beside a
 * red "TOUGH MATCHUP" pill. Both numbers on screen, agreeing with each other,
 * and the label contradicting both.
 *
 * The tone is from the READER's point of view: a soft defense is good news for
 * the player they are looking at, so soft is positive. That is the opposite of
 * how a defensive coordinator would colour it, and it is what this product is
 * for.
 */
export function matchupBand(rank: number, ranked: number): MatchupBand | null {
  const softness = matchupSoftness(rank, ranked);
  if (softness === null) return null;
  if (softness >= 2 / 3) {
    return { key: "soft", label: "soft matchup", tone: "positive" };
  }
  if (softness <= 1 / 3) {
    return { key: "tough", label: "tough matchup", tone: "negative" };
  }
  return { key: "middling", label: "middling", tone: "neutral" };
}

/**
 * How SOFT a matchup is, as a 0..1 fraction — 0 is the toughest defense in the
 * field, 1 the most generous.
 *
 * Named for what it means rather than for the number it is derived from.
 * `rank_vs_position` now counts 1 = the BEST defense, so a fraction that simply
 * tracked the rank would fill a bar from the wrong end, and a helper called
 * `rankFraction` that silently inverted would be a trap for the next reader.
 * Callers colour a bar with this; they should not have to remember which way
 * the underlying rank runs.
 */
export function matchupSoftness(rank: number, ranked: number): number | null {
  if (ranked <= 1 || rank < 1) return null;
  // Rank 1 is the BEST defense, so softness rises WITH the rank. Note this is
  // arithmetically what the old `rankFraction` computed — under the previous
  // convention the same expression meant the opposite thing. Renaming it was
  // the point: the formula is unchanged and its meaning is not, which is
  // precisely the kind of flip that survives a type check.
  return Math.min(Math.max((rank - 1) / (ranked - 1), 0), 1);
}

function sumOrNull(a: number | null, b: number | null): number | null {
  if (a === null && b === null) return null;
  return (a ?? 0) + (b ?? 0);
}
