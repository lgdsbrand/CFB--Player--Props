/**
 * Usage share — how much of his own offence a player is (migration 0071).
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * The client asked for "usage % or target and run share %" beside the line and
 * the recent hit rate. It answers what those two leave open: nine targets means
 * one thing at a 30% share and another at 12%, and a back with a 68% rush share
 * is a different bet from a committee member with 31%.
 *
 * THE SHARE FOLLOWS THE MARKET, NOT ONLY THE POSITION, which is the same rule
 * `defenseStatForMarket` follows next to it. A running back's receptions row
 * wants his target share and his rush-yards row wants his carries; showing one
 * number for "the RB" would be right on one of those rows and misleading on the
 * other.
 *
 * A MEAN OF PER-GAME SHARES, NOT A SHARE OF THE WINDOW'S TOTALS. The stored
 * column is a per-game ratio, so the totals it came from are not on the row and
 * cannot be re-summed here. The two differ by a point or so — the mean weights
 * a 22-target game the same as a 38-target one — and the cheaper definition was
 * taken deliberately over storing three more denominators for it. Any caption
 * should say "per game" rather than imply a season total.
 *
 * EVERY SUMMARY CARRIES ITS DENOMINATOR, for the reason `lib/core/splits.ts`
 * gives: college target share is withheld on about one team-game in seven where
 * attribution is incomplete, so a five-game window can easily rest on three
 * games, and a percentage shown without its sample invites reading it as a
 * settled fact about the player.
 */

// Relative, with the extension: `lib/core` is imported both by the Next bundler
// and by Node's test runner, and only the bundler understands `@/*`.
import { rankBasis } from "./defense-view.ts";
import type { PlayerGameLogRow, PositionGroup } from "./types.ts";

/** Which of a player's three shares a column is showing. */
export type UsageKind = "target" | "rush" | "snap";

export type UsageStat = {
  kind: UsageKind;
  /** Full label, for a panel. */
  label: string;
  /** Compact heading, for a board column. */
  short: string;
  /** What the denominator is, for a tooltip. Never omitted. */
  hint: string;
  /**
   * The denominator as a plural noun, for a sentence: "31% of his team's
   * TARGETS". Carried on the stat rather than switched on `kind` at each call
   * site, so a fourth share added here cannot be described as one of these
   * three by a surface that never heard of it.
   */
  noun: string;
  value: (row: PlayerGameLogRow) => number | null;
};

const STATS = {
  target: {
    kind: "target",
    label: "Target share",
    short: "TGT%",
    noun: "targets",
    hint:
      "Share of his team's targets in each game, averaged over the window. " +
      "Withheld for a game whose target attribution is incomplete.",
    value: (row) => row.targetShare,
  },
  rush: {
    kind: "rush",
    label: "Rush share",
    short: "RUSH%",
    noun: "carries",
    hint: "Share of his team's rush attempts in each game, averaged over the window.",
    value: (row) => row.rushShare,
  },
  snap: {
    kind: "snap",
    label: "Snap share",
    short: "SNAP%",
    noun: "offensive snaps",
    hint:
      "Share of his team's offensive snaps in each game, averaged over the " +
      "window. NFL only — college has no snap source.",
    value: (row) => row.snapShare,
  },
} as const satisfies Record<UsageKind, UsageStat>;

/**
 * The shares worth showing on a player's page, most telling first.
 *
 * A QB GETS SNAPS AND CARRIES, NOT TARGETS. His target share is zero by
 * construction and would be a column of noughts presented as a usage figure.
 * Snap share is the one that moves for him — it is how a mid-game hook or a
 * split backfield package shows up — and his rush share is the mobile-quarterback
 * read the rushing markets are priced on.
 *
 * SNAP SHARE IS LISTED FOR EVERY POSITION EVEN THOUGH COLLEGE HAS NONE. A
 * missing value renders as "—" with its sample at zero, which says "no source"
 * in the same place a reader is already looking; branching the list on sport
 * would hide a column on one board and leave no trace that it exists.
 */
export function usageStatsFor(position: PositionGroup): UsageStat[] {
  switch (position) {
    case "QB":
      return [STATS.snap, STATS.rush];
    case "RB":
      return [STATS.rush, STATS.target, STATS.snap];
    default:
      return [STATS.target, STATS.snap];
  }
}

