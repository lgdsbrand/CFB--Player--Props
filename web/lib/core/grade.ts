/**
 * The GRADE badge on a card header.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3).
 *
 * WHAT THIS IS, EXACTLY. CLAUDE.md §7 lists a GRADE badge among the components
 * to reuse from the client's pitcher card. This one is a restatement of the
 * confidence percentage in coarser form — nothing more. It is NOT a second,
 * independent rating, and it deliberately introduces no new information: a
 * letter that blended in factors the confidence figure does not carry would be
 * a new claim about a pick, and this product's claim is the calibrated
 * probability.
 *
 * WHY NOT EDGE. Edge would be the more useful basis — it is what the board
 * sorts by — but every edge on the board today comes from a synthetic
 * development line and equals `confidence - 0.5` by construction. Grading on it
 * would dress up an artefact as a rating. Revisit once real book prices arrive:
 * a grade blending calibrated confidence with genuine market disagreement is
 * the right long-term shape, and this is the seam where it goes.
 *
 * THE THRESHOLDS. Confidence runs 0.5 (a coin flip) to 1.0. The bands below are
 * chosen so the letters spread across the range the model actually produces —
 * measured on 2025 week 10, roughly 60% of priced rows land between 0.50 and
 * 0.60 and under 2% clear 0.80. A scale that awarded an A at 0.60 would grade
 * most of the board an A and say nothing.
 */

export type Grade = "A+" | "A" | "B" | "C" | "D";

const BANDS: { min: number; grade: Grade }[] = [
  { min: 0.8, grade: "A+" },
  { min: 0.72, grade: "A" },
  { min: 0.65, grade: "B" },
  { min: 0.58, grade: "C" },
  { min: 0, grade: "D" },
];

/** Null confidence yields null: a lean with no line has nothing to grade. */
export function gradeFor(confidence: number | null): Grade | null {
  if (confidence === null || !Number.isFinite(confidence)) return null;
  return BANDS.find((band) => confidence >= band.min)?.grade ?? "D";
}

/**
 * Semantic colour token for a grade. Returns a token name, never a hex value —
 * the palette lives in globals.css so the reskin stays one file.
 */
export function gradeToneToken(
  grade: Grade | null,
): "positive" | "target" | "muted" {
  if (grade === "A+" || grade === "A") return "positive";
  if (grade === "B") return "target";
  return "muted";
}
