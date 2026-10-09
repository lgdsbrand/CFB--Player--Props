/**
 * Game lines — the pure half of the game model's screens (CLAUDE.md §11, G1).
 *
 * The MARKET's numbers — consensus and sharp spreads, how far they moved,
 * how each team has done against the number — and, at the bottom, the game
 * model's projection and its edge against one book (G4, and since 2026-10-06
 * shown publicly at the user's instruction; CLAUDE.md §11).
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
  /** Full game, first half or first quarter. The same book prices each apart. */
  period: ModelPeriod;
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
  /**
   * The splits the game page's records add (client, 2026-10-06). Optional
   * because head-to-head reads do not need them; a game without them simply
   * counts toward no split.
   */
  conferenceGame?: boolean | null;
  neutralSite?: boolean;
  /**
   * Each side's poll rank ENTERING that game's week (the week-N poll is the
   * one published before week N; migration 0032), so "against ranked teams"
   * means ranked when they played, not ranked now.
   */
  homeRank?: number | null;
  awayRank?: number | null;
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
  conference: { w: number; l: number };
  home: { w: number; l: number };
  away: { w: number; l: number };
  neutral: { w: number; l: number };
  /** Against an opponent ranked in the poll entering that game's week. */
  vsRanked: { w: number; l: number };
  ats: { w: number; l: number; p: number };
  totals: { o: number; u: number; p: number };
  /** Games played with no line on record, so ungraded against it. */
  noLine: number;
}

