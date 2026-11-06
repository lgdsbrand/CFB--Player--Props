import assert from "node:assert/strict";
import { test } from "node:test";

import type { TrackerPick } from "./tracker.ts";
import { MAX_CARDS, publicEngines, yesterdayResults } from "./yesterday-results.ts";

let id = 1;
function pick(overrides: Partial<TrackerPick> = {}): TrackerPick {
  return {
    id: id++,
    engine: "v2",
    gameId: id,
    market: "spreads",
    side: "home",
    line: -3.5,
    price: -110,
    modelProb: 0.53,
    edge: 0.025,
    modelLine: null,
    gap: null,
    rawProb: null,
    sportsbookName: "Pinnacle",
    season: 2026,
    week: 11,
    // Thursday 2026-11-05, 7:30 p.m. Eastern.
    startDate: "2026-11-06T00:30:00+00:00",
    homePoints: 30,
    awayPoints: 20,
    home: "LIB",
    away: "SHSU",
    result: "win",
    units: 0.9091,
    ...overrides,
  };
}

// Friday 2026-11-06, noon Eastern: yesterday is Thursday.
const NOW = new Date("2026-11-06T17:00:00Z");

test("college: a record per public engine, cards for the rule plays only", () => {
  const picks = [
    pick({ engine: "v2", result: "win", units: 0.9091 }),
    pick({ engine: "v2", result: "loss", units: -1 }),
    pick({ engine: "rules", market: "totals", side: "over", line: 52.5, result: "loss", units: -1 }),
    // Not public: never counted.
    pick({ engine: "v1", result: "win" }),
  ];
  const r = yesterdayResults("cfb", picks, NOW);
  assert.deepEqual(r?.records, [
    { engine: "v2", label: "Game model", wins: 1, losses: 1, pushes: 0, units: -0.09 },
    { engine: "rules", label: "Rule plays", wins: 0, losses: 1, pushes: 0, units: -1 },
  ]);
  assert.equal(r?.cards.length, 1);
  assert.equal(r?.cards[0].pick, "Over 52.5");
  assert.equal(r?.cards[0].result, "loss");
});

test("only yesterday's graded picks: pending, older and today's are left out", () => {
  const picks = [
    pick({ engine: "fantasy", result: "pending", units: null }),
    pick({ engine: "fantasy", startDate: "2026-11-04T00:30:00+00:00" }),
    pick({ engine: "fantasy", startDate: "2026-11-06T16:00:00+00:00" }),
    // 10 p.m. Thursday Eastern is Thursday, though it is Friday in UTC.
    pick({ engine: "fantasy", startDate: "2026-11-06T03:00:00+00:00", market: "h2h", side: "away", line: null }),
  ];
  const r = yesterdayResults("nfl", picks, NOW);
  assert.deepEqual(r?.records.map((x) => [x.engine, x.wins, x.losses]), [["fantasy", 1, 0]]);
  assert.deepEqual(r?.cards.map((c) => c.pick), ["SHSU ML"]);
});

test("a sport with nothing graded yesterday is null, not 0-0", () => {
  assert.equal(yesterdayResults("cfb", [], NOW), null);
  assert.equal(yesterdayResults("cfb", [pick({ engine: "v1" })], NOW), null);
});

test("cards are capped, records are not", () => {
  const picks = Array.from({ length: MAX_CARDS + 5 }, () => pick({ engine: "rules" }));
  const r = yesterdayResults("cfb", picks, NOW);
  assert.equal(r?.cards.length, MAX_CARDS);
  assert.equal(r?.records[0].wins, MAX_CARDS + 5);
});

test("each sport reads only its public engines", () => {
  assert.deepEqual(publicEngines("cfb"), ["v2", "rules"]);
  assert.deepEqual(publicEngines("nfl"), ["fantasy"]);
});
