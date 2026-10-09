import assert from "node:assert/strict";
import { test } from "node:test";

import {
  atsResult,
  devigFirst,
  formatFair,
  formatPoints,
  formatRecord,
  lineMove,
  marginRangeLabel,
  marketEdge,
  modelFavourite,
  modelSpreadLabel,
  orderByEdge,
  projectedScore,
  sharpRetailGap,
  spreadLabel,
  spreadMoveToward,
  spreadSideLabel,
  tableSideResult,
  teamRecord,
  totalMoveLabel,
  totalSideLabel,
  totalRangeLabel,
  totalResult,
  type GameMarketSummary,
  type GameProjection,
  type GradedGame,
  type PricedMarket,
} from "./game-lines.ts";

test("spread labels name the favourite from a home-perspective line", () => {
  assert.equal(spreadLabel(-7, "UGA", "CLEM"), "UGA -7.0");
  assert.equal(spreadLabel(3.5, "UGA", "CLEM"), "CLEM -3.5");
  assert.equal(spreadLabel(0, "UGA", "CLEM"), "PK");
  assert.equal(spreadLabel(null, "UGA", "CLEM"), null);
});

test("a median on a quarter point is printed as one, not rounded to a half", () => {
  assert.equal(formatPoints(-6.75), "6.75");
  assert.equal(formatPoints(51.5), "51.5");
  assert.equal(formatPoints(7), "7.0");
});

test("line moves are signed from the home side and ignore float noise", () => {
  assert.equal(lineMove(-7, -6.5), -0.5);
  assert.equal(lineMove(-6.5, -6.5), null);
  assert.equal(lineMove(null, -6.5), null);
  assert.equal(lineMove(0.1 + 0.2, 0.3), null);
  // Home line falling means the home team became a bigger favourite.
  assert.deepEqual(spreadMoveToward(-1.5, "UGA", "CLEM"), { points: "1.5", team: "UGA" });
  assert.deepEqual(spreadMoveToward(2, "UGA", "CLEM"), { points: "2.0", team: "CLEM" });
  assert.equal(totalMoveLabel(2), "up 2.0");
  assert.equal(totalMoveLabel(-1.5), "down 1.5");
});

test("fair probabilities print as whole percentages", () => {
  assert.equal(formatFair(0.6249), "62%");
  assert.equal(formatFair(null), "—");
});

function summary(overrides: Partial<GameMarketSummary>): GameMarketSummary {
  return {
    gameId: 1,
    market: "spreads",
    books: 10,
    consensusLine: -7,
    consensusFirstLine: -6.5,
    consensusFair: 0.5,
    sharpLine: -7,
    sharpFirstLine: -6.5,
    sharpFair: 0.5,
    sharpFirstFair: 0.5,
    exchangeLine: -7,
    exchangeFair: 0.5,
    retailLine: -6.5,
    retailFirstLine: -6.5,
    retailFair: 0.5,
    sharpBooks: 1,
    exchangeBooks: 3,
    retailBooks: 4,
    firstSeenAt: null,
    lastMovedAt: null,
    ...overrides,
  };
}

test("the sharp-retail gap is retail minus sharp, home perspective", () => {
  // Retail has home at -6.5, Pinnacle at -7: retail is cheaper on home.
  assert.equal(sharpRetailGap(summary({})), 0.5);
  assert.equal(sharpRetailGap(summary({ retailLine: -7 })), null);
  assert.equal(sharpRetailGap(summary({ sharpLine: null })), null);
  assert.equal(sharpRetailGap(undefined), null);
});

function game(overrides: Partial<GradedGame>): GradedGame {
  return {
    gameId: 1,
    season: 2026,
    week: 1,
    startDate: null,
    homeTeamId: 10,
    awayTeamId: 20,
    homePoints: 28,
    awayPoints: 21,
    homeSpread: -3.5,
    total: 50.5,
    ...overrides,
  };
}

test("ATS grading covers both sides and pushes", () => {
  // Home won by 7 laying 3.5: home covers, away does not.
  assert.equal(atsResult(game({}), 10), "W");
  assert.equal(atsResult(game({}), 20), "L");
  // Home won by 7 laying 7: push both ways.
  assert.equal(atsResult(game({ homeSpread: -7 }), 10), "P");
  assert.equal(atsResult(game({ homeSpread: -7 }), 20), "P");
  // Away was the favourite (home +10) and won by 3: away did not cover.
  const upset = game({ homePoints: 17, awayPoints: 20, homeSpread: 10 });
  assert.equal(atsResult(upset, 20), "L");
  assert.equal(atsResult(upset, 10), "W");
  assert.equal(atsResult(game({ homeSpread: null }), 10), null);
  assert.equal(atsResult(game({}), 99), null);
});

test("totals grade over, under and push", () => {
  assert.equal(totalResult(game({})), "U"); // 49 < 50.5
  assert.equal(totalResult(game({ total: 45 })), "O");
  assert.equal(totalResult(game({ total: 49 })), "P");
  assert.equal(totalResult(game({ total: null })), null);
});

