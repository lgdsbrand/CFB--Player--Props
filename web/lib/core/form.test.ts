/**
 * Tests for ungraded recent form — the no-line path.
 *
 *   npm run test
 *
 * WHAT IS WORTH TESTING HERE. Not that a mean is a sum over a count. The
 * failure modes that matter are the ones where a wrong answer still looks like
 * a number: a null stat counted as a zero (which drags every average down), a
 * mean of nothing rendered as 0.0 rather than a dash, last season's games
 * leaking into a figure labelled SZN, and an average formatted so it can pass
 * for a hit rate.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { PlayerGameLogRow } from "@/lib/core/types";

import {
  formatFormMean,
  formGames,
  formSeasonToDate,
  formSummary,
  formWindows,
} from "./form.ts";
import { priorSeasonCount, topUpFromPriorSeason } from "./hit-rate.ts";

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
    q1PassYards: null,
    q1RushAttempts: null,
    q1RushYards: null,
    q1RushTds: null,
    q1Targets: null,
    q1Receptions: null,
    q1RecYards: null,
    q1RecTds: null,
    q1OffensiveTds: null,
    snaps: null,
    teamTargets: null,
    teamRushAttempts: null,
    teamSnaps: null,
    ...values,
  } as PlayerGameLogRow;
}

// -----------------------------------------------------------------------------
// Building the sample
// -----------------------------------------------------------------------------

test("a game with no value for the market is dropped, not counted as zero", () => {
  // A receiver's log carries nulls for pass_yards. Counting those as zeroes
  // would halve his receiving average the moment the market changed.
  const form = formGames(
    [
      game(3, { recYards: 80 }),
      game(2, { recYards: null }),
      game(1, { recYards: 40 }),
    ],
    "rec_yards",
  );

  assert.equal(form.length, 2);
  assert.equal(formSummary(form, 5).mean, 60);
});

test("an unmapped market yields no games rather than a wrong number", () => {
  const form = formGames([game(1, { recYards: 80 })], "not_a_market");
  assert.deepEqual(form, []);
});

test("form is ordered most recent first, like the graded path", () => {
  const form = formGames(
    [game(1, { recYards: 10 }), game(3, { recYards: 30 }), game(2, { recYards: 20 })],
    "rec_yards",
  );

  assert.deepEqual(
    form.map((g) => g.week),
    [3, 2, 1],
  );
});

// -----------------------------------------------------------------------------
// The summary
// -----------------------------------------------------------------------------

test("a window longer than the log uses what exists and says how many", () => {
  const form = formGames(
    [game(2, { recYards: 50 }), game(1, { recYards: 70 })],
    "rec_yards",
  );
  const summary = formSummary(form, 10);

  assert.equal(summary.window, 10);
  assert.equal(summary.played, 2);
  assert.equal(summary.mean, 60);
});

test("nothing played is a null mean, never 0", () => {
  // 0.0 is a real average — a player who was held to nothing twice. "No games"
  // is a different statement and has to render differently.
  const summary = formSummary([], 5);

  assert.equal(summary.played, 0);
  assert.equal(summary.mean, null);
  assert.equal(formatFormMean(summary.mean), "—");
});

test("a genuine zero average is reported as 0.0, not as a dash", () => {
  const form = formGames(
    [game(2, { recYards: 0 }), game(1, { recYards: 0 })],
    "rec_yards",
  );

  assert.equal(formSummary(form, 5).mean, 0);
  assert.equal(formatFormMean(0), "0.0");
});

test("min and max travel with the mean, because a mean hides the spread", () => {
  const form = formGames(
    [game(3, { recYards: 12 }), game(2, { recYards: 108 }), game(1, { recYards: 60 })],
    "rec_yards",
  );
  const summary = formSummary(form, 3);

  assert.equal(summary.mean, 60);
  assert.equal(summary.min, 12);
  assert.equal(summary.max, 108);
});

test("the window takes the most recent games, not the first in the array", () => {
  const form = formGames(
    [
      game(1, { recYards: 0 }),
      game(2, { recYards: 0 }),
      game(3, { recYards: 90 }),
      game(4, { recYards: 90 }),
    ],
    "rec_yards",
  );

  // Weeks 4 and 3 are the recent pair.
  assert.equal(formSummary(form, 2).mean, 90);
});

// -----------------------------------------------------------------------------
// Windows and the season column
// -----------------------------------------------------------------------------

test("form windows mirror the graded windows' labels", () => {
  const form = formGames([game(1, { recYards: 50 })], "rec_yards");
  const splits = formWindows(form, [5, 10]);

  assert.deepEqual(
    splits.map((s) => s.label),
    ["L5", "L10"],
  );
  assert.deepEqual(
    splits.map((s) => s.key),
    ["l5", "l10"],
  );
});

test("the season figure excludes last season, which the sample deliberately holds", () => {
  // On the NFL a young season is topped up from last season so L5 is not mostly
  // empty. Those games must not reach a column labelled SZN.
  const form = formGames(
    [
      game(1, { season: 2026, recYards: 100 }),
      game(17, { season: 2025, recYards: 20 }),
      game(16, { season: 2025, recYards: 20 }),
    ],
    "rec_yards",
  );

  const szn = formSeasonToDate(form, 2026);
  assert.ok(szn !== null);
  assert.equal(szn.played, 1);
  assert.equal(szn.mean, 100);

  // And a player with nothing yet this season gets null, not last season's
  // figure under this season's heading.
  const noneYet = formGames([game(17, { season: 2025, recYards: 20 })], "rec_yards");
  assert.equal(formSeasonToDate(noneYet, 2026), null);
});

// -----------------------------------------------------------------------------
// The shared sample helpers, now generic over graded and ungraded rows
// -----------------------------------------------------------------------------

test("the ungraded sample tops up from last season by the same rule as the graded one", () => {
  const form = formGames(
    [
      game(1, { season: 2026, recYards: 70 }),
      game(17, { season: 2025, recYards: 50 }),
      game(16, { season: 2025, recYards: 40 }),
      game(15, { season: 2025, recYards: 30 }),
    ],
    "rec_yards",
  );

  const sample = topUpFromPriorSeason(form, 2026, 3);

  assert.equal(sample.length, 3);
  assert.deepEqual(
    sample.map((g) => [g.season, g.week]),
    [
      [2026, 1],
      [2025, 17],
      [2025, 16],
    ],
  );
  assert.equal(priorSeasonCount(sample, 2026), 2);
});

// -----------------------------------------------------------------------------
// Formatting — the one job is not looking like a hit rate
// -----------------------------------------------------------------------------

test("a mean never renders with a percent sign and always keeps a decimal", () => {
  // "62.4" beside "61%" is unambiguous; a bare "62" is not. The decimal is the
  // cheapest signal available that this is not a rate.
  assert.equal(formatFormMean(62.44), "62.4");
  assert.equal(formatFormMean(1), "1.0");
  assert.equal(formatFormMean(100), "100.0");
  assert.ok(!formatFormMean(62.44).includes("%"));
});