/** One team's straight-up, ATS and over/under record over the given games. */
export function teamRecord(games: GradedGame[], teamId: number): TeamRecord {
  const record: TeamRecord = {
    straightUp: { w: 0, l: 0 },
    conference: { w: 0, l: 0 },
    home: { w: 0, l: 0 },
    away: { w: 0, l: 0 },
    neutral: { w: 0, l: 0 },
    vsRanked: { w: 0, l: 0 },
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
    const tally = (bucket: { w: number; l: number }) => {
      if (margin > 0) bucket.w += 1;
      else if (margin < 0) bucket.l += 1;
    };
    tally(record.straightUp);
    if (game.conferenceGame) tally(record.conference);
    if (game.neutralSite) tally(record.neutral);
    else if (game.neutralSite === false) tally(home ? record.home : record.away);
    const opponentRank = home ? game.awayRank : game.homeRank;
    if (opponentRank !== null && opponentRank !== undefined) tally(record.vsRanked);

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

// =============================================================================
// The game model's fair line and its edge (CLAUDE.md §11)
// =============================================================================
// The fair line has been on the site since G4. THE EDGE JOINED IT ON
// 2026-10-06, at the user's instruction and against the G4 decision to show
// the number only (CLAUDE.md §11 records both). Its shadow picks are still a
// private, frozen test (migration 0078) that nothing on the site reads; the
// edge below is computed from the same quote and probability, so the two
// never disagree about a game.

export type ModelPeriod = "full" | "h1" | "q1";

export interface ModelRange {
  mean: number;
  p10: number;
  p90: number;
}

/**
 * The model set against ONE book's full-game price (migration 0082).
 *
 * Written by `run_game_model` from the quote its shadow picks use (Pinnacle,
 * then DraftKings, then FanDuel). `first` is the home side of a spread and
 * the over of a total; `line` is from the HOME team's side, like every spread
 * in this file. The model's probability is its share of simulated outcomes
 * at exactly this line, which the site could not reproduce from the mean and
 * range, so it is stored rather than derived here — and stored CALIBRATED
 * since 2026-10-07 (CALIBRATION_SLOPE in worker/core/game_picks.py): pulled
 * toward 50% by how often the model's past confidence actually came in. So a
 * wide gap between the two lines can honestly be a small edge.
 */
export interface PricedMarket {
  bookKey: string | null;
  bookName: string | null;
  line: number;
  firstPrice: number;
  secondPrice: number;
  modelFirstProb: number;
}

/** One row of `game_projections`. Margin is HOME minus AWAY. */
export interface GameProjection {
  gameId: number;
  modelVersion: string;
  evidencePhase: "early" | "later";
  pHomeWin: number;
  madeAt: string;
  periods: Record<ModelPeriod, { margin: ModelRange; total: ModelRange }>;
  /** Null when no listed book had a usable price at the last run. */
  spread: PricedMarket | null;
  total: PricedMarket | null;
  /**
   * A team in this game has no previous season in the model's data — in
   * practice it is new to FBS (migration 0083). The site then shows none of
   * the model's numbers for the game, only the book's; user decision,
   * 2026-10-06, after NDSU and Sacramento State topped the table at +47% and
   * +39% on a season the model never saw.
   */
  missingPriorSeason: boolean;
}

/** Why a flagged game shows no model numbers, in the reader's terms. */
export const MISSING_PRIOR_NOTE = "new to FBS";

/** An American price's implied probability, vig included. */
export function impliedProbability(price: number): number {
  return price < 0 ? -price / (-price + 100) : 100 / (price + 100);
}

/**
 * The vig-free probability of the FIRST side of a two-way price, by the
 * proportional method — the same arithmetic as `devig` in
 * `worker/core/game_picks.py`, pinned to the same numbers by the tests.
 */
export function devigFirst(firstPrice: number, secondPrice: number): number {
  const a = impliedProbability(firstPrice);
  const b = impliedProbability(secondPrice);
  return a / (a + b);
}

/**
 * The edge on the side the model prefers (CLAUDE.md §6): model probability
 * minus the book's vig-free probability of the same side.
 *
 * WHY ONE SIDE AND NOT TWO. With the vig removed the two sides' edges are
 * exact mirrors, so the preferred side's is the non-negative one and the
 * other carries no information. A dead heat reads as the first side at 0.
 */
export function marketEdge(market: PricedMarket): {
  side: "first" | "second";
  edge: number;
  modelProb: number;
  bookProb: number;
} {
  const book = devigFirst(market.firstPrice, market.secondPrice);
  const edge = market.modelFirstProb - book;
  return edge >= 0
    ? { side: "first", edge, modelProb: market.modelFirstProb, bookProb: book }
    : {
        side: "second",
        edge: -edge,
        modelProb: 1 - market.modelFirstProb,
        bookProb: 1 - book,
      };
}

/**
 * One side of a spread as a bettor reads it: "TROY -10.0", "USM +10.0".
 * `homeLine` is the home team's handicap; the away side holds its mirror.
 */
export function spreadSideLabel(
  homeLine: number,
  side: "first" | "second",
  home: string,
  away: string,
): string {
  const line = side === "first" ? homeLine : -homeLine;
  if (line === 0) return `${side === "first" ? home : away} PK`;
  const sign = line > 0 ? "+" : "-";
  return `${side === "first" ? home : away} ${sign}${formatPoints(line)}`;
}

/**
 * The games in the Lines table's order: by the model's edge on one market,
 * largest first, or untouched (kickoff) when no edge order is asked for.
 *
 * A GAME WITH NO EDGE GOES LAST, in the order it arrived, rather than being
 * treated as an edge of zero: "not priced" and "priced at no edge" are
 * different facts, and sorting them together would scatter unpriced games
 * among the small edges. The sort is stable, so ties keep kickoff order.
 */
/**
 * Within this many points of the book's line, the model has no real opinion:
 * the table shows the edge but completed games grade it "Pass", in neither
 * tally (client, 2026-10-09: FIU -6.4 against -6.5).
 */
export const PASS_GAP = 1;

export type TableSide = {
  side: "first" | "second";
  /** Calibrated edge of THAT side; slightly negative when the juice outweighs it. */
  edge: number;
  /** The model's calibrated probability of that side. */
  modelProb: number;
  /** Points between our number and the book's line. */
  gap: number;
  /** Our number within PASS_GAP of the line. */
  pass: boolean;
};

/**
 * The side the games table shows: where OUR NUMBER sits against the book's
 * line (client, 2026-10-09). Book UTSA -7.5, ours UTSA -1.6 is USF +7.5.
 *
 * It used to be the side `marketEdge` favours, which compares probabilities.
 * The spread calibration flattens those to about 50%, so on a close call the
 * juice decided the side: at -103 UTSA / -113 USF the table said UTSA -7.5
 * +0.1% while our line said USF, on 26 of 76 priced games this season. The
 * edge is still the calibrated one (CLAUDE.md §6), now of the side the lines
 * name, so the two columns can no longer contradict each other.
 *
 * `model` is the margin (home minus away) for a spread, the total for a total.
 * Equal numbers fall back to the edge's side; the gap is then 0, a pass.
 */
export function tableSide(
  market: PricedMarket,
  kind: "spreads" | "totals",
  model: number,
): TableSide {
  const ours = kind === "spreads" ? -model : model;
  const gap = Math.round(Math.abs(ours - market.line) * 10) / 10;
  const firstByLines = kind === "spreads" ? ours < market.line : ours > market.line;
  const side = gap === 0 ? marketEdge(market).side : firstByLines ? "first" : "second";
  const book = devigFirst(market.firstPrice, market.secondPrice);
  const edge = side === "first" ? market.modelFirstProb - book : book - market.modelFirstProb;
  const modelProb = side === "first" ? market.modelFirstProb : 1 - market.modelFirstProb;
  return { side, edge, modelProb, gap, pass: gap < PASS_GAP };
}

/** The model number `tableSide` reads, from a projection. */
export function modelNumber(projection: GameProjection, kind: "spreads" | "totals"): number {
  return kind === "spreads" ? projection.periods.full.margin.mean : projection.periods.full.total.mean;
}

export function orderByEdge<G extends { gameId: number }>(
  games: G[],
  projections: Map<number, GameProjection>,
  order: "spread_edge" | "total_edge" | undefined,
): G[] {
  if (!order) return games;
  const edgeOf = (game: G): number | null => {
    const projection = projections.get(game.gameId);
    // Not shown, so not ranked: a hidden edge sorted to the top would leave
    // the table's first row reading "—".
    if (projection?.missingPriorSeason) return null;
    const kind = order === "spread_edge" ? "spreads" : "totals";
    const market = kind === "spreads" ? projection?.spread : projection?.total;
    return market && projection ? tableSide(market, kind, modelNumber(projection, kind)).edge : null;
  };
  const priced = games.filter((game) => edgeOf(game) !== null);
  const unpriced = games.filter((game) => edgeOf(game) === null);
  return [...priced.sort((a, b) => edgeOf(b)! - edgeOf(a)!), ...unpriced];
}

/**
 * Whether the side the games table showed (`tableSide`: where our number sits
 * against the line) won against the final score, or "pass" when our number
 * was within PASS_GAP of the line. For the completed games section (client,
 * 2026-10-08: "so we could go back and look"): the row as it stood at kickoff,
 * since `run_game_model` never re-projects a started game.
 */
export function tableSideResult(
  market: PricedMarket,
  kind: "spreads" | "totals",
  model: number,
  homePoints: number,
  awayPoints: number,
): "win" | "loss" | "push" | "pass" {
  const { side, pass } = tableSide(market, kind, model);
  if (pass) return "pass";
  const raw =
    kind === "spreads"
      ? homePoints - awayPoints + market.line
      : homePoints + awayPoints - market.line;
  const margin = side === "first" ? raw : -raw;
  return margin > 0 ? "win" : margin < 0 ? "loss" : "push";
}

/** "Over 51.5" / "Under 51.5". */
export function totalSideLabel(line: number, side: "first" | "second"): string {
  return `${side === "first" ? "Over" : "Under"} ${formatPoints(line)}`;
}

export const MODEL_PERIOD_LABELS: Record<ModelPeriod, string> = {
  full: "Full game",
  h1: "1st half",
  q1: "1st quarter",
};

/**
 * The model's margin as a fair spread naming the favourite: a projected home
 * win by 9.3 is "UGA -9.3". One decimal on purpose: a model number is not a
 * book line and should not look like one rounded to the half point.
 */
export function modelSpreadLabel(margin: number, home: string, away: string): string {
  const points = Math.abs(margin).toFixed(1);
  if (points === "0.0") return "PK";
  return margin > 0 ? `${home} -${points}` : `${away} -${points}`;
}

/** "ALA by 3 to UGA by 21": the 80% range of the margin in team terms. */
export function marginRangeLabel(
  range: ModelRange,
  home: string,
  away: string,
): string {
  const side = (margin: number) => {
    const points = Math.round(Math.abs(margin));
    if (points === 0) return "level";
    return `${margin > 0 ? home : away} by ${points}`;
  };
  return `${side(range.p10)} to ${side(range.p90)}`;
}

/** "38 to 62". */
export function totalRangeLabel(range: ModelRange): string {
  return `${Math.round(range.p10)} to ${Math.round(range.p90)}`;
}

/** The favourite and its win probability, e.g. { team: "UGA", p: 0.78 }. */
export function modelFavourite(
  pHomeWin: number,
  home: string,
  away: string,
): { team: string; p: number } {
  return pHomeWin >= 0.5 ? { team: home, p: pHomeWin } : { team: away, p: 1 - pHomeWin };
}

/**
 * The model's projected score (client, 2026-10-09: "our projected score isn't
 * displayed"). Not a second model: the expected margin and total already
 * stored, split back into the two teams' points, home = (total + margin) / 2.
 * Null for a game whose model numbers are hidden (a team new to FBS).
 */
export function projectedScore(
  projection: Pick<GameProjection, "periods" | "missingPriorSeason"> | null | undefined,
): { home: number; away: number } | null {
  if (!projection || projection.missingPriorSeason) return null;
  const { margin, total } = projection.periods.full;
  if (!Number.isFinite(margin.mean) || !Number.isFinite(total.mean)) return null;
  return {
    home: Math.round(((total.mean + margin.mean) / 2) * 10) / 10,
    away: Math.round(((total.mean - margin.mean) / 2) * 10) / 10,
  };
}
