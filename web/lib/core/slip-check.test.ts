import assert from "node:assert/strict";
import { test } from "node:test";

import {
  blendWinProb,
  bookCounts,
  calibrateGame,
  checkLeg,
  checkSlip,
  decodeSlipKeys,
  encodeSlipKeys,
  gameProbAtLine,
  indexCatalogue,
  normalCdf,
  parseLegKey,
  propProbAtLine,
  searchCatalogue,
  type CheckEntry,
} from "./slip-check.ts";
import { legKey, type GameLeg, type PropLeg } from "./slip.ts";

const close = (a: number | null, b: number, tol = 1e-3) =>
  assert.ok(a !== null && Math.abs(a - b) < tol, `${a} vs ${b}`);

function propLeg(side: "over" | "under", line = 62.5, overrides: Partial<PropLeg> = {}): PropLeg {
  return {
    kind: "prop", gameId: 1, playerId: 10, marketKey: "rush_yards", side, line, binary: false,
    player: "R. Brown", market: "Rush Yds", matchup: "TEX @ OU", startDate: "2026-10-17T19:30:00Z",
    ...overrides,
  };
}

function propEntry(overrides: Partial<CheckEntry> = {}, prices = { over: { dk: -115, fd: -110 }, under: { dk: -105 } }): CheckEntry {
  return {
    id: "p:1:10:rush_yards:62.50",
    kind: "prop",
    gameId: 1,
    startDate: "2026-10-17T19:30:00Z",
    title: "R. Brown",
    market: "Rush Yds 62.5",
    matchup: "TEX @ OU",
    search: "r. brown tex ou texas oklahoma rush yds",
    sides: [
      { leg: propLeg("over"), label: "Over 62.5", prices: prices.over },
      { leg: propLeg("under"), label: "Under 62.5", prices: prices.under },
    ],
    model: { kind: "prob", first: 0.56 },
    ...overrides,
  };
}

function gameLeg(market: GameLeg["market"], side: GameLeg["side"], line: number | null, gameId = 2): GameLeg {
  return { kind: "game", gameId, period: "full", market, side, line, home: "TTU", away: "ASU", startDate: "2026-10-17T19:30:00Z" };
}

function mlEntry(gameId = 2): CheckEntry {
  return {
    id: `g:${gameId}:full:h2h:`,
    kind: "game",
    gameId,
    startDate: "2026-10-17T19:30:00Z",
    title: "ASU @ TTU",
    market: "Moneyline",
    matchup: "ASU @ TTU",
    search: "asu ttu arizona state texas tech moneyline ml",
    sides: [
      { leg: gameLeg("h2h", "home", null, gameId), label: "TTU ML", prices: { dk: -600 } },
      { leg: gameLeg("h2h", "away", null, gameId), label: "ASU ML", prices: { dk: 440 } },
    ],
    model: { kind: "h2h", rawHome: 0.89 },
  };
}

test("prop probability: exact at a known line, interpolated between, none outside", () => {
  const rungs = [
    { line: 40.5, probOver: 0.62 },
    { line: 60.5, probOver: 0.54 },
    { line: 80.5, probOver: 0.45 },
  ];
  const point = { line: 62.5, probOver: 0.53 };
  close(propProbAtLine(point, rungs, 62.5), 0.53);
  close(propProbAtLine(point, rungs, 60.5), 0.54);
  close(propProbAtLine(point, rungs, 71.5), 0.53 + (9 / 18) * (0.45 - 0.53));
  assert.equal(propProbAtLine(point, rungs, 85.5), null);
  assert.equal(propProbAtLine(null, [], 62.5), null);
});

test("game calibration and the moneyline blend match the worker's formulas", () => {
  // sigmoid(0.0565 * logit(0.8)) — a raw 80% spread side is about 52%.
  close(calibrateGame(0.8, "spreads"), 1 / (1 + Math.exp(-0.0565 * Math.log(4))));
  close(calibrateGame(0.5, "totals"), 0.5);
  const z = 0.073 * Math.log(0.89 / 0.11) + 1.045 * Math.log(0.8 / 0.2);
  close(blendWinProb(0.89, 0.8), 1 / (1 + Math.exp(-z)));
  close(normalCdf(0), 0.5, 1e-7);
  close(normalCdf(1.2815515655), 0.9, 1e-6);
});

test("game probability: the stored number at the priced line, the normal elsewhere", () => {
  const range = { mean: 20.5, p10: 5, p90: 36 };
  assert.equal(gameProbAtLine("spreads", range, -14, { line: -14, modelFirstProb: 0.507 }), 0.507);
  // Home -14 against a 20.5 mean margin: home covers more often than not.
  const other = gameProbAtLine("spreads", range, -13.5, { line: -14, modelFirstProb: 0.507 });
  assert.ok(other > 0.5 && other < 0.52, String(other));
  // A total far under the projection leans over.
  assert.ok(gameProbAtLine("totals", { mean: 55, p10: 40, p90: 70 }, 48.5, null) > 0.55);
});

