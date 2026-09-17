import assert from "node:assert/strict";
import test from "node:test";

import {
  MARKET_SCOPES,
  hasScope,
  isDerived,
  marketsInScope,
  resolveMarketScope,
  scopeKeys,
} from "./market-scope.ts";
import type { Market, PositionGroup } from "@/lib/core/types";

function market(
  key: string,
  overrides: Partial<Market> = {},
): Market {
  return {
    key,
    displayName: key,
    shortLabel: null,
    emoji: null,
    statColumn: key,
    isBinary: false,
    defaultLine: null,
    unit: "yards",
    sortOrder: 100,
    ladderStep: 5,
    positions: ["WR"] as PositionGroup[],
    parentMarketKey: null,
    publishesCall: true,
    ...overrides,
  };
}

const REC_YARDS = market("rec_yards");
const RUSH_YARDS = market("rush_yards", { positions: ["RB"] as PositionGroup[] });
const Q1_REC_YARDS = market("q1_rec_yards", {
  parentMarketKey: "rec_yards",
  publishesCall: false,
});

const NFL = [REC_YARDS, RUSH_YARDS, Q1_REC_YARDS];
const COLLEGE = [REC_YARDS, RUSH_YARDS];

// -----------------------------------------------------------------------------
// What makes a market belong to a scope
// -----------------------------------------------------------------------------

test("a scope is decided by the parent link, not by the key's spelling", () => {
  // `q1_` is a naming convention and conventions are not enforced. The database
  // says what a market is derived from; guessing it from the key would put a
  // hand-inserted market in whichever list its name happened to suggest.
  assert.equal(isDerived(Q1_REC_YARDS), true);
  assert.equal(isDerived(market("q1_looks_derived_but_is_not")), false);
});

test("the two scopes partition the catalogue — no market is in both or neither", () => {
  const full = marketsInScope(NFL, "full").map((m) => m.key);
  const q1 = marketsInScope(NFL, "q1").map((m) => m.key);

  assert.deepEqual(full, ["rec_yards", "rush_yards"]);
  assert.deepEqual(q1, ["q1_rec_yards"]);
  assert.equal(full.length + q1.length, NFL.length);
  assert.equal(
    full.some((key) => q1.includes(key)),
    false,
  );
});

test("scope is independent of whether a market publishes a call", () => {
  // They coincide TODAY — every derived market also withholds its call — and
  // the whole point of keeping them apart is that the day a first-quarter model
  // earns its call, the scope must not disappear with it.
  const speaking = market("q1_rush_yards", {
    parentMarketKey: "rush_yards",
    publishesCall: true,
  });

  assert.equal(isDerived(speaking), true);
  assert.deepEqual(
    marketsInScope([REC_YARDS, speaking], "q1").map((m) => m.key),
    ["q1_rush_yards"],
  );
});

// -----------------------------------------------------------------------------
// A sport with nothing in a scope
// -----------------------------------------------------------------------------

test("college has no first-quarter scope, because no college book posts one", () => {
  assert.equal(hasScope(COLLEGE, "q1"), false);
  assert.equal(hasScope(COLLEGE, "full"), true);
  assert.equal(hasScope(NFL, "q1"), true);
});

test("an empty scope yields an empty key list, which is not the same as no filter", () => {
  // The caller passes this straight to `.in("market_key", ...)`. An empty array
  // has to mean "nothing qualifies" — if it were read as "no restriction", a
  // hand-edited ?scope=q1 on the college board would show the WHOLE board under
  // a first-quarter heading, which is worse than showing nothing.
  assert.deepEqual(scopeKeys(COLLEGE, "q1"), []);
});

// -----------------------------------------------------------------------------
// Reading the URL
// -----------------------------------------------------------------------------

test("only the exact value selects the first-quarter scope", () => {
  assert.equal(resolveMarketScope("q1"), "q1");
  assert.equal(resolveMarketScope(undefined), "full");
  assert.equal(resolveMarketScope("full"), "full");
  // Degrades rather than throwing, like every other board parameter: these URLs
  // get shared, bookmarked and truncated.
  assert.equal(resolveMarketScope("Q1"), "full");
  assert.equal(resolveMarketScope("first-quarter"), "full");
  assert.equal(resolveMarketScope(["q1", "full"]), "q1");
});

// -----------------------------------------------------------------------------
// What the scope says on screen
// -----------------------------------------------------------------------------

test("the first-quarter list carries its own explanation, the full board does not", () => {
  // The blurb travels with the LIST because a shared link arrives at the
  // destination having never shown the control that would have explained it.
  // The full-game board needs no such note — it behaves as the product always
  // has.
  assert.equal(MARKET_SCOPES.full.blurb, null);
  assert.notEqual(MARKET_SCOPES.q1.blurb, null);
  assert.match(String(MARKET_SCOPES.q1.blurb), /No model call/);
});
