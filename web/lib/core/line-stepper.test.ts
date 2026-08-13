/**
 * Tests for stepping the hit-rate chart's line.
 *
 * THE ONE THAT MATTERS MOST is that the stepper at offset 0 grades identically
 * to the server. The page renders the chart from `gradeGames`, and the stepper
 * re-grades in the browser; if the two disagreed about a push, the chart would
 * change colour the instant a reader touched a control and then changed back,
 * with no explanation and nothing failing.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { gradeGames } from "./hit-rate.ts";
import {
  MAX_OFFSET,
  offsetBounds,
  regrade,
  steppedLine,
  tally,
} from "./line-stepper.ts";
import type { PlayerGameLogRow } from "./types.ts";

function game(week: number, rushYards: number): PlayerGameLogRow {
  return {
    playerId: 1,
    gameId: week,
    season: 2025,
    week,
    opponentTeamId: 9,
    opponentAbbreviation: "OPP",
    isHome: true,
    neutralSite: false,
    rushYards,
  } as PlayerGameLogRow;
}

// Values chosen so 64.5 gives a mix, and 65 lands a push exactly on the line.
const GAMES = [game(1, 40), game(2, 65), game(3, 90), game(4, 120), game(5, 55)];
const VALUES = GAMES.map((g) => ({ value: g.rushYards as number }));

test("offset zero grades exactly as the server does", () => {
  for (const side of ["over", "under"] as const) {
    const server = gradeGames(GAMES, "rush_yards", 65, side);
    const client = regrade(VALUES, steppedLine(65, 0, 2.5), side);

    // gradeGames sorts most-recent-first; compare as multisets of (value, hit).
    const asPairs = (rows: { value: number; hit: boolean | null }[]) =>
      rows.map((r) => `${r.value}:${r.hit}`).sort();

    assert.deepEqual(asPairs(client), asPairs(server), `side=${side}`);
  }
});

test("a value landing exactly on the line is a push, not an under", () => {
  // The whole reason `outcomeFor` is shared rather than reimplemented: this is
  // the one case a second copy would be free to get wrong, and the one nobody
  // would notice.
  assert.equal(regrade([{ value: 65 }], 65, "over")[0].hit, null);
  assert.equal(regrade([{ value: 65 }], 65, "under")[0].hit, null);
});

test("stepping up makes an over harder and an under easier", () => {
  // Values are 40, 65, 90, 120, 55.
  assert.equal(steppedLine(64.5, 4, 2.5), 74.5);

  const overHits = (offset: number) =>
    tally(regrade(VALUES, steppedLine(64.5, offset, 2.5), "over")).hits;
  const underHits = (offset: number) =>
    tally(regrade(VALUES, steppedLine(64.5, offset, 2.5), "under")).hits;

  assert.equal(overHits(0), 3); // 65, 90, 120
  assert.equal(overHits(4), 2); // 90, 120 clear 74.5
  assert.equal(overHits(12), 1); // only 120 clears 94.5

  // The mirror image, and the reason the two are worth asserting together:
  // every game the over loses, the under wins.
  assert.equal(underHits(0), 2);
  assert.equal(underHits(4), 3);
  assert.equal(underHits(12), 4);
});

test("repeated stepping does not drift off the grid", () => {
  // Six steps of 0.25 is 1.5000000000000002 without the rounding, which renders
  // as a line no book would post.
  assert.equal(steppedLine(0, 6, 0.25), 1.5);
  assert.equal(steppedLine(64.5, 3, 2.5), 72);
  assert.equal(steppedLine(0.5, 1, 0.1), 0.6);
});

test("the line is floored at zero rather than going negative", () => {
  // Every market here is a count or a yardage. A negative line is not a harder
  // bet, it is one every game clears.
  assert.equal(steppedLine(2.5, -6, 2.5), 0);
  assert.equal(steppedLine(1, -1, 5), 0);
});

test("the bounds stop BEFORE the line reaches zero", () => {
  // So the control can disable a button instead of offering a step that
  // silently does nothing. Zero itself is excluded, not clamped to: "over 0
  // rushing yards" is cleared by every game that happened and says nothing.
  // 10 steps down by 2.5 reaches 2.5 at -3; -4 would be 0.
  assert.deepEqual(offsetBounds(10, 2.5), { min: -3, max: MAX_OFFSET });
  // Already one step from zero, so it cannot go down at all.
  assert.deepEqual(offsetBounds(2.5, 2.5), { min: 0, max: MAX_OFFSET });
  // A step larger than the line is the same case.
  assert.deepEqual(offsetBounds(0.5, 5), { min: 0, max: MAX_OFFSET });
  // Never further than the cap, however small the step.
  assert.equal(offsetBounds(1000, 1).min, -MAX_OFFSET);
});

test("pushes leave the denominator rather than counting as losses", () => {
  const t = tally(regrade(VALUES, 65, "over"));
  assert.equal(t.pushes, 1);
  assert.equal(t.decided, 4);
  assert.equal(t.hits, 2);
  assert.equal(t.rate, 0.5);
});

test("a tally of nothing decided is null, not zero", () => {
  // 0% and "no games decided" are different statements and read differently.
  const t = tally([{ hit: null }, { hit: null }]);
  assert.equal(t.rate, null);
  assert.equal(t.decided, 0);
});

test("the called side does not flip as the line moves", () => {
  // Re-deciding the side at each step would redraw the chart green wherever a
  // reader pushed it, which is the flattering-number problem the panel warns
  // about.
  const pushedFar = regrade(VALUES, steppedLine(64.5, 6, 25), "over");
  assert.equal(tally(pushedFar).hits, 0);
  assert.ok(pushedFar.every((p) => p.hit === false));
});
