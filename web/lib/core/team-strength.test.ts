import assert from "node:assert/strict";
import { test } from "node:test";

import { ordinal, rankStrength, type StrengthRow } from "./team-strength.ts";

function row(teamId: number, overrides: Partial<StrengthRow> = {}): StrengthRow {
  return {
    teamId,
    gamesIncluded: 5,
    offPointsPg: 30,
    offPpa: 0.2,
    offSuccess: 0.45,
    offPlaysPg: 70,
    defPointsPg: 25,
    defPpa: 0.15,
    defSuccess: 0.4,
    ...overrides,
  };
}

test("offense ranks highest first, defense lowest first", () => {
  const rows = [
    row(1, { offPointsPg: 40, defPointsPg: 14 }),
    row(2, { offPointsPg: 30, defPointsPg: 21 }),
    row(3, { offPointsPg: 20, defPointsPg: 35 }),
  ];
  const ranked = rankStrength(rows, [1, 3]);
  assert.equal(ranked.get(1)!.metrics.offPointsPg!.rank, 1);
  assert.equal(ranked.get(1)!.metrics.defPointsPg!.rank, 1); // allows the fewest
  assert.equal(ranked.get(3)!.metrics.offPointsPg!.rank, 3);
  assert.equal(ranked.get(3)!.metrics.defPointsPg!.rank, 3);
  assert.equal(ranked.get(1)!.metrics.offPointsPg!.of, 3);
  assert.equal(ranked.has(2), false); // only the teams asked for
});

test("ties share the better rank", () => {
  const rows = [row(1, { offPpa: 0.3 }), row(2, { offPpa: 0.3 }), row(3, { offPpa: 0.1 })];
  const ranked = rankStrength(rows, [1, 2, 3]);
  assert.equal(ranked.get(1)!.metrics.offPpa!.rank, 1);
  assert.equal(ranked.get(2)!.metrics.offPpa!.rank, 1);
  assert.equal(ranked.get(3)!.metrics.offPpa!.rank, 3);
});

test("the bar runs from full at first to empty at last", () => {
  const rows = [row(1, { offSuccess: 0.5 }), row(2, { offSuccess: 0.45 }), row(3, { offSuccess: 0.4 })];
  const ranked = rankStrength(rows, [1, 2, 3]);
  assert.equal(ranked.get(1)!.metrics.offSuccess!.share, 1);
  assert.equal(ranked.get(2)!.metrics.offSuccess!.share, 0.5);
  assert.equal(ranked.get(3)!.metrics.offSuccess!.share, 0);
});

test("a missing value is unranked, not ranked last", () => {
  const rows = [row(1, { offPlaysPg: null }), row(2), row(3, { offPlaysPg: 60 })];
  const ranked = rankStrength(rows, [1, 3]);
  assert.equal(ranked.get(1)!.metrics.offPlaysPg, undefined);
  assert.equal(ranked.get(3)!.metrics.offPlaysPg!.of, 2);
  assert.equal(ranked.get(3)!.metrics.offPlaysPg!.rank, 2);
});

test("a team with no row this week is absent, so the panel can say so", () => {
  assert.equal(rankStrength([row(1)], [1, 99]).has(99), false);
});

test("ordinals", () => {
  assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 112, 136].map(ordinal), [
    "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "112th", "136th",
  ]);
});
