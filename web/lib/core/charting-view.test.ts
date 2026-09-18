/**
 * Defensive tendencies (migration 0072).
 *
 * Scope: the banding rule, and the one property the whole module exists to
 * hold — that this ramp never reuses the colours that mean good and bad
 * everywhere else in the app.
 */

import assert from "node:assert/strict";
import { test, describe } from "node:test";

import {
  blitzStyle,
  blitzStyleFor,
  formatRate,
  sampleNote,
  sportHasCharting,
} from "./charting-view.ts";
import type { DefenseCharting } from "./types.ts";

function charting(values: Partial<DefenseCharting> = {}): DefenseCharting {
  return {
    defenseTeamId: 1,
    season: 2026,
    asOfWeek: 2,
    games: 1,
    dropbacks: 35,
    boxPlays: 60,
    blitzRate: 0.3,
    blitzRank: 16,
    meanPassRushers: 4.3,
    heavyBoxRate: 0.33,
    ...values,
  };
}

describe("the style ramp is never a verdict", () => {
  test("no band borrows the hit/miss colours", () => {
    // THE POINT OF THE MODULE. `positive` and `negative` mean over and under
    // everywhere else here, so a reader seeing green on a blitz rate would take
    // it as a recommendation the model has never made.
    for (const rank of [1, 16, 32]) {
      const tone = blitzStyle(rank, 32).tone;
      assert.ok(!tone.includes("positive"), tone);
      assert.ok(!tone.includes("negative"), tone);
    }
  });

  test("the two ends are visually distinct", () => {
    assert.notEqual(blitzStyle(1, 32).tone, blitzStyle(32, 32).tone);
  });

  test("every band says what it is in words", () => {
    // The rank runs the opposite way to the one above it on the page, so the
    // number cannot be the only thing carrying the meaning.
    for (const rank of [1, 16, 32]) {
      assert.ok(blitzStyle(rank, 32).label.length > 0);
    }
  });
});

describe("banding by thirds of the rated field", () => {
  test("the top third is blitz-heavy and the bottom third blitz-light", () => {
    assert.equal(blitzStyle(1, 32).band, "heavy");
    assert.equal(blitzStyle(10, 32).band, "heavy");
    assert.equal(blitzStyle(16, 32).band, "average");
    assert.equal(blitzStyle(32, 32).band, "light");
    assert.equal(blitzStyle(30, 32).band, "light");
  });

  test("the bands follow the field, not a fixed rate", () => {
    // The league mean moved 27.4% -> 29.0% -> 29.7% across 2023-25. A fixed cut
    // would reband a defense that changed nothing it does.
    assert.equal(blitzStyle(3, 9).band, "heavy");
    assert.equal(blitzStyle(3, 32).band, "heavy");
    assert.equal(blitzStyle(9, 9).band, "light");
    assert.equal(blitzStyle(9, 32).band, "heavy");
  });

  test("an empty field bands nothing rather than dividing by zero", () => {
    assert.equal(blitzStyle(1, 0).band, "average");
  });

  test("a defense with no rank gets no style at all", () => {
    // Under the dropback floor it is UNMEASURED, which is not the same as
    // unaggressive — so it is excluded rather than banded as light.
    assert.equal(blitzStyleFor({ opponentBlitzRank: null }, 32), null);
    assert.equal(blitzStyleFor({ opponentBlitzRank: 4 }, 32)?.band, "heavy");
  });
});

describe("formatting and sample", () => {
  test("whole percents only", () => {
    // Entering week 2 a rate rests on about 35 dropbacks, which supports
    // neither a decimal place nor the impression of one.
    assert.equal(formatRate(0.2966), "30%");
    assert.equal(formatRate(0), "0%");
  });

  test("absence is a dash, never 0%", () => {
    assert.equal(formatRate(null), "—");
  });

  test("the sample always states games and dropbacks", () => {
    assert.equal(sampleNote(charting({ games: 1, dropbacks: 35 })), "1 game · 35 dropbacks");
    assert.equal(sampleNote(charting({ games: 7, dropbacks: 250 })), "7 games · 250 dropbacks");
  });

  test("nothing charted says so rather than showing a zero", () => {
    assert.equal(sampleNote(charting({ games: 0, dropbacks: 0 })), "no games charted yet");
  });
});

describe("which sports can carry this at all", () => {
  test("NFL only — there is no college charting source at any price", () => {
    assert.equal(sportHasCharting("nfl"), true);
    assert.equal(sportHasCharting("cfb"), false);
  });
});
