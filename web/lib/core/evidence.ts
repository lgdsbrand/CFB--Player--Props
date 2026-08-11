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
 * starter's 6.0, and less is always less. So the sample is what the board
 * shows, on cards and in its slate note alike, and the share appears only
 * where there is room for the sentence that keeps it honest — the player page.
 *
 * NOTHING HERE IS A CALENDAR TEST. No function asks what week it is. A slate
 * that is running on thin evidence says so because its rows say so, which is
 * the discipline migration 0027 records for `v_slate_weeks` — describe what
 * exists, not what a rule predicts should exist.
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

/**
 * Share of a slate that must be thin before the board says so.
 *
 * A note that fires for three stragglers trains people to ignore it. Measured
 * across all sixteen weeks of 2025 in the displayed conferences, this selects
 * weeks 1 and 2 (60%, 60%) and nothing later — week 5 is 37%, week 8 24%, week
 * 14 18%.
 *
 * WEEK 3 SITS AT 49.86% AND IS THEREFORE ONE ROW FROM FLIPPING, which is fine
 * and is worth understanding rather than tuning away. If it flips on, the note
 * reports "1,268 of 2,543 rest on fewer than four effective games" and "2,008
 * of 2,543 face a rated defense" — both true, both useful, neither alarming.
 * The claims that would be wrong outside an opening weekend are gated on
 * `openingWeekend`, which needs the ratings to be absent as well, and week 3's
 * are not. The threshold decides tone, not truth.
 *
 * AN EARLIER VERSION OF THIS COUNTED `prior_weight >= 0.4` INSTEAD, and it was
 * wrong in the way the module comment describes: on 2025 week 1 that reported
 * "1,979 of 3,068 projections rest mostly on last season" when all 3,068 do,
 * the missing 1,089 being transfers whose priors were discounted hardest. A
 * count that quietly excludes the least-evidenced third of the board is worse
 * than no count.
 */
export const THIN_SLATE_SHARE = 0.5;

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

/** What one week's rows say about the evidence behind the whole board. */
export type SlateEvidence = {
  rows: number;
  /** Rows below `THIN_EVIDENCE_GAMES` of evidence. */
  thin: number;
  /** 0..1. Zero when the slate is empty, never NaN. */
  thinShare: number;
  /** `thinShare` past `THIN_SLATE_SHARE` — decided here, not in a component. */
  mostlyThin: boolean;
  ranked: number;
  /**
   * Whether an opponent adjustment was available for these rows.
   *
   * "none" is the opening-weekend state and is a fact about the DATA, not a
   * degraded rendering: defense ratings are built from games played this
   * season, so entering week 1 no defense in the league has one. Measured on
   * 2025: 0 of 3,068 week-1 rows carry a rank, 1,122 of 3,041 in week 2, and
   * 3,848 of 3,848 by week 8.
   */
  matchup: "none" | "partial" | "full";
  /**
   * The season has not yet produced evidence about anybody: most rows are thin
   * AND no defense is rated.
   *
   * BOTH CONDITIONS, because either alone describes something else. Ratings can
   * be missing in November if the job has not run — a real fault, and one this
   * note should still report, but not one where "players with no prior season
   * cannot be projected" is the useful thing to say. Thinness alone runs
   * through week 3, where the matchup panels do work. Only the pair means
   * opening weekend, and only that pair earns the coverage caveat.
   */
  openingWeekend: boolean;
  /** Whether the board should say any of this out loud. */
  show: boolean;
};

export function slateEvidence(counts: {
  rows: number;
  thin: number;
  ranked: number;
}): SlateEvidence {
  const rows = Math.max(counts.rows, 0);
  const thin = Math.min(Math.max(counts.thin, 0), rows);
  const ranked = Math.min(Math.max(counts.ranked, 0), rows);
  const thinShare = rows === 0 ? 0 : thin / rows;

  const matchup = ranked === 0 ? "none" : ranked < rows ? "partial" : "full";
  const mostlyThin = thinShare >= THIN_SLATE_SHARE;

  return {
    rows,
    thin,
    thinShare,
    mostlyThin,
    ranked,
    matchup,
    openingWeekend: rows > 0 && mostlyThin && matchup === "none",
    // Two independent reasons to speak up, and the second is not redundant: a
    // slate with no ratings at all is worth reporting even in November, where
    // it would mean the ratings job has not run rather than that the season has
    // not started. An empty slate says nothing — there is nothing to qualify.
    show: rows > 0 && (mostlyThin || matchup === "none"),
  };
}
