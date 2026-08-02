/**
 * Tests for the hit-rate math.
 *
 *   npm run test
 *
 * Run by Node's built-in test runner, which executes TypeScript directly on
 * Node 24 — no test framework dependency, which CLAUDE.md §0 would have wanted
 * justified. `tsc --noEmit` still type-checks this file, so the two together
 * cover the same ground a framework would.
 *
 * WHAT IS WORTH TESTING HERE. Not that 3/5 is 60%. The failure modes that
 * actually matter are the ones where a wrong answer still looks like a number:
 * a push counted as a loss, "no games" rendered as 0%, or a window that counts
 * calendar weeks and silently scores a bye as a miss.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

// Relative, with the extension: Node resolves these itself and knows nothing
// about the `@/*` path alias, which only exists for tsc and the bundler. A
// type-only import may still use the alias — it is erased before Node sees it.
import type { PlayerGameLogRow } from "@/lib/core/types";

import { formatCount, formatLine } from "./format.ts";
import {
  formatHitRate,
  gradeGames,
  hitRate,
  splitByVenue,
  statValue,
} from "./hit-rate.ts";

function game(
  week: number,
  values: Partial<PlayerGameLogRow> = {},
): PlayerGameLogRow {
  return {
    playerId: 1,
    gameId: 1000 + week,
    season: 2025,
    week,
    positionGroup: "WR",
    isHome: true,
    opponentTeamId: 500 + week,
    opponentAbbreviation: "OPP",
    opponentSchool: "Opponent",
    startDate: null,
    neutralSite: false,
    passAttempts: null,
    passCompletions: null,
    passYards: null,
    passTds: null,
    interceptions: null,
    rushAttempts: null,
    rushYards: null,
    rushTds: null,
    targets: null,
    receptions: null,
    recYards: null,
    recTds: null,
    offensiveTds: null,
    ...values,
  };
}

test("statValue maps a market's stat column onto the log field", () => {
  assert.equal(statValue(game(1, { recYards: 62 }), "rec_yards"), 62);
  assert.equal(statValue(game(1, { receptions: 4 }), "receptions"), 4);
  assert.equal(statValue(game(1, { offensiveTds: 1 }), "offensive_tds"), 1);
});

test("an unmapped stat column reads as no data, not as zero", () => {
  // A market can be added to the catalogue by INSERT with no deploy. If that
  // happens, the chart must show nothing rather than a confident 0.
  assert.equal(statValue(game(1, { recYards: 62 }), "punt_yards"), null);
});

test("a zero is a real value and must not be confused with a missing one", () => {
  assert.equal(statValue(game(1, { recYards: 0 }), "rec_yards"), 0);
  assert.equal(statValue(game(1, {}), "rec_yards"), null);
});

test("games grade over, under and push against the line", () => {
  const games = [
    game(3, { recYards: 80 }),
    game(2, { recYards: 20 }),
    game(1, { recYards: 50 }),
  ];
  const graded = gradeGames(games, "rec_yards", 50, "over");

  assert.deepEqual(
    graded.map((g) => g.outcome),
    ["over", "under", "push"],
  );
});

test("hit is judged on the CALLED side, not on the over", () => {
  const games = [game(1, { recYards: 20 })];

  assert.equal(gradeGames(games, "rec_yards", 50, "over")[0].hit, false);
  assert.equal(gradeGames(games, "rec_yards", 50, "under")[0].hit, true);
});

test("a push is neither a hit nor a miss", () => {
  const graded = gradeGames([game(1, { recYards: 50 })], "rec_yards", 50, "over");
  assert.equal(graded[0].hit, null);
});

test("games come back most recent first", () => {
  const graded = gradeGames(
    [game(1, { recYards: 10 }), game(5, { recYards: 10 }), game(3, { recYards: 10 })],
    "rec_yards",
    5,
    "over",
  );
  assert.deepEqual(
    graded.map((g) => g.week),
    [5, 3, 1],
  );
});

test("games without the stat are dropped rather than graded as zero", () => {
  const graded = gradeGames(
    [game(2, { recYards: 60 }), game(1, {})],
    "rec_yards",
    50,
    "over",
  );
  assert.equal(graded.length, 1);
  assert.equal(graded[0].week, 2);
});

test("pushes leave the denominator, so they cannot inflate a side", () => {
  // Three overs, one push, one under against a whole-number line. Counting the
  // push as an under-hit — which the model's own `value > line` grading would
  // do — turns 3/4 into 3/5 and understates the over by 15 points.
  const graded = gradeGames(
    [
      game(5, { recYards: 60 }),
      game(4, { recYards: 60 }),
      game(3, { recYards: 60 }),
      game(2, { recYards: 50 }),
      game(1, { recYards: 40 }),
    ],
    "rec_yards",
    50,
    "over",
  );
  const summary = hitRate(graded, 5);

  assert.equal(summary.hits, 3);
  assert.equal(summary.decided, 4);
  assert.equal(summary.pushes, 1);
  assert.equal(summary.rate, 0.75);
});

test("the window counts appearances, not calendar weeks", () => {
  // Weeks 4 and 7 are missing — a bye and an injury. L3 must reach back to
  // week 2 rather than scoring the absences as misses.
  const graded = gradeGames(
    [
      game(8, { recYards: 60 }),
      game(6, { recYards: 60 }),
      game(5, { recYards: 20 }),
      game(2, { recYards: 60 }),
    ],
    "rec_yards",
    50,
    "over",
  );
  const summary = hitRate(graded, 3);

  assert.deepEqual(
    summary.games.map((g) => g.week),
    [8, 6, 5],
  );
  assert.equal(summary.decided, 3);
  assert.equal(summary.hits, 2);
});

test("a window longer than the record uses what exists", () => {
  const graded = gradeGames([game(1, { recYards: 60 })], "rec_yards", 50, "over");
  const summary = hitRate(graded, 10);

  assert.equal(summary.decided, 1);
  assert.equal(summary.rate, 1);
});

test("no decided games gives a null rate, never zero", () => {
  assert.equal(hitRate([], 5).rate, null);
  // All pushes: five games, nothing decided.
  const allPushes = gradeGames(
    [game(2, { recYards: 50 }), game(1, { recYards: 50 })],
    "rec_yards",
    50,
    "over",
  );
  assert.equal(hitRate(allPushes, 5).rate, null);
});

test("a null rate renders as a dash, because 0% is a different claim", () => {
  assert.equal(formatHitRate(null), "—");
  assert.equal(formatHitRate(0), "0%");
  assert.equal(formatHitRate(0.8), "80%");
  assert.equal(formatHitRate(0.666), "67%");
});

test("neutral sites count as neither home nor away", () => {
  const graded = gradeGames(
    [
      game(3, { recYards: 60, isHome: true }),
      game(2, { recYards: 60, isHome: false }),
      game(1, { recYards: 60, isHome: true, neutralSite: true }),
    ],
    "rec_yards",
    50,
    "over",
  );
  const split = splitByVenue(graded);

  assert.equal(split.home.length, 1);
  assert.equal(split.away.length, 1);
  assert.equal(split.neutral.length, 1);
});

// -----------------------------------------------------------------------------
// Formatting that has bitten us
// -----------------------------------------------------------------------------
test("a value rounding to zero never renders as negative zero", () => {
  // Quantiles come from bisecting a survival function, which for a discrete
  // count lands a hair below zero. "proj -0.0" reads as a broken number.
  assert.equal(formatLine(-0.04), "0.0");
  assert.equal(formatLine(-0), "0.0");
  assert.equal(formatLine(0), "0.0");
  assert.equal(formatLine(-1.2), "-1.2");
  assert.equal(formatLine(62.5), "62.5");
});

test("counts are grouped for a US audience regardless of server locale", () => {
  // A bare toLocaleString() on a non-US host renders 3788 as "3.788".
  assert.equal(formatCount(3788), "3,788");
  assert.equal(formatCount(999), "999");
});
