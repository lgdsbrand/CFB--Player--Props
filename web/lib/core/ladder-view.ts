import type { LadderRung } from "@/lib/core/types";

/**
 * Presenting the alternate-line ladder.
 *
 * The client's ask, verbatim: "RB line 60, model says 90." The board answers one
 * probability against one line; this answers how far the line can be pushed
 * before the model stops agreeing.
 *
 * NO ARITHMETIC ON PROBABILITIES HAPPENS HERE. The rungs arrive computed, from
 * scipy in the worker, because three of the seven distribution families in live
 * use need the regularised incomplete gamma and incomplete beta and a second
 * implementation in TypeScript would be a third copy of the model maths (see
 * migration 0039). This module only orders, locates and labels them.
 */

/** A rung with the book's line located relative to it, for rendering. */
export type LadderView = {
  rungs: LadderRung[];
  /**
   * Index of the first rung at or above the book line, or null when there is no
   * line, or when the line sits outside the rung range entirely.
   *
   * An INSERTION POINT, not a match. Rung spacing widens to cover a broad
   * distribution — passing yards routinely step to 75 — so a book line almost
   * never coincides with a rung, and a UI that looked for equality would mark
   * nothing on most rows. See `bookLineOnRung`.
   */
  bookIndex: number | null;
  /** True only when the book's line IS one of the rungs. Rare; do not rely on it. */
  bookLineOnRung: boolean;
  /** The rung closest to the model's median, for a "this is our number" marker. */
  medianIndex: number | null;
};

/**
 * Parse whatever the database handed back, defensively.
 *
 * `projections.ladder` is jsonb with only an outer array CHECK on it, so the
 * shape of each element is a worker-side guarantee rather than a database one.
 * A malformed rung is dropped rather than rendered as NaN, and a wholly
 * unparseable value yields an empty ladder, which the panel already handles by
 * not rendering.
 */
export function parseLadder(raw: unknown): LadderRung[] {
  if (!Array.isArray(raw)) return [];
  const rungs: LadderRung[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) continue;
    const { line, prob_over: probOver } = entry as Record<string, unknown>;
    if (typeof line !== "number" || typeof probOver !== "number") continue;
    if (!Number.isFinite(line) || !Number.isFinite(probOver)) continue;
    rungs.push({ line, probOver });
  }
  // Ascending by line. The worker writes them in order and audit_data asserts it,
  // but the panel's meaning depends on the order and re-sorting costs nothing.
  return rungs.sort((a, b) => a.line - b.line);
}

/**
 * Takes rungs ALREADY PARSED by the read layer, not raw jsonb.
 *
 * This signature was `unknown` and calling `parseLadder` itself, which was a bug
 * with no symptom a type checker could see: the read layer parses at the database
 * boundary, producing `probOver`, and a second parse looked for `prob_over`,
 * found nothing, and returned an empty ladder. The panel then rendered nothing at
 * all, on every row, and every test still passed because the tests fed it raw
 * shapes. Parsing happens exactly once, where untrusted data enters.
 */
export function ladderView(
  rungs: LadderRung[] | null,
  bookLine: number | null,
  median: number | null,
): LadderView | null {
  if (!rungs || rungs.length === 0) return null;

  // Ascending by line. The worker writes them in order, `parseLadder` sorts, and
  // audit_data asserts it — but the panel's meaning depends on the order, so this
  // does not take it on trust from three places that could each change.
  const sorted = [...rungs].sort((a, b) => a.line - b.line);

  let bookIndex: number | null = null;
  if (bookLine !== null) {
    const at = sorted.findIndex((rung) => rung.line >= bookLine);
    // Deliberately null when the line is above every rung. The marker means
    // "the book sits here among these", and a line beyond the top rung does not
    // sit among them — pinning it to the last rung would misplace it by an
    // unknown distance and read as agreement the ladder does not show.
    bookIndex = at === -1 ? null : at;
  }

  const medianIndex =
    median === null
      ? null
      : sorted.reduce(
          (best, rung, index) =>
            Math.abs(rung.line - median) < Math.abs(sorted[best].line - median)
              ? index
              : best,
          0,
        );

  return {
    rungs: sorted,
    bookIndex,
    bookLineOnRung:
      bookLine !== null && sorted.some((rung) => rung.line === bookLine),
    medianIndex,
  };
}

/**
 * Which side the model favours at a given rung, on the same rule the board uses.
 *
 * Mirrors `probability.side_and_confidence`: the call is the side holding the
 * majority of the mass. Stated here rather than imported because the read layer
 * has no access to the Python, and pinned to the same 0.5 boundary so a rung and
 * a card can never disagree about which way the model leans.
 */
export function rungSide(probOver: number): "over" | "under" {
  return probOver >= 0.5 ? "over" : "under";
}

/** The mass on the favoured side — what the rung's percentage should read. */
export function rungConfidence(probOver: number): number {
  return probOver >= 0.5 ? probOver : 1 - probOver;
}

/**
 * THE CAVEAT. Shown as a tooltip on the panel heading.
 *
 * A ladder asserts the book is mispriced at several lines rather than one, which
 * is a STRONGER claim than the board makes, not a weaker one — and it is the
 * claim two graded weeks did not support. The model is well calibrated and has
 * measurable skill on both sides, but against real closing lines it did not beat
 * blindly betting under, and the gap widened on exactly the high-edge rows the
 * board surfaces. Every rung is the model's read; none is a recommendation.
 *
 * Kept as an exported constant so the wording is in one place and a test can
 * assert it is actually attached to something.
 */
export const LADDER_CAVEAT =
  "The model's read at each line, not a recommendation. These are model " +
  "probabilities with no book price attached — books post one line, so a rung " +
  "has no market to disagree with. Against real closing lines the model has not " +
  "beaten simply betting the under, so treat a confident rung as the model's " +
  "view of the player, not as a priced edge.";