test("a leg: model vs the book's vig-free chance, the value at its price, a verdict", () => {
  const entry = propEntry();
  const over = checkLeg(propLeg("over"), { entry, index: 0 }, "dk");
  close(over.modelProb, 0.56);
  const book = (115 / 215) / (115 / 215 + 105 / 205);
  close(over.bookProb, book);
  close(over.edge, 0.56 - book);
  close(over.value, 0.56 * (1 + 100 / 115) - 1);
  assert.equal(over.verdict, "agrees");
  assert.equal(over.fairPrice, -127);

  const under = checkLeg(propLeg("under"), { entry, index: 1 }, "dk");
  close(under.modelProb, 0.44);
  assert.equal(under.verdict, "disagrees");

  // FanDuel prices only the over: no vig-free chance, the value decides.
  const fd = checkLeg(propLeg("over"), { entry, index: 0 }, "fd");
  assert.equal(fd.bookProb, null);
  assert.equal(fd.verdict, "agrees");

  assert.equal(checkLeg(propLeg("under"), { entry, index: 1 }, "fd").verdict, "not-priced");
  assert.equal(checkLeg(propLeg("over", 70.5), undefined, "dk").verdict, "not-found");
  assert.equal(checkLeg(propLeg("over"), { entry: propEntry({ model: null }), index: 0 }, "dk").verdict, "no-model");
});

test("a moneyline leg uses the win chance blended toward that book's price", () => {
  const entry = mlEntry();
  const home = checkLeg(gameLeg("h2h", "home", null), { entry, index: 0 }, "dk");
  const book = (600 / 700) / (600 / 700 + 100 / 540);
  close(home.bookProb, book);
  close(home.modelProb, blendWinProb(0.89, book));
  const away = checkLeg(gameLeg("h2h", "away", null), { entry, index: 1 }, "dk");
  close(away.modelProb, 1 - blendWinProb(0.89, book));
});

test("a slip: the all-hit chance, fair price and book parlay price", () => {
  const prop = propEntry();
  const ml = mlEntry();
  const index = indexCatalogue([prop, ml]);
  const legs = [propLeg("over"), gameLeg("h2h", "home", null)];
  const check = checkSlip(legs, index, "dk");
  assert.equal(check.blocked, null);
  const p = check.legs[0].modelProb! * check.legs[1].modelProb!;
  close(check.modelProb, p);
  const decimal = (1 + 100 / 115) * (1 + 100 / 600);
  close(check.value, p * decimal - 1);
  assert.equal(check.bookPrice, Math.round((decimal - 1) * 100) >= 100 ? Math.round((decimal - 1) * 100) : -Math.round(100 / (decimal - 1)));
  assert.ok(check.fairPrice !== null);
});

test("a slip with two legs from one game, or a leg with no model, gets no combined chance", () => {
  const prop = propEntry();
  const index = indexCatalogue([prop, mlEntry(1)]);
  const sameGame = checkSlip([propLeg("over"), gameLeg("h2h", "home", null, 1)], index, "dk");
  assert.equal(sameGame.blocked, "same-game");
  assert.equal(sameGame.modelProb, null);
  assert.equal(sameGame.bookPrice, null);
  assert.equal(sameGame.legs.length, 2);

  const noModel = checkSlip([propLeg("over", 99.5)], index, "dk");
  assert.equal(noModel.blocked, "no-model");
  assert.equal(checkSlip([], index, "dk").blocked, "empty");
});

test("book counts and search: priced at the book, still to play, every word", () => {
  const entries = [propEntry(), mlEntry(), propEntry({ id: "x", startDate: "2026-10-01T00:00:00Z", search: "r. brown old" })];
  const counts = bookCounts({ books: [{ key: "dk", name: "DraftKings" }, { key: "fd", name: "FanDuel" }, { key: "mgm", name: "BetMGM" }], entries });
  assert.deepEqual(counts.map((b) => [b.key, b.count]), [["dk", 3], ["fd", 2]]);
  const now = new Date("2026-10-12T12:00:00Z");
  assert.deepEqual(searchCatalogue(entries, "dk", "brown rush", now).map((e) => e.id), ["p:1:10:rush_yards:62.50"]);
  assert.deepEqual(searchCatalogue(entries, "fd", "moneyline", now), []);
  assert.equal(searchCatalogue(entries, "dk", "", now).length, 2);
});

test("slip keys round-trip through a URL; junk is dropped", () => {
  const legs = [propLeg("over"), gameLeg("h2h", "home", null), gameLeg("spreads", "away", -14)];
  const keys = decodeSlipKeys(encodeSlipKeys(legs));
  assert.deepEqual(keys, legs.map(legKey));
  assert.deepEqual(parseLegKey(legKey(legs[2])), { kind: "game", gameId: 2, period: "full", market: "spreads", side: "away", line: -14 });
  assert.deepEqual(decodeSlipKeys("p:1:2:rush_yards:sideways:5~g:1:full:h2h:home:~<script>"), ["g:1:full:h2h:home:"]);
});
