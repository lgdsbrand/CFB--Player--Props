import assert from "node:assert/strict";
import { test } from "node:test";

import {
  asOfLabel,
  chartPoints,
  favourite,
  marginRangeLabel,
  minutesSince,
  projectedScore,
  spreadLabel,
  toLiveProjection,
  waitingState,
  winLabel,
} from "./live-model.ts";

test("waitingState: before kickoff, a while after it, then nothing", () => {
  const kickoff = "2026-10-23T00:15:00Z";
  const at = Date.parse(kickoff);
  assert.equal(waitingState(kickoff, at - 86_400_000), "before");
  assert.equal(waitingState(kickoff, at - 1), "before");
  assert.equal(waitingState(kickoff, at), "waiting");
  assert.equal(waitingState(kickoff, at + 5 * 3_600_000), "waiting");
  assert.equal(waitingState(kickoff, at + 5 * 3_600_000 + 1), null);
  assert.equal(waitingState(null, at), null);
  assert.equal(waitingState("not a date", at), null);
});

const ROW = {
  game_id: 16487, status: "in_progress", period: 4, clock: "9:22", elapsed: 3038,
  home_points: 23, away_points: 16, possession: null, p_home_win: "0.84997",
  margin_mean: "7.3", margin_lo: "0.3", margin_hi: "14.4", total_mean: "46.0",
  total_lo: "39.0", total_hi: "53.9", prior_margin: "10.7", prior_total: "51.5",
  prior_source: "fantasy", updated_at: "2026-10-20T02:45:44Z",
};

test("a stored row reads into numbers, as the worker wrote it", () => {
  const p = toLiveProjection(ROW);
  assert.equal(p.pHomeWin, 0.84997);
  assert.equal(p.marginMean, 7.3);
  assert.equal(p.period, 4);
  assert.equal(p.possession, null);
  assert.equal(p.priorSource, "fantasy");
});

test("the favourite and its win %, from either side, and a dead heat names nobody", () => {
  assert.deepEqual(favourite({ pHomeWin: 0.84997 }, "SF", "WAS"), { team: "SF", pct: 85 });
  assert.deepEqual(favourite({ pHomeWin: 0.3 }, "SF", "WAS"), { team: "WAS", pct: 70 });
  assert.deepEqual(favourite({ pHomeWin: 0.502 }, "SF", "WAS"), { team: null, pct: 50 });
  assert.equal(winLabel({ pHomeWin: 0.502 }, "SF", "WAS"), "Even");
  assert.equal(winLabel({ pHomeWin: 0.9991 }, "SF", "WAS"), "SF 99%"); // never 100% mid-game
});

test("the projected final splits the total by the margin", () => {
  assert.deepEqual(projectedScore({ marginMean: 7.3, totalMean: 46 }), { home: 26.7, away: 19.4 });
});

test("margins read as spreads and ranges read in words", () => {
  assert.equal(spreadLabel(7.3, "SF", "WAS"), "SF -7.3");
  assert.equal(spreadLabel(-3, "SF", "WAS"), "WAS -3.0");
  assert.equal(spreadLabel(0.01, "SF", "WAS"), "Pick'em");
  assert.equal(marginRangeLabel(0.3, 14.4, "SF", "WAS"), "SF by 0.3 to SF by 14.4");
  assert.equal(marginRangeLabel(-2.5, 9, "SF", "WAS"), "WAS by 2.5 to SF by 9.0");
  assert.equal(marginRangeLabel(0, 9, "SF", "WAS"), "a tie to SF by 9.0");
});

test("chart points run in game order, in minutes and percent, overtime at the 60th minute", () => {
  const points = chartPoints([
    { elapsed: 3297, homePoints: 23, awayPoints: 17, pHomeWin: 0.74703 },
    { elapsed: 3038, homePoints: 23, awayPoints: 16, pHomeWin: 0.84997 },
    { elapsed: 3900, homePoints: 26, awayPoints: 23, pHomeWin: 1 },
  ]);
  assert.deepEqual(points.map((p) => p.minute), [50.6, 55, 60]);
  assert.deepEqual(points.map((p) => p.homePct), [85, 74.7, 100]);
  assert.equal(points[0].score, "16-23"); // away-home, as the page reads "AWAY @ HOME"
});

test("the numbers say which moment of the game they belong to", () => {
  const p = toLiveProjection(ROW);
  assert.equal(asOfLabel(p, "SF", "WAS"), "4th 9:22 · WAS 16, SF 23");
  assert.equal(asOfLabel({ ...p, period: 2, clock: "0:00" }, "SF", "WAS"), "Half · WAS 16, SF 23");
  assert.equal(asOfLabel({ ...p, period: 5, clock: "6:10" }, "SF", "WAS"), "OT 6:10 · WAS 16, SF 23");
});

test("minutes since an update", () => {
  assert.equal(minutesSince("2026-10-20T02:45:00Z", Date.parse("2026-10-20T02:48:10Z")), 3);
  assert.equal(minutesSince("2026-10-20T02:45:00Z", Date.parse("2026-10-20T02:44:00Z")), 0);
});
