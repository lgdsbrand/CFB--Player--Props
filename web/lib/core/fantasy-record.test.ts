import assert from "node:assert/strict";
import { test } from "node:test";

import { fantasyRecordFeed, RECENT_PICKS } from "./fantasy-record.ts";
import type { TrackerPick } from "./tracker.ts";

function pick(overrides: Partial<TrackerPick>): TrackerPick {
  return {
    id: 1,
    engine: "fantasy",
    gameId: 10,
    market: "totals",
    side: "over",
    line: 42.5,
    price: -103,
    modelProb: 0.83,
    edge: 0.34,
    modelLine: 49.5,
    gap: 7,
    rawProb: null,
    sportsbookName: "Pinnacle",
    season: 2026,
    week: 6,
    startDate: "2026-10-18T13:30:00Z",
    homePoints: 31,
    awayPoints: 34,
    home: "JAX",
    away: "HOU",
    result: "win",
    units: 0.9709,
    ...overrides,
  };
}

// The four week-6 picks of 2026-10-18 as graded on production, plus a hidden
// sheet-version pick that must not count.
const WEEK_6 = [
  pick({ id: 759 }),
  pick({ id: 764, line: 38.5, price: 101, home: "TB", away: "PIT", units: 1.01, startDate: "2026-10-18T17:00:00Z" }),
  pick({ id: 762, line: 39, price: -109, home: "PHI", away: "CAR", result: "push", units: 0, startDate: "2026-10-18T17:00:00Z" }),
  pick({
    id: 760, market: "spreads", side: "home", line: 1, price: -109, home: "ATL", away: "CHI",
    homePoints: 23, awayPoints: 31, result: "loss", units: -1, startDate: "2026-10-18T17:00:00Z",
  }),
  pick({ id: 765, engine: "fantasy_sheet", market: "spreads", side: "away", line: -13.5 }),
];

test("the feed counts the shown version only, split by market, in his record shape", () => {
  const feed = fantasyRecordFeed(WEEK_6);
  assert.deepEqual(feed.overall, { wins: 2, losses: 1, pushes: 1, pending: 0, win_rate: 2 / 3, units: 0.98 });
  assert.deepEqual(feed.spread, { wins: 0, losses: 1, pushes: 0, pending: 0, win_rate: 0, units: -1 });
  assert.equal(feed.total.wins, 2);
  assert.equal(feed.total.pushes, 1);
  assert.equal(feed.by_week.length, 1);
  assert.deepEqual([feed.by_week[0].season, feed.by_week[0].week], [2026, 6]);
});

test("recent picks are newest first and read like the tracker", () => {
  const feed = fantasyRecordFeed(WEEK_6);
  assert.equal(feed.picks.length, 4);
  assert.equal(feed.picks.at(-1)?.matchup, "HOU @ JAX"); // the earliest kickoff
  const spread = feed.picks.find((p) => p.market === "spread");
  assert.equal(spread?.pick, "ATL +1.0");
  assert.equal(spread?.final, "31-23");
});

test("weeks are newest first and the pick list is capped", () => {
  const many = Array.from({ length: RECENT_PICKS + 5 }, (_, i) =>
    pick({ id: i + 1, week: i < 10 ? 6 : 7, startDate: `2026-10-${String(10 + i).padStart(2, "0")}T17:00:00Z` }),
  );
  const feed = fantasyRecordFeed(many);
  assert.deepEqual(feed.by_week.map((w) => w.week), [7, 6]);
  assert.equal(feed.picks.length, RECENT_PICKS);
});

test("an engine with no picks yet is a zero record, not an error", () => {
  const feed = fantasyRecordFeed([]);
  assert.equal(feed.overall.win_rate, null);
  assert.deepEqual(feed.by_week, []);
});
