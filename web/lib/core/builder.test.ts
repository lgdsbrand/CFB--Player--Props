/**
 * Tests for the bet builder: which picks may enter, that a slip never holds
 * two picks on one player (or one game, unless asked), and that a target is
 * reached when the pool allows it and approached when it does not.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  booksByCoverage,
  buildSlip,
  eligible,
  legHitRate,
  nextShuffleSeed,
  passes,
  slipKey,
  type BuilderCandidate,
  type BuilderSettings,
} from "./builder.ts";

let nextId = 1;

function prop(overrides: Partial<BuilderCandidate> & { price?: number; gameId?: number } = {}): BuilderCandidate {
  const id = nextId++;
  const { price = -110, gameId = id, ...rest } = overrides;
  return {
    leg: {
      kind: "prop",
      gameId,
      playerId: id,
      marketKey: "rec_yards",
      side: "over",
      line: 50.5,
      binary: false,
      player: `Player ${id}`,
      market: "Rec Yds",
      matchup: "AAA @ BBB",
      startDate: null,
    },
    playerId: id,
    marketKey: "rec_yards",
    prob: 0.6,
    edge: 0.06,
    hits: 4,
    decided: 5,
    prices: { draftkings: price },
    ...rest,
  };
}

function settings(overrides: Partial<BuilderSettings> = {}): BuilderSettings {
  return {
    book: "draftkings",
    targetOdds: null,
    legs: "auto",
    minProb: 0.5,
    minHitRate: null,
    minDecided: 4,
    minPrice: -500,
    maxPrice: 500,
    markets: [],
    side: null,
    edgesOnly: false,
    edgeThreshold: 0.05,
    gameIds: [],
    sameGame: false,
    ...overrides,
  };
}

test("a pick needs a price at the chosen book, inside the price range", () => {
  const c = prop({ price: -250 });
  assert.equal(passes(c, settings()), true);
  assert.equal(passes(c, settings({ book: "fanduel" })), false);
  assert.equal(passes(c, settings({ minPrice: -200 })), false);
  assert.equal(passes(prop({ price: 250 }), settings({ maxPrice: 200 })), false);
});

test("confidence, hit rate, side, market, edge and game filters each apply", () => {
  const c = prop({ prob: 0.62, hits: 4, decided: 5, edge: 0.03 });
  assert.equal(passes(c, settings({ minProb: 0.65 })), false);
  assert.equal(passes(c, settings({ minHitRate: 0.8 })), true);
  assert.equal(passes(c, settings({ minHitRate: 1 })), false);
  assert.equal(passes(c, settings({ side: "under" })), false);
  assert.equal(passes(c, settings({ markets: ["pass_yards"] })), false);
  assert.equal(passes(c, settings({ edgesOnly: true })), false);
  assert.equal(passes(c, settings({ gameIds: [c.leg.gameId + 1000] })), false);
});

test("a hit rate needs enough decided games, and game lines have none", () => {
  assert.equal(legHitRate({ hits: 3, decided: 3 }, 4), null);
  assert.equal(legHitRate({ hits: 3, decided: 4 }, 4), 0.75);
  assert.equal(legHitRate({ hits: null, decided: null }, 4), null);
  // So a hit-rate floor leaves out anything without one.
  assert.equal(passes(prop({ hits: null, decided: null }), settings({ minHitRate: 0.6 })), false);
});

test("anytime TD has no edge, so edges-only leaves it out", () => {
  assert.equal(passes(prop({ edge: null }), settings({ edgesOnly: true })), false);
});

test("eligible sorts by model probability", () => {
  const low = prop({ prob: 0.55 });
  const high = prop({ prob: 0.7 });
  assert.deepEqual(eligible([low, high], settings()).map((c) => c.prob), [0.7, 0.55]);
});

test("without a target, auto builds three of the strongest picks", () => {
  const pool = [0.7, 0.66, 0.64, 0.6, 0.58].map((prob) => prop({ prob }));
  const slip = buildSlip(pool, settings());
  assert.ok(slip);
  assert.deepEqual(slip.picks.map((p) => p.candidate.prob), [0.7, 0.66, 0.64]);
  assert.equal(slip.onTarget, true);
  assert.ok(Math.abs(slip.allHitProb - 0.7 * 0.66 * 0.64) < 1e-9);
});

test("one pick per game unless same-game is allowed", () => {
  const a = prop({ prob: 0.7, gameId: 1 });
  const b = prop({ prob: 0.69, gameId: 1 });
  const c = prop({ prob: 0.6, gameId: 2 });
  const apart = buildSlip([a, b, c], settings({ legs: 2 }));
  assert.deepEqual(apart?.picks.map((p) => p.candidate), [a, c]);
  assert.equal(apart?.sameGame, false);
  const together = buildSlip([a, b, c], settings({ legs: 2, sameGame: true }));
  assert.deepEqual(together?.picks.map((p) => p.candidate), [a, b]);
  assert.equal(together?.sameGame, true);
});

test("never two picks on one player", () => {
  const a = prop({ prob: 0.7 });
  const b = { ...prop({ prob: 0.69 }), playerId: a.playerId };
  const c = prop({ prob: 0.6 });
  const slip = buildSlip([a, b, c], settings({ legs: 2, sameGame: true }));
  assert.deepEqual(slip?.picks.map((p) => p.candidate), [a, c]);
});

test("a target is reached by swapping in longer prices", () => {
  // Five short favourites and a few plus-money picks: +300 needs some of both.
  const pool = [
    ...[0.72, 0.71, 0.7, 0.69, 0.68].map((prob) => prop({ prob, price: -250 })),
    ...[0.56, 0.55, 0.54].map((prob) => prop({ prob, price: 120 })),
  ];
  const slip = buildSlip(pool, settings({ targetOdds: 300, legs: 3 }));
  assert.ok(slip);
  assert.equal(slip.picks.length, 3);
  assert.equal(slip.onTarget, true, `got ${slip.american}`);
  assert.ok(slip.decimal >= 3.6 && slip.decimal <= 4.4);
});

test("auto picks the size that reaches the target with the best chance", () => {
  const pool = [0.7, 0.69, 0.68, 0.67, 0.66, 0.65].map((prob) => prop({ prob, price: -110 }));
  // -110 three times is about +596; twice is about +264.
  const slip = buildSlip(pool, settings({ targetOdds: 264 }));
  assert.equal(slip?.picks.length, 2);
  assert.equal(slip?.onTarget, true);
});

test("an unreachable target returns the closest slip, flagged", () => {
  const pool = [0.7, 0.69, 0.68].map((prob) => prop({ prob, price: -400 }));
  const slip = buildSlip(pool, settings({ targetOdds: 1000, legs: 3 }));
  assert.ok(slip);
  assert.equal(slip.onTarget, false);
});

test("too few picks for the size asked builds nothing", () => {
  assert.equal(buildSlip([prop()], settings({ legs: 2 })), null);
  assert.equal(buildSlip([], settings()), null);
});

test("a shuffle is repeatable and stays within the settings", () => {
  const pool = Array.from({ length: 30 }, (_, i) => prop({ prob: 0.55 + i * 0.005 }));
  const s = settings({ legs: 3, minProb: 0.6 });
  const one = buildSlip(pool, s, 7);
  const again = buildSlip(pool, s, 7);
  assert.deepEqual(one?.picks.map((p) => p.candidate), again?.picks.map((p) => p.candidate));
  assert.ok(one?.picks.every((p) => p.candidate.prob >= 0.6));
});

test("books are offered most-covered first", () => {
  const pool = [
    prop({ prices: { fanduel: -110, draftkings: -110 } }),
    prop({ prices: { fanduel: -110 } }),
  ];
  assert.deepEqual(
    booksByCoverage(pool, { fanduel: "FanDuel", draftkings: "DraftKings" }).map((b) => b.key),
    ["fanduel", "draftkings"],
  );
});

test("shuffle moves to a different slip when one exists", () => {
  const pool = Array.from({ length: 12 }, (_, i) => prop({ prob: 0.6 + i * 0.01 }));
  const s = settings({ legs: 2 });
  const next = nextShuffleSeed(pool, s, 0);
  assert.notEqual(slipKey(buildSlip(pool, s, next)), slipKey(buildSlip(pool, s, 0)));
  // With exactly two picks there is no other slip; it still moves on.
  assert.equal(nextShuffleSeed(pool.slice(0, 2), s, 0), 1);
});
