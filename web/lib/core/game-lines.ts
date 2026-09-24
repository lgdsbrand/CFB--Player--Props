/**
 * Game lines — the pure half of the game model's screens (CLAUDE.md §11, G1).
 *
 * Everything here describes the MARKET, not a model: consensus and sharp
 * spreads, how far they moved, and how each team has done against the number.
 * The game model's own projection lands beside these in G4; nothing in this
 * file may start to look like one before then.
 *
 * ONE ORIENTATION RULE. Every spread in this file is stored from the HOME
 * team's perspective, negative = home favoured, exactly as `game_odds.line`
 * and `game_lines.spread` store it. Labels flip it to the favourite at the
 * last moment, and nowhere else.
 */

export type OddsMarket = "h2h" | "spreads" | "totals";

export type MarketRole = "sharp" | "exchange" | "retail" | "other";

/** One row of `v_game_odds_summary`: every captured book on one game market. */
export interface GameMarketSummary {
  gameId: number;
  market: OddsMarket;
  books: number;
  consensusLine: number | null;
  consensusFirstLine: number | null;
  consensusFair: number | null;
  sharpLine: number | null;
  sharpFirstLine: number | null;
  sharpFair: number | null;
  sharpFirstFair: number | null;
  exchangeLine: number | null;
  exchangeFair: number | null;
  retailLine: number | null;
  retailFirstLine: number | null;
  retailFair: number | null;
  sharpBooks: number;
  exchangeBooks: number;
  retailBooks: number;
  firstSeenAt: string | null;
  lastMovedAt: string | null;
}

export type GameOddsSummary = Partial<Record<OddsMarket, GameMarketSummary>>;

/** One book's current price on one game market, from `v_game_odds_current`. */
export interface BookOdds {
  sportsbookKey: string;
  sportsbookName: string;
  role: MarketRole;
  market: OddsMarket;
  line: number | null;
  homePrice: number | null;
  awayPrice: number | null;
  overPrice: number | null;
  underPrice: number | null;
  firstLine: number | null;
  capturedAt: string;
  firstCapturedAt: string;
}

/**
 * A number of points as a book would print it. Medians across an even number
 * of books can land on a quarter point, and rounding that to a half would
 * print a line no book offered, so quarters keep two decimals.
 */
export function formatPoints(points: number): string {
  const abs = Math.abs(points);
  return Number.isInteger(abs * 2) ? abs.toFixed(1) : abs.toFixed(2);
}

/**
 * "UGA -7.0", "PK", or null when there is no line. The favourite is named,
 * because a bare "-7.0" on a row with two teams asks the reader to know which
 * side the convention sits on.
 */
export function spreadLabel(
  homeLine: number | null,
  home: string,
  away: string,
): string | null {
  if (homeLine === null) return null;
  if (homeLine === 0) return "PK";
  return homeLine < 0
    ? `${home} -${formatPoints(homeLine)}`
    : `${away} -${formatPoints(homeLine)}`;
}

/**
 * Movement since the first price we saw, in points, signed from the home
 * team's perspective (a negative move means the home team got MORE favoured).
 * Null when either end is missing or nothing moved.
 */
export function lineMove(now: number | null, first: number | null): number | null {
  if (now === null || first === null) return null;
  const delta = Math.round((now - first) * 100) / 100;
  return delta === 0 ? null : delta;
}

/**
 * Which team a spread move went TOWARD, for a caption like "moved 1.5 to UGA".
 * The home team is "toward" when its line fell.
 */
export function spreadMoveToward(
  delta: number | null,
  home: string,
  away: string,
): { points: string; team: string } | null {
  if (delta === null) return null;
  return {
    points: formatPoints(delta),
    team: delta < 0 ? home : away,
  };
}

/** A total's move: "up 2.0" / "down 1.5". */
export function totalMoveLabel(delta: number | null): string | null {
  if (delta === null) return null;
  return `${delta > 0 ? "up" : "down"} ${formatPoints(delta)}`;
}