/**
 * The share a market's stat is read against, if there is one.
 *
 * Returns null rather than a near-miss for the passing markets: a quarterback's
 * pass yards have no usage denominator on this row — his attempts ARE the team's
 * — and printing his snap share under a "pass yards" heading would answer a
 * question nobody asked while looking like an answer to the one they did.
 */
export function usageStatForMarket(
  statColumn: string,
  position?: PositionGroup | null,
): UsageStat | null {
  // A first-quarter market reads against the same share as its parent. The
  // stored shares are whole-game — `q1_targets` exists on the row but no
  // first-quarter TEAM total does — so a Q1 row shows the player's whole-game
  // role, which is what it means. Stripping the prefix keeps that decision in
  // one place instead of a second switch that could drift from this one.
  if (statColumn.startsWith("q1_")) {
    return usageStatForMarket(statColumn.slice(3), position);
  }

  switch (statColumn) {
    case "rush_attempts":
    case "rush_yards":
    case "rush_tds":
      return STATS.rush;
    case "targets":
    case "receptions":
    case "rec_yards":
    case "rec_tds":
      return STATS.target;

    // ANYTIME TD IS THE ONE MARKET WHOSE SHARE DEPENDS ON THE POSITION, because
    // it is the one market every position plays. A back scores off carries and
    // a receiver off targets, so the same column means two different
    // denominators — and a single choice would be wrong for half the board on
    // the market that draws the most attention of any.
    //
    // THROUGH `rankBasis`, not a switch of its own. That function already owns
    // "rushing for QB and RB, receiving for WR and TE" for this codebase, and
    // it mirrors `RANK_METRICS` in `worker/core/splits.py`. Restating the rule
    // here would make a fourth copy of a decision that has drifted before.
    case "offensive_tds":
      if (!position) return null;
      return rankBasis(position).key === "rush" ? STATS.rush : STATS.target;

    default:
      return null;
  }
}

export type UsageSummary = {
  stat: UsageStat;
  /** Mean of the per-game shares that exist, 0-1. */
  share: number;
  /** Games in the window that carried a share — the denominator to show. */
  games: number;
  /** Games in the window that did not. Non-zero is worth a caption. */
  missing: number;
};

/**
 * Mean share over the most recent `window` games.
 *
 * `games` MUST ALREADY BE ORDERED most-recent-first and already cut to the weeks
 * the surface may show — the same list the hit rate is built from, so the two
 * figures beside each other always describe the same games. `orderGames` in
 * `lib/core/hit-rate.ts` is what establishes that order.
 *
 * Returns null when no game in the window carries this share, which is a real
 * and common answer: a college player has no snap share at all, and a receiver
 * can lose a short window entirely to the target-attribution guard. Null reads
 * as "no data", never as zero usage.
 */
export function summariseUsage(
  games: PlayerGameLogRow[],
  stat: UsageStat,
  window: number,
): UsageSummary | null {
  const considered = games.slice(0, Math.max(0, window));
  const values = considered
    .map((game) => stat.value(game))
    .filter((value): value is number => value !== null);

  if (values.length === 0) return null;

  const total = values.reduce((sum, value) => sum + value, 0);
  return {
    stat,
    share: total / values.length,
    games: values.length,
    missing: considered.length - values.length,
  };
}

/**
 * The usage figure for one board row, market and all.
 *
 * The card's counterpart to `summariseRow` in `board-view.ts`, and named beside
 * it so the two surfaces cannot end up mapping market to share differently.
 * Null on a market with no denominator, and null on a window whose games carry
 * no share — both of which the caller renders as nothing rather than as zero.
 */
export function usageForRow(
  market: { statColumn: string } | undefined,
  games: PlayerGameLogRow[],
  window: number,
  position?: PositionGroup | null,
): UsageSummary | null {
  if (!market) return null;
  const stat = usageStatForMarket(market.statColumn, position);
  if (!stat) return null;
  return summariseUsage(games, stat, window);
}

/**
 * A share as a whole percent.
 *
 * NO DECIMAL PLACE, on purpose. Snap share arrives from nflverse rounded to two
 * decimals, so a tenth of a percent would be precision the source never had;
 * and a target share resting on four games does not support one either.
 */
export function formatUsageShare(share: number | null): string {
  if (share === null) return "—";
  return `${Math.round(share * 100)}%`;
}
