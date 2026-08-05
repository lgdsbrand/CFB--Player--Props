/**
 * Tests for the evidence markers the board shows in the opening weeks.
 *
 * THE FIRST TEST IS THE ONE THAT CHANGED THE DESIGN. It was written asserting
 * that both an opening-weekend transfer and an opening-weekend returning
 * starter would be flagged "priors-led", since neither has played a game and
 * both are projected entirely from last season. It failed: the transfer's prior
 * is discounted to 0.25, below any threshold the starter's 0.50 clears. That is
 * not a threshold that needed adjusting — it is the whole quantity being unfit
 * for the purpose. `prior_weight` splits a player's own prior against a generic
 * POSITION BASELINE, not against this season, so on an opening slate the
 * hardest-discounted players score lowest on it while being the least evidenced
 * on the board. The slate note was counting rows by it at the time and would
 * have reported 1,979 of 3,068 projections resting on last season when the true
 * figure was all 3,068.
 *
 * Everything below counts on `effective_sample` instead, which cannot invert.
 * The fixtures are 2025 rows, so a change in the projector that moves these
 * numbers should be looked at rather than pasted over.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  evidenceFor,
  evidenceTitle,
  formatEvidence,
  slateEvidence,
  THIN_EVIDENCE_GAMES,
} from "./evidence.ts";

// -----------------------------------------------------------------------------
// Per-row evidence
// -----------------------------------------------------------------------------

test("the prior share inverts on an opening slate and the sample does not", () => {
  // 2025 week 1. Neither has played; both are projected entirely from last
  // season. The transfer's prior is discounted by CHANGED_TEAM_PRIOR_MULTIPLIER,
  // so three quarters of his projection is replacement-level baseline.
  const returning = evidenceFor({ priorWeight: 0.5, effectiveSample: 6.0 });
  const transfer = evidenceFor({ priorWeight: 0.25, effectiveSample: 3.0 });
  assert.ok(returning && transfer);

  // The share says the transfer depends on last season LESS, which is true of
  // the arithmetic and the opposite of the situation. Nothing on the board may
  // rank players by this.
  assert.ok(transfer.priorShare < returning.priorShare);

  // The sample says what a reader needs: less is behind the transfer's number.
  assert.ok(transfer.games < returning.games);
  assert.equal(transfer.isThin, true);
  assert.equal(returning.isThin, false);
});

test("a settled-season row is not thin", () => {
  // 2025 week 8 mean: prior weight 0.221, effective sample 5.39.
  const evidence = evidenceFor({ priorWeight: 0.221, effectiveSample: 5.39 });
  assert.ok(evidence);
  assert.equal(evidence.isThin, false);
});

test("the thin threshold is exclusive, so exactly four games is not thin", () => {
  const at = evidenceFor({
    priorWeight: 0.3,
    effectiveSample: THIN_EVIDENCE_GAMES,
  });
  const below = evidenceFor({
    priorWeight: 0.3,
    effectiveSample: THIN_EVIDENCE_GAMES - 0.1,
  });
  assert.equal(at?.isThin, false);
  assert.equal(below?.isThin, true);
});

test("a missing sample says nothing rather than zero", () => {
  // Rendering null as 0.0 would assert the model knows NOTHING about a player,
  // which is a far stronger claim than "this row does not carry the number".
  assert.equal(evidenceFor({ priorWeight: 0.5, effectiveSample: null }), null);
  assert.equal(evidenceFor({ priorWeight: null, effectiveSample: 6 }), null);
  assert.equal(
    evidenceFor({ priorWeight: 0.5, effectiveSample: Number.NaN }),
    null,
  );
});

test("the compact form keeps one decimal so 3.0 and 3.8 are distinguishable", () => {
  const evidence = evidenceFor({ priorWeight: 0.5, effectiveSample: 3.0 });
  assert.ok(evidence);
  assert.equal(formatEvidence(evidence), "3.0 gm");
});

test("the title states what the prior share is a share OF", () => {
  // "50% from last season" invites the reading that the other half comes from
  // this one. In week 1 there is no other half.
  const evidence = evidenceFor({ priorWeight: 0.5, effectiveSample: 6 });
  assert.ok(evidence);
  const title = evidenceTitle(evidence);
  assert.match(title, /6\.0 games of evidence/);
  assert.match(title, /50% weight/);
});

// -----------------------------------------------------------------------------
// Slate-level evidence
// -----------------------------------------------------------------------------

test("an opening slate is thin, unrated, and says so in full", () => {
  // 2025 week 1, displayed conferences: 3,068 rows, 1,829 thin, 0 ranked.
  const slate = slateEvidence({ rows: 3068, thin: 1829, ranked: 0 });
  assert.equal(slate.matchup, "none");
  assert.equal(slate.mostlyThin, true);
  assert.equal(slate.openingWeekend, true);
  assert.equal(slate.show, true);
});

test("the second weekend is partly rated, so it is not an opening weekend", () => {
  // 2025 week 2: ratings exist for defenses that have played once. The board
  // still speaks up — the rows are as thin as week 1's — but the claim that
  // nothing is matchup-adjusted no longer holds and must not be made.
  const slate = slateEvidence({ rows: 3041, thin: 1831, ranked: 1122 });
  assert.equal(slate.matchup, "partial");
  assert.equal(slate.mostlyThin, true);
  assert.equal(slate.openingWeekend, false);
  assert.equal(slate.show, true);
});

test("a settled slate says nothing", () => {
  // 2025 week 8: every row rated, 24% thin.
  const slate = slateEvidence({ rows: 3848, thin: 918, ranked: 3848 });
  assert.equal(slate.matchup, "full");
  assert.equal(slate.mostlyThin, false);
  assert.equal(slate.show, false);
});

test("a mid-season slate whose opponents are partly unrated stays quiet", () => {
  // 2025 week 12: 56 rows face a defense with no rating, which is ordinary and
  // is not worth a banner. Partial coverage alone must not trigger one.
  const slate = slateEvidence({ rows: 3878, thin: 717, ranked: 3822 });
  assert.equal(slate.matchup, "partial");
  assert.equal(slate.show, false);
});

test("a slate with no ratings at all speaks up even when its rows are thick", () => {
  // Not an opening weekend — this is what a ratings job that failed to run
  // looks like from the board, and it should not pass silently because the
  // calendar says November. It does NOT earn the opening-weekend copy.
  const slate = slateEvidence({ rows: 4000, thin: 400, ranked: 0 });
  assert.equal(slate.matchup, "none");
  assert.equal(slate.mostlyThin, false);
  assert.equal(slate.openingWeekend, false);
  assert.equal(slate.show, true);
});

test("week 3 sits a whisker below the threshold and either answer is safe", () => {
  // 1,268 of 2,543 is 49.86%. The point is not that it stays off — it is that
  // flipping on changes only the tone: `openingWeekend` needs the ratings gone
  // too, and week 3's are present.
  const slate = slateEvidence({ rows: 2543, thin: 1268, ranked: 2008 });
  assert.equal(slate.mostlyThin, false);
  assert.equal(slate.openingWeekend, false);

  const nudged = slateEvidence({ rows: 2543, thin: 1272, ranked: 2008 });
  assert.equal(nudged.mostlyThin, true);
  assert.equal(nudged.openingWeekend, false);
});

test("counts that exceed the row total are clamped rather than believed", () => {
  const slate = slateEvidence({ rows: 10, thin: 99, ranked: 99 });
  assert.equal(slate.thin, 10);
  assert.equal(slate.ranked, 10);
  assert.equal(slate.thinShare, 1);
  assert.equal(slate.matchup, "full");
});

test("an empty slate has nothing to qualify", () => {
  const slate = slateEvidence({ rows: 0, thin: 0, ranked: 0 });
  assert.equal(slate.thinShare, 0);
  assert.equal(slate.show, false);
  assert.equal(slate.openingWeekend, false);
});