test("a team record counts games without a line separately", () => {
  const games = [
    game({ gameId: 1 }), // home W, covers, under
    game({ gameId: 2, homeTeamId: 30, awayTeamId: 10, homePoints: 35, awayPoints: 14, homeSpread: -10, total: 45 }),
    game({ gameId: 3, homeSpread: null, total: null }),
    game({ gameId: 4, homeTeamId: 40, awayTeamId: 50 }), // not this team
  ];
  const record = teamRecord(games, 10);
  assert.deepEqual(record.straightUp, { w: 2, l: 1 });
  assert.deepEqual(record.ats, { w: 1, l: 1, p: 0 });
  assert.deepEqual(record.totals, { o: 1, u: 1, p: 0 });
  assert.equal(record.noLine, 1);
  assert.equal(formatRecord(3, 1), "3-1");
  assert.equal(formatRecord(3, 1, 1), "3-1-1");
});

test("record splits: conference, venue and against teams ranked at the time", () => {
  // Team 10. Defaults from `game()`: 10 hosts 20 and wins.
  const games = [
    game({ gameId: 1, conferenceGame: true, neutralSite: false, awayRank: 12 }), // conf home W vs #12
    game({ gameId: 2, homeTeamId: 30, awayTeamId: 10, homePoints: 35, awayPoints: 14, conferenceGame: false, neutralSite: false, homeRank: null }), // away L
    game({ gameId: 3, neutralSite: true, homeRank: 5, awayRank: 8 }), // neutral W; 10's OWN rank must not count
    game({ gameId: 4 }), // no split fields at all: counts toward none of them
  ];
  const record = teamRecord(games, 10);
  assert.deepEqual(record.straightUp, { w: 3, l: 1 });
  assert.deepEqual(record.conference, { w: 1, l: 0 });
  assert.deepEqual(record.home, { w: 1, l: 0 });
  assert.deepEqual(record.away, { w: 0, l: 1 });
  assert.deepEqual(record.neutral, { w: 1, l: 0 });
  assert.deepEqual(record.vsRanked, { w: 2, l: 0 });
});

test("the model's fair spread names the favourite and keeps one decimal", () => {
  assert.equal(modelSpreadLabel(9.34, "UGA", "CLEM"), "UGA -9.3");
  assert.equal(modelSpreadLabel(-3, "UGA", "CLEM"), "CLEM -3.0");
  assert.equal(modelSpreadLabel(0.02, "UGA", "CLEM"), "PK");
});

test("the model's ranges read in team terms", () => {
  const margin = { mean: 9, p10: -3.4, p90: 21.2 };
  assert.equal(marginRangeLabel(margin, "UGA", "CLEM"), "CLEM by 3 to UGA by 21");
  assert.equal(marginRangeLabel({ mean: 0, p10: 0.2, p90: 7 }, "UGA", "CLEM"), "level to UGA by 7");
  assert.equal(totalRangeLabel({ mean: 50, p10: 37.6, p90: 62.2 }), "38 to 62");
  assert.deepEqual(modelFavourite(0.3, "UGA", "CLEM"), { team: "CLEM", p: 0.7 });
});

// -----------------------------------------------------------------------------
// The game model's edge against one book (migration 0082)
// -----------------------------------------------------------------------------

const close = (a: number, b: number, tol = 1e-12) =>
  assert.ok(Math.abs(a - b) < tol, `${a} vs ${b}`);

test("devig matches the worker's proportional method on the same prices", () => {
  // The vectors of `test_devig_is_proportional` in worker/tests/test_game_picks.py.
  close(devigFirst(-110, -110), 0.5);
  close(devigFirst(-150, 130), 0.6 / (0.6 + 100 / 230));
});

function priced(overrides: Partial<PricedMarket> = {}): PricedMarket {
  return {
    bookKey: "pinnacle",
    bookName: "Pinnacle",
    line: -10,
    firstPrice: -105,
    secondPrice: -115,
    modelFirstProb: 0.655,
    ...overrides,
  };
}

test("the edge is model minus vig-free book, on the side the model prefers", () => {
  const home = marketEdge(priced());
  assert.equal(home.side, "first");
  close(home.bookProb, devigFirst(-105, -115));
  close(home.edge, 0.655 - devigFirst(-105, -115));

  const away = marketEdge(priced({ modelFirstProb: 0.331 }));
  assert.equal(away.side, "second");
  close(away.modelProb, 1 - 0.331);
  close(away.edge, 1 - 0.331 - (1 - devigFirst(-105, -115)));
  assert.ok(away.edge > 0);
});

test("the two sides' edges mirror, so the preferred side's is never negative", () => {
  for (const p of [0.01, 0.3, 0.49, 0.5, 0.51, 0.7, 0.99]) {
    assert.ok(marketEdge(priced({ modelFirstProb: p })).edge >= 0, String(p));
  }
});

