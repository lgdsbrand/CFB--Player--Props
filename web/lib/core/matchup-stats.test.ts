import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MATCHUP_METRICS,
  injurySummary,
  radarPoints,
  rankMatchup,
  type MatchupInputRow,
} from "./matchup-stats.ts";

function row(teamId: number, overrides: Partial<MatchupInputRow> = {}): MatchupInputRow {
  return {
    teamId,
    gamesIncluded: 6,
    masseyOff: 22,
    masseyDef: 0,
    srs: 0,
    offEpaPlay: 0,
    thirdDownPct: 0.4,
    redZonePct: 0.55,
    playsPg: 62,
    defEpaPlay: 0,
    oppThirdDownPct: 0.4,
    oppRedZonePct: 0.55,
    ...overrides,
  };
}

test("what a defense allows ranks lowest first; ratings and offense highest first", () => {
  const rows = [
    row(1, { masseyDef: 4, defEpaPlay: -0.1, oppRedZonePct: 0.4, offEpaPlay: 0.2 }),
    row(2, { masseyDef: 0, defEpaPlay: 0.0, oppRedZonePct: 0.55, offEpaPlay: 0.0 }),
    row(3, { masseyDef: -4, defEpaPlay: 0.1, oppRedZonePct: 0.7, offEpaPlay: -0.2 }),
  ];
  const ranked = rankMatchup(rows, [1, 3]);
  const best = ranked.get(1)!.metrics;
  assert.equal(best.masseyDef!.rank, 1); // higher defense rating = better
  assert.equal(best.defEpaPlay!.rank, 1); // allows the least EPA
  assert.equal(best.oppRedZonePct!.rank, 1);
  assert.equal(best.offEpaPlay!.rank, 1);
  assert.equal(ranked.get(3)!.metrics.defEpaPlay!.rank, 3);
  assert.equal(ranked.get(3)!.metrics.defEpaPlay!.of, 3);
});

test("the radar reads further out as better on every axis, and leaves pace off", () => {
  const rows = [row(1, { defEpaPlay: -0.1, offEpaPlay: 0.2 }), row(2, { defEpaPlay: 0.1, offEpaPlay: -0.2 })];
  const ranked = rankMatchup(rows, [1, 2]);
  const points = radarPoints(ranked.get(1), ranked.get(2));
  const epaD = points.find((p) => p.axis === "EPA D")!;
  assert.equal(epaD.away, 100); // allows less: outermost
  assert.equal(epaD.home, 0);
  assert.equal(epaD.awayRank!.rank, 1);
  assert.equal(points.find((p) => p.axis === "EPA")!.away, 100);
  assert.ok(!points.some((p) => p.axis.toLowerCase().includes("plays")));
  assert.equal(points.length, MATCHUP_METRICS.filter((m) => m.short !== null).length);
});

test("a missing value plots nothing rather than a zero", () => {
  const rows = [row(1, { redZonePct: null }), row(2)];
  const points = radarPoints(...([1, 2].map((id) => rankMatchup(rows, [1, 2]).get(id)) as [never, never]));
  const rz = points.find((p) => p.axis === "Red zone")!;
  assert.equal(rz.away, null);
  assert.equal(rz.awayRank, null);
  assert.equal(rz.home, 100); // ranked alone: 1st of 1
});

test("a team with no row at all gets no entry, and the radar shows only the other", () => {
  const ranked = rankMatchup([row(1)], [1, 99]);
  assert.equal(ranked.has(99), false);
  const points = radarPoints(ranked.get(1), ranked.get(99));
  assert.ok(points.every((p) => p.home === null && p.away !== null));
});

test("the injury line counts game statuses in order of severity and ignores the rest", () => {
  assert.equal(
    injurySummary(["Questionable", "Out", "Questionable", "Injured Reserve", "Doubtful", "Out"]),
    "2 out · 1 doubtful · 2 questionable",
  );
  assert.equal(injurySummary(["Injured Reserve"]), null);
  assert.equal(injurySummary([]), null);
});

test("signed formats use a true minus and no sign at zero", () => {
  const def = MATCHUP_METRICS.find((m) => m.key === "masseyDef")!;
  assert.equal(def.format(-4.6), "−4.6");
  assert.equal(def.format(0.44), "+0.4");
  assert.equal(def.format(0), "0.0");
  assert.equal(MATCHUP_METRICS.find((m) => m.key === "redZonePct")!.format(0.5385), "53.9%");
});
