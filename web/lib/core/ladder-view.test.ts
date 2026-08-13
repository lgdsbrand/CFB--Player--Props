/**
 * Tests for the alternate-line ladder's presentation logic.
 *
 * THE ONE THAT MATTERS MOST is the book-line placement. Rung spacing widens to
 * cover a broad distribution — passing yards routinely step to 75 against a
 * nominal 25 — so a book line almost never coincides with a rung. A UI written
 * to look for equality would mark nothing on the overwhelming majority of rows
 * and would look perfectly fine on the handful where it happened to line up.
 *
 * The parser is tested against malformed input on purpose: `projections.ladder`
 * is jsonb carrying only an outer array CHECK, so element shape is a worker-side
 * guarantee, and the read layer is the wrong place to discover that by rendering
 * NaN into a percentage.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LADDER_CAVEAT,
  ladderView,
  parseLadder,
  rungConfidence,
  rungSide,
} from "./ladder-view.ts";

/** A real shape: a back the model likes well above the posted line. */
const RUNGS = [
  { line: 40.5, prob_over: 0.91 },
  { line: 60.5, prob_over: 0.78 },
  { line: 80.5, prob_over: 0.58 },
  { line: 100.5, prob_over: 0.36 },
  { line: 120.5, prob_over: 0.19 },
];

test("parseLadder returns rungs ascending by line", () => {
  const parsed = parseLadder([...RUNGS].reverse());
  assert.deepEqual(
    parsed.map((r) => r.line),
    [40.5, 60.5, 80.5, 100.5, 120.5],
  );
});

test("parseLadder drops malformed rungs rather than rendering NaN", () => {
  const parsed = parseLadder([
    { line: 40.5, prob_over: 0.91 },
    { line: "60.5", prob_over: 0.78 }, // string, from a bad writer
    { line: 80.5 }, // missing probability
    { line: 100.5, prob_over: null },
    null,
    "nonsense",
    { line: Number.NaN, prob_over: 0.5 },
  ]);
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].line, 40.5);
});

test("parseLadder treats a non-array as an empty ladder", () => {
  assert.deepEqual(parseLadder(null), []);
  assert.deepEqual(parseLadder(undefined), []);
  assert.deepEqual(parseLadder({ line: 40.5 }), []);
});

test("ladderView is null when there are no rungs, so the panel can skip", () => {
  assert.equal(ladderView(null, 60.5, 90), null);
  assert.equal(ladderView([], 60.5, 90), null);
});

test("ladderView takes PARSED rungs — the bug that shipped nothing at all", () => {
  // `ladderView` used to accept `unknown` and parse internally. The read layer
  // already parses at the database boundary, so the second parse looked for
  // `prob_over` on objects carrying `probOver`, dropped every rung, and returned
  // null. The panel then rendered nothing on every row in the product, and the
  // type checker could not see it because `unknown` accepts anything.
  //
  // Every other test here now goes through `parseLadder` first, which is the real
  // pipeline. This one pins the boundary directly.
  const parsed = parseLadder(RUNGS);
  assert.equal(parsed.length, 5);
  const view = ladderView(parsed, 60.5, 90);
  assert.ok(view, "parsed rungs must produce a view");
  assert.equal(view.rungs.length, 5);
});

test("ladderView sorts rungs it is handed out of order", () => {
  const view = ladderView(
    [
      { line: 80.5, probOver: 0.58 },
      { line: 40.5, probOver: 0.91 },
      { line: 60.5, probOver: 0.78 },
    ],
    null,
    null,
  );
  assert.deepEqual(view!.rungs.map((r) => r.line), [40.5, 60.5, 80.5]);
});

test("the book line is located BETWEEN rungs, not only when it matches", () => {
  // 64.5 sits between the 60.5 and 80.5 rungs — the normal case once spacing
  // widens. The marker must land on the first rung at or above it.
  const view = ladderView(parseLadder(RUNGS), 64.5, 90);
  assert.ok(view);
  assert.equal(view.bookIndex, 2);
  assert.equal(view.rungs[view.bookIndex!].line, 80.5);
  assert.equal(view.bookLineOnRung, false);
});

test("an exact match is reported as one, and still gives an index", () => {
  const view = ladderView(parseLadder(RUNGS), 60.5, 90);
  assert.ok(view);
  assert.equal(view.bookIndex, 1);
  assert.equal(view.bookLineOnRung, true);
});

test("a line below every rung marks the first rung", () => {
  const view = ladderView(parseLadder(RUNGS), 10.5, 90);
  assert.ok(view);
  assert.equal(view.bookIndex, 0);
});

test("a line ABOVE every rung marks nothing", () => {
  // Deliberate: the marker means "the book sits here among these". Pinning it to
  // the top rung would misplace it by an unknown distance and read as agreement
  // the ladder does not actually show.
  const view = ladderView(parseLadder(RUNGS), 400.5, 90);
  assert.ok(view);
  assert.equal(view.bookIndex, null);
});

test("no book line means no marker, and the ladder still renders", () => {
  const view = ladderView(parseLadder(RUNGS), null, 90);
  assert.ok(view);
  assert.equal(view.bookIndex, null);
  assert.equal(view.bookLineOnRung, false);
  assert.equal(view.rungs.length, 5);
});

test("the median marker picks the nearest rung, not the nearest above", () => {
  // 90 is closer to 80.5 than to 100.5, so a naive findIndex(line >= median)
  // would put it on the wrong rung.
  const view = ladderView(parseLadder(RUNGS), null, 90);
  assert.ok(view);
  assert.equal(view.rungs[view.medianIndex!].line, 80.5);
});

test("the median marker survives a median outside the rung range", () => {
  const low = ladderView(parseLadder(RUNGS), null, 5);
  assert.equal(low!.rungs[low!.medianIndex!].line, 40.5);
  const high = ladderView(parseLadder(RUNGS), null, 500);
  assert.equal(high!.rungs[high!.medianIndex!].line, 120.5);
});

test("rungSide and rungConfidence agree with the board's 0.5 boundary", () => {
  assert.equal(rungSide(0.78), "over");
  assert.equal(rungSide(0.5), "over"); // ties go over, as picks.side does
  assert.equal(rungSide(0.36), "under");

  assert.equal(rungConfidence(0.78), 0.78);
  assert.ok(Math.abs(rungConfidence(0.36) - 0.64) < 1e-9);
});

test("the caveat names the thing that is actually unproven", () => {
  // Not a wording test. The caveat exists because the model has NOT been shown
  // to beat a real closing line, and a ladder asserts mispricing at several
  // lines rather than one. If someone softens this to a generic disclaimer, the
  // specific claim stops being made.
  assert.match(LADDER_CAVEAT, /not a recommendation/i);
  assert.match(LADDER_CAVEAT, /under/i);
  assert.ok(LADDER_CAVEAT.length > 120);
});
