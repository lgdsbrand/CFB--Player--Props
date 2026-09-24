import assert from "node:assert/strict";
import { test } from "node:test";

import {
  atsResult,
  formatFair,
  formatPoints,
  formatRecord,
  lineMove,
  sharpRetailGap,
  spreadLabel,
  spreadMoveToward,
  teamRecord,
  totalMoveLabel,
  totalResult,
  type GameMarketSummary,
  type GradedGame,
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
