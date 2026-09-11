/**
 * Tests for the read layer's PostgREST predicate builders.
 *
 *   npm run test
 *
 * These are strings, and a wrong string still returns rows — just the wrong
 * ones. Nothing downstream can tell a log missing last season's recent games
 * from a player who did not play them.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { thisAndLastSeason } from "./query.ts";

test("the week cut applies to this season only, never to last season", () => {
  // The trap: widening `.eq("season")` to two seasons while keeping
  // `.lt("week", 9)` would drop last season's weeks 9 onward — its most recent
  // games, which are exactly the ones a top-up wants.
  assert.equal(
    thisAndLastSeason(2026, 9),
    "season.eq.2025,and(season.eq.2026,week.lt.9)",
  );
});

test("with no week cut, both seasons are taken whole", () => {
  assert.equal(thisAndLastSeason(2026), "season.eq.2025,season.eq.2026");
});
