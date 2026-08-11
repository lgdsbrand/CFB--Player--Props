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
 *
 * THE SLATE-LEVEL TESTS WERE REMOVED WITH THE BANNER THEY COVERED. `slateEvidence`
 * and the `EvidenceNote` it fed are gone at the client's request, so the eight
 * tests pinning the opening-weekend thresholds went with them rather than being
 * left to guard code nothing calls. What survives is the per-player marker,
 * which is a different claim and still on every card.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  evidenceFor,
  evidenceTitle,
  formatEvidence,
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
  assert.equal(formatEvidence(evidence), "3.0 games");
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