test("a spread side is named from the home line, with the away side mirrored", () => {
  assert.equal(spreadSideLabel(-10, "first", "TROY", "USM"), "TROY -10.0");
  assert.equal(spreadSideLabel(-10, "second", "TROY", "USM"), "USM +10.0");
  assert.equal(spreadSideLabel(3, "first", "KENN", "JXST"), "KENN +3.0");
  assert.equal(spreadSideLabel(0, "second", "KENN", "JXST"), "JXST PK");
});

test("a total side reads Over or Under the line", () => {
  assert.equal(totalSideLabel(51.5, "first"), "Over 51.5");
  assert.equal(totalSideLabel(51.5, "second"), "Under 51.5");
});

test("edge order puts the biggest edge first and unpriced games last, in kickoff order", () => {
  const projection = (gameId: number, spread: PricedMarket | null): GameProjection => ({
    gameId,
    modelVersion: "ratings-v1",
    evidencePhase: "later",
    pHomeWin: 0.5,
    madeAt: "2026-10-06T23:30:00Z",
    periods: {
      full: { margin: { mean: 0, p10: 0, p90: 0 }, total: { mean: 0, p10: 0, p90: 0 } },
      h1: { margin: { mean: 0, p10: 0, p90: 0 }, total: { mean: 0, p10: 0, p90: 0 } },
      q1: { margin: { mean: 0, p10: 0, p90: 0 }, total: { mean: 0, p10: 0, p90: 0 } },
    },
    spread,
    total: null,
    missingPriorSeason: gameId === 4,
  });
  // Kickoff order 1..5. Even prices, so each edge is |p - 0.5|.
  const games = [1, 2, 3, 4, 5].map((gameId) => ({ gameId }));
  const projections = new Map([
    [1, projection(1, priced({ firstPrice: -110, secondPrice: -110, modelFirstProb: 0.52 }))],
    [2, projection(2, null)],
    [3, projection(3, priced({ firstPrice: -110, secondPrice: -110, modelFirstProb: 0.3 }))],
    [5, projection(5, priced({ firstPrice: -110, secondPrice: -110, modelFirstProb: 0.6 }))],
    // The largest edge of all, but flagged (a team new to FBS): not shown, so
    // it sorts with the unpriced games instead of heading the table.
    [4, projection(4, priced({ firstPrice: -110, secondPrice: -110, modelFirstProb: 0.95 }))],
  ]);
  assert.deepEqual(
    orderByEdge(games, projections, "spread_edge").map((g) => g.gameId),
    [3, 5, 1, 2, 4],
  );
  // No total priced anywhere: every game is "unpriced" and keeps kickoff order.
  assert.deepEqual(
    orderByEdge(games, projections, "total_edge").map((g) => g.gameId),
    [1, 2, 3, 4, 5],
  );
  assert.equal(orderByEdge(games, projections, undefined), games);
});

test("the table's side is graded against the final score", () => {
  // Wed 10-07: FIU -6.5 at -112/-104, model 49.9% home -> the table showed
  // NMSU +6.5. FIU won 22-3, so that side lost.
  const fiu: PricedMarket = { bookKey: "pinnacle", bookName: "Pinnacle", line: -6.5, firstPrice: -112, secondPrice: -104, modelFirstProb: 0.49909 };
  assert.equal(tableSideResult(fiu, "spreads", 22, 3), "loss");
  // KENN +2.5, model 50.5% home: KENN lost 27-26, covered.
  const kenn: PricedMarket = { bookKey: "pinnacle", bookName: "Pinnacle", line: 2.5, firstPrice: 101, secondPrice: -117, modelFirstProb: 0.50484 };
  assert.equal(tableSideResult(kenn, "spreads", 26, 27), "win");
  const total: PricedMarket = { bookKey: "pinnacle", bookName: "Pinnacle", line: 50, firstPrice: -105, secondPrice: -113, modelFirstProb: 0.50554 };
  assert.equal(tableSideResult(total, "totals", 26, 27), "win");
  assert.equal(tableSideResult(total, "totals", 25, 25), "push");
});

test("projected score splits the margin and total back into points", () => {
  const range = (mean: number) => ({ mean, p10: mean, p90: mean });
  const full = (margin: number, total: number) => ({
    periods: {
      full: { margin: range(margin), total: range(total) },
      h1: { margin: range(0), total: range(0) },
      q1: { margin: range(0), total: range(0) },
    },
    missingPriorSeason: false,
  });
  // WKU (home) vs Missouri State, 2026-10-08: margin 4.7, total 55.0.
  assert.deepEqual(projectedScore(full(4.7, 55)), { home: 29.9, away: 25.2 });
  assert.deepEqual(projectedScore(full(-10, 41)), { home: 15.5, away: 25.5 });
  assert.equal(projectedScore({ ...full(3, 50), missingPriorSeason: true }), null);
  assert.equal(projectedScore(null), null);
});
