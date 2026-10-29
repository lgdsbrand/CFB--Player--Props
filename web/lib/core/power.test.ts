/** Tests for the games page's power-rating helpers (`lib/core/power.ts`). */
import assert from "node:assert/strict";
import { test } from "node:test";

import { formatPower, gameScoreTier } from "./power.ts";

test("game score bands at 65 and 80", () => {
  assert.equal(gameScoreTier(64), null);
  assert.equal(gameScoreTier(65), "good");
  assert.equal(gameScoreTier(79), "good");
  assert.equal(gameScoreTier(80), "hot");
});

test("a rating is signed to one decimal and never prints -0.0", () => {
  assert.equal(formatPower(7.44), "+7.4");
  assert.equal(formatPower(-0.31), "-0.3");
  assert.equal(formatPower(-0.04), "0.0");
  assert.equal(formatPower(0.04), "0.0");
  assert.equal(formatPower(8), "+8.0");
});
