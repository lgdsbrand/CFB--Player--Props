import assert from "node:assert/strict";
import { test } from "node:test";

import { liveStateLabel, orderLive, periodLabel, toLiveScore, type LiveScore } from "./live.ts";

function s(overrides: Partial<LiveScore>): LiveScore {
  return {
    gameId: 1,
    status: "in_progress",
    period: 3,
    clock: "08:42",
    homePoints: 21,
    awayPoints: 14,
    homeLineScores: [7, 14, 0],
    awayLineScores: [0, 7, 7],
    possession: "home",
    situation: null,
    lastPlay: null,
    updatedAt: "2026-10-10T20:00:00Z",
    ...overrides,
  };
}

test("periods read as a scoreboard reads them, overtime included", () => {
  assert.equal(periodLabel(1), "1st");
  assert.equal(periodLabel(4), "4th");
  assert.equal(periodLabel(5), "OT");
  assert.equal(periodLabel(7), "3OT");
});

test("the state line", () => {
  assert.equal(liveStateLabel(s({})), "3rd 08:42");
  assert.equal(liveStateLabel(s({ period: 2, clock: "0:00" })), "Half");
  assert.equal(liveStateLabel(s({ period: 1, clock: null })), "End 1st");
  assert.equal(liveStateLabel(s({ period: null, clock: null })), "Live");
  assert.equal(liveStateLabel(s({ status: "completed", period: null })), "Final");
  assert.equal(liveStateLabel(s({ status: "completed", period: 6 })), "Final/2OT");
  assert.equal(liveStateLabel(s({ status: "scheduled" })), "Soon");
});

test("live games first, then finals, kickoff order kept within each", () => {
  const ordered = orderLive([
    s({ gameId: 1, status: "completed" }),
    s({ gameId: 2 }),
    s({ gameId: 3, status: "scheduled" }),
    s({ gameId: 4 }),
  ]);
  assert.deepEqual(ordered.map((x) => x.gameId), [2, 4, 1, 3]);
});

test("a database row maps, arrays and nulls included", () => {
  const row = toLiveScore({
    game_id: 6877, status: "completed", period: null, clock: null,
    home_points: 55, away_points: 34, home_line_scores: [17, 14, 10, 14],
    away_line_scores: null, possession: null, situation: null,
    last_play: "Timeout", updated_at: "2026-10-07T03:00:00Z",
  });
  assert.equal(row.homePoints, 55);
  assert.deepEqual(row.homeLineScores, [17, 14, 10, 14]);
  assert.equal(row.awayLineScores, null);
});
