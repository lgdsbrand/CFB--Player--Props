/**
 * How much the model actually knows about a player, and how to say it.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). The NFL build copies this unchanged —
 * every quantity here is produced by the modelling library, not by the college
 * adapter, and the thresholds are properties of the blend rather than of a
 * sport's calendar.
 *
 * TWO NUMBERS, AND ONLY ONE OF THEM IS SAFE WITHOUT A SENTENCE ATTACHED.
 *
 * `priorWeight` is the share of a projection carried by THIS PLAYER'S OWN prior
 * season. `effectiveSample` is current-season games plus prior-season games
 * discounted by that weight — "games of evidence".
 *
 * The share inverts on an opening slate, and the reason is worth stating
 * exactly, because the obvious reading of it is wrong twice over. What the
 * weight does NOT split is prior season against this season; it splits the
 * player's own prior against a generic POSITION BASELINE. A transfer takes the
 * changed-team discount, so on 2025 week 1 he carries 0.25 against a returning
 * starter's 0.50 — meaning three quarters of his projection is replacement-level
 * baseline rather than anything anyone observed him do. Printed on a card that
 * reads as "less dependent on last season", which is true of the arithmetic and
 * the opposite of the situation.
 *
 * `effectiveSample` cannot invert. That transfer shows 3.0 games against the
 * starter's 6.0, and less is always less. So the sample is what the card
 * shows, and the share appears only where there is room for the sentence that
 * keeps it honest — the player page.
 *
 * NOTHING HERE IS A CALENDAR TEST. No function asks what week it is. A player
 * running on thin evidence is flagged because his own row says so, not because
 * of what week it is — describe what exists, not what a rule predicts should
 * exist.
 *
 * THIS IS NOW A PER-PLAYER SIGNAL ONLY. There used to be a slate-level
 * companion — `slateEvidence` and an `EvidenceNote` banner that said what share
 * of a whole week was thin — and the client asked for it removed, so it is
 * gone along with the two count queries that fed it. The per-card marker below
 * is deliberately NOT part of that removal: it is what still separates a
 * 9.2-game starter from a 2.0-game backup, on every week and not just the
 * opening ones.
 */

/**
 * Below this many effective games, a projection is thin enough to flag.
 *
 * NOT A WEEK-1 MARKER. Measured across 2025: 59% of week-1 rows fall below it,
 * 25% of week 8 and 19% of week 13 — so it separates the thinly-evidenced
 * players from the well-evidenced ones INSIDE every week, which is the useful
 * comparison. A marker that fired on all of week 1 and none of week 8 would be
 * a redundant restatement of the week selector.
 *
 * Four games is also where the blend stops being dominated by the prior, which
 * is why it is the number rather than three or five: the worker's decay is
 * `PRIOR_GAMES_EQUIVALENT / (PRIOR_GAMES_EQUIVALENT + games)` with an
 * equivalent of 4.0, so at four current-season games the prior weight is
 * exactly halved from its ceiling.
 */
export const THIN_EVIDENCE_GAMES = 4;

export type Evidence = {
  /** Effective games behind the projection. */
  games: number;
  /** Share carried by this player's own prior season, 0..1. */
  priorShare: number;
  /** Fewer than `THIN_EVIDENCE_GAMES` — worth flagging on the card. */
  isThin: boolean;
};

/**
 * Read the evidence off a projection row, or null if it carries none.
 *
 * NULL IS NOT ZERO. A row with no `effective_sample` is one this code cannot
 * describe; rendering it as "0.0 games" would assert the model knows nothing
 * about a player, which is a much stronger claim than the absence of a number.
 * Every row written since Phase 4a carries both, so null means an older row or
 * a schema that has drifted — either way, say nothing.
 */
export function evidenceFor(row: {
  priorWeight: number | null;
  effectiveSample: number | null;
}): Evidence | null {
  const { priorWeight, effectiveSample } = row;
  if (
    effectiveSample === null ||
    priorWeight === null ||
    !Number.isFinite(effectiveSample) ||
    !Number.isFinite(priorWeight)
  ) {
    return null;
  }

  return {
    games: effectiveSample,
    priorShare: priorWeight,
    isThin: effectiveSample < THIN_EVIDENCE_GAMES,
  };
}

/**
 * "3.0 games" — the card's compact form. `.pill` upper-cases it.
 *
 * IT USED TO READ "3.0 GM" and the client asked what it meant, which is the
 * whole argument: this is the card's uncertainty signal, it is the one badge
 * whose meaning is not guessable from context, and four saved characters bought
 * nothing. "GAMES" is still short enough to sit beside the RK and GRADE pills.
 *
 * The unit is spelled out and the qualifier is not — these are EFFECTIVE games,
 * not games played, and that distinction needs the sentence in `evidenceTitle`
 * rather than a longer label. A badge reading "3.0 EFF GAMES" would swap one
 * unexplained abbreviation for another.
 */
export function formatEvidence(evidence: Evidence): string {
  return `${evidence.games.toFixed(1)} games`;
}

/**
 * The full sentence, for the pill's title and the player page.
 *
 * States the fraction AND what it is a fraction of, because "50% from last
 * season" invites the reading that the other 50% is from this one — which is
 * false in week 1, where there is no other 50%.
 */
export function evidenceTitle(evidence: Evidence): string {
  const share = Math.round(evidence.priorShare * 100);
  return (
    `${evidence.games.toFixed(1)} games of evidence behind this player: ` +
    `this season's games, plus last season's discounted to the ${share}% ` +
    `weight the model gives them. ` +
    (evidence.isThin
      ? "Thin — the projection is doing more extrapolating than measuring."
      : "Enough to measure from rather than extrapolate.")
  );
}
