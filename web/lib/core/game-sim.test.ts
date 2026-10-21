import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ballPath,
  checkpoints,
  describePlay,
  downLabel,
  edgeSideLabel,
  fieldX,
  histRange,
  pAbove,
  quarterStarts,
  replayVsLine,
  sideNumbers,
  spotLabel,
  toGameSim,
  toSimEdge,
  toSimPlay,
} from "./game-sim.ts";

const NAMES = { home: "KC", away: "BUF" };

// Rows as the worker stores them (game_sims.replay, 2026-10-21 production sample).
const RAW = [
  { q: 1, off: "home", away: 0, home: 0, play: "kickoff", clock: 900, result: "kickoff", end_off: "home", end_ytg: 65 },
  { q: 1, off: "home", ytg: 62, away: 0, down: 2, gain: 16, home: 0, play: "pass", togo: 7, clock: 856, result: "first_down", end_off: "home", end_ytg: 46 },
  { q: 1, off: "home", ytg: 20, away: 0, down: 3, gain: 20, home: 6, play: "pass", togo: 6, clock: 771, result: "td", end_off: "home", end_ytg: 20 },
  { q: 1, off: "home", away: 0, home: 7, play: "extra_point", clock: 765, result: "good", end_off: "home", end_ytg: 20 },
  { q: 1, off: "away", ytg: 66, away: 0, down: 4, home: 7, play: "punt", togo: 3, clock: 2, result: "punt", end_off: "home", end_ytg: 81 },
  { q: 2, off: "home", ytg: 81, away: 0, down: 1, gain: -3, home: 7, play: "run", togo: 10, clock: 900, result: "none", end_off: "home", end_ytg: 84 },
  { q: 2, off: "away", ytg: 30, away: 3, down: 4, home: 7, play: "field_goal", togo: 6, clock: 5, result: "good", end_off: "home", end_ytg: 65 },
  { q: 3, off: "away", away: 3, home: 7, play: "kickoff", clock: 900, result: "kickoff", end_off: "away", end_ytg: 75 },
];
const PLAYS = RAW.map(toSimPlay);

const seg = (home: number, away: number) => ({
  home, away, home_range: [0, 14], away_range: [0, 10], p_home_lead: 0.5, p_tie: 0.2,
  total: { lo: 0, n: [2, 0, 0, 3, 0, 0, 0, 4, 0, 0, 1] },
  margin: { lo: -3, n: [1, 0, 0, 2, 0, 0, 0, 1] },
});

test("a stored game_sims row reads with its replay", () => {
  const sim = toGameSim({
    game_id: 16495, stage: "pregame", sims: 2000, period: null, clock: null, home_points: 0,
    away_points: 0, prior_home: "24.5", prior_away: "20.1", made_at: "2026-10-21T09:40:00Z",
    summary: { quarters: [seg(5, 4.8), seg(6, 5), seg(4, 4), seg(6, 5)], halves: { first: seg(11, 9.8), second: seg(10, 9) }, game: seg(25, 20) },
    replay: RAW,
  });
  assert.equal(sim.priorHome, 24.5);
  assert.equal(sim.quarters[0].home, 5);
  assert.equal(sim.firstHalf.away, 9.8);
  assert.equal(sim.replay.length, 8);
  assert.equal(sim.replay[1].down, 2);
  assert.equal(sim.replay[0].ytg, null);
});

test("the home team attacks to the right, the away team to the left", () => {
  assert.equal(fieldX("home", 75), 25);
  assert.equal(fieldX("away", 75), 75);
  assert.equal(fieldX("home", 0), 100);
});

test("a touchdown ends in the end zone and a punt where the receiver takes over", () => {
  assert.deepEqual(ballPath(PLAYS[2]), { from: 80, to: 100 });
  assert.deepEqual(ballPath(PLAYS[4]), { from: 66, to: 19 });
  assert.deepEqual(ballPath(PLAYS[0]), { from: 35, to: 35 }); // kicked from BUF 35, KC at own 35
});

test("downs, spots and plays read like a broadcast", () => {
  assert.equal(downLabel(3, 6, 20), "3rd & 6");
  assert.equal(downLabel(1, 4, 4), "1st & Goal");
  assert.equal(downLabel(null, null, null), null);
  assert.equal(spotLabel("home", 62, NAMES), "KC 38");
  assert.equal(spotLabel("home", 20, NAMES), "BUF 20");
  assert.equal(spotLabel("away", 50, NAMES), "50");
  assert.equal(describePlay(PLAYS[1], NAMES), "KC pass for 16 yards, FIRST DOWN");
  assert.equal(describePlay(PLAYS[2], NAMES), "KC pass for a 20-yard TOUCHDOWN");
  assert.equal(describePlay(PLAYS[3], NAMES), "KC extra point is good");
  assert.equal(describePlay(PLAYS[4], NAMES), "BUF punt, KC take over at KC 19");
  assert.equal(describePlay(PLAYS[5], NAMES), "KC run for a loss of 3 yards");
  assert.equal(describePlay(PLAYS[6], NAMES), "BUF 47-yard field goal is GOOD");
});

test("the replay pauses at the end of the first quarter and of the half", () => {
  assert.deepEqual(checkpoints(PLAYS), [
    { period: "q1", index: 4, home: 7, away: 0 },
    { period: "h1", index: 6, home: 7, away: 3 },
  ]);
  assert.deepEqual([...quarterStarts(PLAYS)], [[1, 0], [2, 5], [3, 7]]);
});

test("histograms price a line with pushes left out, and give an 80% range", () => {
  const h = { lo: 0, n: [2, 0, 0, 3, 0, 0, 0, 4, 0, 0, 1] }; // 0,0,3,3,3,7,7,7,7,10
  assert.equal(pAbove(h, 6.5), 0.5);
  assert.equal(pAbove(h, 3), 5 / 7);
  assert.deepEqual(histRange(h), [0, 7]);
});

const EDGE = toSimEdge({
  game_id: 16495, period: "q1", market: "spreads", line: "-0.5", sportsbook_id: 26,
  first_price: -120, second_price: 100, book_prob: "0.47826", shown_prob: "0.47409",
  side: "away", shown_edge: "0.00417", model_mean: "0.2", result: null, made_at: "2026-10-21T06:54:45Z",
});

test("our side reads from the team's perspective, with our % and the book's of it", () => {
  assert.equal(edgeSideLabel(EDGE, NAMES), "BUF +0.5");
  const s = sideNumbers(EDGE);
  assert.equal(s.price, 100);
  assert.ok(Math.abs(s.ours - 0.52591) < 1e-9 && Math.abs(s.book - 0.52174) < 1e-9);
  assert.equal(edgeSideLabel({ ...EDGE, market: "totals", side: "over", line: 9.5 }, NAMES), "Over 9.5");
  assert.equal(edgeSideLabel({ ...EDGE, market: "h2h", side: "home", line: null }, NAMES), "KC ML");
});

test("the replayed period is set against the book's line", () => {
  assert.equal(replayVsLine(EDGE, { home: 7, away: 0 }), "first"); // KC -0.5 covers
  assert.equal(replayVsLine({ ...EDGE, market: "totals", line: 7 }, { home: 7, away: 0 }), "push");
  assert.equal(replayVsLine({ ...EDGE, market: "h2h", line: null }, { home: 3, away: 3 }), "push");
});
