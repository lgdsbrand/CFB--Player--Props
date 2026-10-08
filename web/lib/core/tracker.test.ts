import assert from "node:assert/strict";
import { test } from "node:test";

import {
  byDay,
  cardFilter,
  cards,
  formatUnits,
  inPeriod,
  isEdgePlay,
  pickLabel,
  record,
  resolveCardKey,
  resolveEngine,
  resolvePeriod,
  type TrackerPick,
} from "./tracker.ts";

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
    sportsbookName: "Pinnacle",
    season: 2026,
    week: 6,
    startDate: "2026-10-08T23:00:00+00:00",
    homePoints: 30,
    awayPoints: 20,
    home: "LIB",
    away: "SHSU",
    result: "win",
    units: 0.9091,
    ...overrides,
  };
}

test("a record counts pushes apart and leaves them out of the win rate", () => {
  const r = record([
    pick({ result: "win", units: 0.9091 }),
    pick({ result: "loss", units: -1 }),
    pick({ result: "push", units: 0 }),
    pick({ result: "pending", units: null }),
  ]);
  assert.deepEqual(
    { w: r.wins, l: r.losses, p: r.pushes, pending: r.pending, rate: r.winRate, units: r.units },
    { w: 1, l: 1, p: 1, pending: 1, rate: 0.5, units: -0.09 },
  );
  assert.equal(record([]).winRate, null);
});

test("periods: yesterday and month by Eastern day, week is the latest graded week", () => {
  const now = new Date("2026-10-09T15:00:00Z"); // Friday morning ET
  const thu = pick({ startDate: "2026-10-08T23:30:00+00:00" }); // Thu 7:30 PM ET
  const lateThu = pick({ startDate: "2026-10-09T02:00:00+00:00" }); // Thu 10 PM ET
  const sep = pick({ startDate: "2026-09-27T00:00:00+00:00", week: 4 }); // Sep 26 ET
  const nextWeek = pick({ week: 7, startDate: "2026-10-10T23:30:00+00:00", result: "pending", units: null });
  const all = [thu, lateThu, sep, nextWeek];
  assert.deepEqual(inPeriod(all, "yesterday", now), [thu, lateThu]);
  assert.deepEqual(inPeriod(all, "month", now), [thu, lateThu, nextWeek]);
  // Week 7 has nothing graded yet, so "Week" is still week 6.
  assert.deepEqual(inPeriod(all, "week", now), [thu, lateThu]);
  assert.equal(inPeriod(all, "all", now).length, 4);
});

test("edge plays are each engine's pre-registered tier", () => {
  assert.equal(isEdgePlay(pick({ engine: "v2", edge: 0.03 })), true);
  assert.equal(isEdgePlay(pick({ engine: "v2", edge: 0.029 })), false);
  assert.equal(isEdgePlay(pick({ engine: "v1", market: "totals", modelProb: 0.64, edge: null })), true);
  assert.equal(isEdgePlay(pick({ engine: "v1", market: "spreads", modelProb: 0.9, edge: null })), false);
});

test("v1 shows its retired moneyline card, v2 has none", () => {
  assert.deepEqual(cards([], "v1").map((c) => c.key), ["spreads", "totals", "edge", "h2h"]);
  assert.deepEqual(cards([], "v2").map((c) => c.key), ["spreads", "totals", "edge"]);
});

test("pick labels read as a bettor writes them", () => {
  assert.equal(pickLabel({ market: "spreads", side: "away", line: -14, home: "LIB", away: "SHSU" }), "SHSU +14.0");
  assert.equal(pickLabel({ market: "totals", side: "under", line: 52.5, home: "LIB", away: "SHSU" }), "Under 52.5");
  assert.equal(pickLabel({ market: "h2h", side: "home", line: null, home: "LIB", away: "SHSU" }), "LIB ML");
});

test("days are newest first, games in kickoff order", () => {
  const a = pick({ startDate: "2026-10-07T23:00:00+00:00" });
  const b = pick({ startDate: "2026-10-08T23:30:00+00:00" });
  const c = pick({ startDate: "2026-10-08T23:00:00+00:00" });
  const days = byDay([a, b, c]);
  assert.deepEqual(days.map((d) => d.day), ["2026-10-08", "2026-10-07"]);
  assert.deepEqual(days[0].picks, [c, b]);
});

test("parsing and units", () => {
  assert.equal(resolveEngine(undefined), "v2");
  assert.equal(resolveEngine("v1"), "v1");
  assert.equal(resolvePeriod("week"), "week");
  assert.equal(resolvePeriod("decade"), "all");
  assert.equal(formatUnits(1.5), "+1.50u");
  assert.equal(formatUnits(-6.57), "-6.57u");
  assert.equal(formatUnits(0), "0.00u");
});

test("a card's filter picks exactly what the card counts", () => {
  const picks = [
    pick({ market: "spreads", edge: 0.021 }),
    pick({ market: "spreads", edge: 0.034 }),
    pick({ market: "totals", side: "over", line: 52.5, edge: 0.05 }),
    pick({ market: "totals", side: "under", line: 48.5, edge: 0.022 }),
  ];
  for (const card of cards(picks, "v2")) {
    const filtered = picks.filter(cardFilter(card.key));
    assert.equal(record(filtered).wins + record(filtered).losses, card.record.wins + card.record.losses);
  }
  assert.equal(picks.filter(cardFilter("spreads")).length, 2);
  assert.equal(picks.filter(cardFilter("totals")).length, 2);
  assert.equal(picks.filter(cardFilter("edge")).length, 2);
});

test("resolveCardKey accepts only the cards the engine shows", () => {
  assert.equal(resolveCardKey("spreads", "v2"), "spreads");
  assert.equal(resolveCardKey("edge", "v2"), "edge");
  assert.equal(resolveCardKey("h2h", "v2"), null);
  assert.equal(resolveCardKey("h2h", "v1"), "h2h");
  assert.equal(resolveCardKey(undefined, "v2"), null);
  assert.equal(resolveCardKey("bogus", "v2"), null);
});