/** A vig-free probability as a whole percentage, or an em dash. */
export function formatFair(probability: number | null): string {
  if (probability === null || !Number.isFinite(probability)) return "—";
  return `${Math.round(probability * 100)}%`;
}

/**
 * Sharp against retail on the spread, in points and home perspective. A
 * positive gap means retail has the HOME team as a bigger underdog (or smaller
 * favourite) than Pinnacle — i.e. the home side is the one retail is offering
 * cheaper. Null when either side has no line.
 */
export function sharpRetailGap(summary: GameMarketSummary | undefined): number | null {
  if (!summary || summary.sharpLine === null || summary.retailLine === null) {
    return null;
  }
  const gap = Math.round((summary.retailLine - summary.sharpLine) * 100) / 100;
  return gap === 0 ? null : gap;
}

// =============================================================================
// Against the spread and the total
// =============================================================================

/** A played game with the line it is graded against. */
export interface GradedGame {
  gameId: number;
  season: number;
  week: number;
  startDate: string | null;
  homeTeamId: number;
  awayTeamId: number;
  homePoints: number;
  awayPoints: number;
  /** Home perspective, negative = home favoured. Null: no line on record. */
  homeSpread: number | null;
  total: number | null;
}

export type AtsResult = "W" | "L" | "P";
export type TotalResult = "O" | "U" | "P";

/** Did `teamId` cover? Null when the game carries no spread or the team is not in it. */
export function atsResult(game: GradedGame, teamId: number): AtsResult | null {
  if (game.homeSpread === null) return null;
  let margin: number;
  let spread: number;
  if (teamId === game.homeTeamId) {
    margin = game.homePoints - game.awayPoints;
    spread = game.homeSpread;
  } else if (teamId === game.awayTeamId) {
    margin = game.awayPoints - game.homePoints;
    spread = -game.homeSpread;
  } else {
    return null;
  }
  const result = margin + spread;
  if (result > 0) return "W";
  if (result < 0) return "L";
  return "P";
}

export function totalResult(game: GradedGame): TotalResult | null {
  if (game.total === null) return null;
  const points = game.homePoints + game.awayPoints;
  if (points > game.total) return "O";
  if (points < game.total) return "U";
  return "P";
}

export interface TeamRecord {
  straightUp: { w: number; l: number };
  ats: { w: number; l: number; p: number };
  totals: { o: number; u: number; p: number };
  /** Games played with no line on record, so ungraded against it. */
  noLine: number;
}

/** One team's straight-up, ATS and over/under record over the given games. */
export function teamRecord(games: GradedGame[], teamId: number): TeamRecord {
  const record: TeamRecord = {
    straightUp: { w: 0, l: 0 },
    ats: { w: 0, l: 0, p: 0 },
    totals: { o: 0, u: 0, p: 0 },
    noLine: 0,
  };
  for (const game of games) {
    const home = teamId === game.homeTeamId;
    if (!home && teamId !== game.awayTeamId) continue;
    const margin = home
      ? game.homePoints - game.awayPoints
      : game.awayPoints - game.homePoints;
    // College football has no ties since 1996; a level score is bad data.
    if (margin > 0) record.straightUp.w += 1;
    else if (margin < 0) record.straightUp.l += 1;

    const ats = atsResult(game, teamId);
    if (ats === null) record.noLine += 1;
    else if (ats === "W") record.ats.w += 1;
    else if (ats === "L") record.ats.l += 1;
    else record.ats.p += 1;

    const total = totalResult(game);
    if (total === "O") record.totals.o += 1;
    else if (total === "U") record.totals.u += 1;
    else if (total === "P") record.totals.p += 1;
  }
  return record;
}

/** "3-1" or "3-1-1" — a push is shown only when there was one. */
export function formatRecord(w: number, l: number, p = 0): string {
  return p > 0 ? `${w}-${l}-${p}` : `${w}-${l}`;
}
