import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_TIER,
  PROPS_RECORD_PUBLIC,
  byPickDay,
  marketBreakdown,
  recordWeeks,
  resolveRecordWeek,
  resolveTier,
  tierCards,
  tierMinEdge,
  totals,
  type PropPick,
  type PropRecordRow,
} from "./prop-record.ts";

function row(overrides: Partial<PropRecordRow>): PropRecordRow {
  return {
    season: 2026, week: 7, marketKey: "rush_yards", marketLabel: "Rush Yds", band: 5,
    wins: 0, losses: 0, pushes: 0, voids: 0, pending: 0, units: 0, unpricedWins: 0, ...overrides,
  };
}

const ROWS = [
  row({ band: 10, wins: 2, losses: 1, units: 0.8 }),
  row({ band: 5, wins: 3, losses: 1, units: 1.6 }),
  row({ band: 2, wins: 1, losses: 3, units: -2.1 }),
  row({ band: 0, wins: 4, losses: 6, pushes: 1, units: -2.5 }),
  row({ band: -1, wins: 0, losses: 2, units: -2 }),
  row({ band: null, wins: 1, losses: 0, voids: 2, units: 0.9 }),
  row({ week: 6, band: 5, marketKey: "receptions", marketLabel: "Rec", wins: 1, losses: 0, units: 0.91 }),
];

test("5%+ leads, is the default, and the page is not linked yet", () => {
  assert.equal(DEFAULT_TIER, "5");
  assert.equal(tierCards(ROWS, null)[0].key, "5");
  assert.equal(resolveTier(undefined), "5");
  assert.equal(resolveTier("bogus"), "5");
  assert.equal(resolveTier("all"), "all");
  assert.equal(PROPS_RECORD_PUBLIC, false);
});

test("a tier is every band at or above its floor; all calls takes every pick", () => {
  const week7 = { season: 2026, week: 7 };
  const cards = Object.fromEntries(tierCards(ROWS, week7).map((c) => [c.key, c.totals]));
  assert.deepEqual([cards["10"].wins, cards["10"].losses], [2, 1]);
  assert.deepEqual([cards["5"].wins, cards["5"].losses], [5, 2]);
  assert.deepEqual([cards["2"].wins, cards["2"].losses], [6, 5]);
  assert.deepEqual([cards.all.wins, cards.all.losses, cards.all.pushes, cards.all.voids], [11, 13, 1, 2]);
});

test("win rate and ROI are over decided picks only", () => {
  const t = totals([row({ wins: 3, losses: 1, pushes: 2, voids: 1, units: 1.8 })]);
  assert.equal(t.winRate, 0.75);
  assert.equal(t.roi, 0.45);
  assert.equal(totals([row({ pending: 3 })]).winRate, null);
  assert.equal(totals([]).roi, null);
});

test("markets are broken out within the tier, busiest first", () => {
  const markets = marketBreakdown(ROWS, null, "5");
  assert.deepEqual(markets.map((m) => [m.key, m.totals.wins, m.totals.losses]), [
    ["rush_yards", 5, 2],
    ["receptions", 1, 0],
  ]);
});

test("weeks come newest first, and an unknown week means all weeks", () => {
  const weeks = recordWeeks(ROWS);
  assert.deepEqual(weeks, [{ season: 2026, week: 7 }, { season: 2026, week: 6 }]);
  assert.deepEqual(resolveRecordWeek("6", weeks), { season: 2026, week: 6 });
  assert.equal(resolveRecordWeek("99", weeks), null);
  assert.equal(resolveRecordWeek(undefined, weeks), null);
});

test("the list's edge floor matches the tier", () => {
  assert.equal(tierMinEdge("5"), 0.05);
  assert.equal(tierMinEdge("all"), null);
});

test("picks fold by Eastern day, newest first, a late kickoff staying on its day", () => {
  const p = (id: number, startDate: string) => ({ id, startDate }) as PropPick;
  const days = byPickDay([p(1, "2026-10-18T03:30:00Z"), p(2, "2026-10-17T16:00:00Z"), p(3, "2026-10-16T23:30:00Z")]);
  assert.deepEqual(days.map((d) => [d.day, d.picks.map((x) => x.id)]), [
    ["2026-10-17", [1, 2]],
    ["2026-10-16", [3]],
  ]);
});
