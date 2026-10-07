import assert from "node:assert/strict";
import { test } from "node:test";

import { fairAmerican, splitWagers, wagerLabel, type EvWager } from "./ev.ts";

function w(overrides: Partial<EvWager>): EvWager {
  return {
    gameId: 1,
    market: "spreads",
    side: "home",
    line: -10,
    price: -105,
    fairProb: 0.5,
    ev: 0.01,
    bookKey: "draftkings",
    bookName: "DraftKings",
    role: "retail",
    capturedAt: "2026-10-07T06:38:00Z",
    ...overrides,
  };
}

test("labels read as a bettor reads them, from the side bet", () => {
  assert.equal(wagerLabel(w({ side: "home", line: -10 }), "TROY", "USM"), "TROY -10.0");
  assert.equal(wagerLabel(w({ side: "away", line: -10 }), "TROY", "USM"), "USM +10.0");
  assert.equal(wagerLabel(w({ market: "totals", side: "under", line: 48.5 }), "TROY", "USM"), "Under 48.5");
  assert.equal(wagerLabel(w({ market: "h2h", side: "away", line: null }), "TROY", "USM"), "USM ML");
});

test("fair American price from a probability", () => {
  assert.equal(fairAmerican(0.5), -100);
  assert.equal(fairAmerican(0.6), -150);
  assert.equal(fairAmerican(0.4), 150);
  assert.equal(fairAmerican(0.2646), 278);
});

test("exchanges are kept apart, each list best first", () => {
  const { books, exchanges } = splitWagers([
    w({ bookKey: "kalshi", role: "exchange", ev: 0.037 }),
    w({ bookKey: "betrivers", ev: 0.032 }),
    w({ bookKey: "unibet_se", role: "other", ev: 0.032, price: 290 }),
    w({ bookKey: "novig", role: "exchange", ev: 0.02 }),
  ]);
  assert.deepEqual(books.map((b) => b.bookKey), ["unibet_se", "betrivers"]);
  assert.deepEqual(exchanges.map((b) => b.bookKey), ["kalshi", "novig"]);
});
