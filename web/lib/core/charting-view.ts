/**
 * What kind of defense this is — blitz rate and box counts (migration 0072).
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3), though only the NFL has data: there is no
 * college charting source at any price, so every college row reads null and the
 * surfaces show nothing rather than a row of dashes.
 *
 * THE AXIS THIS ADDS. The board already carries what a defense CONCEDES, in
 * `opponent_rank_vs_position`, opponent-adjusted and coloured good-to-bad. This
 * is what a defense DOES, which is a different question and a differently
 * behaved number: a scheme choice is far more stable than a rate of production.
 * Measured half-to-half over 2023-25, blitz rate repeats at Spearman +0.62
 * against about +0.18 for the adjusted rank — which is why this one earns an
 * ordering where the first-quarter rate (+0.05) was refused one.
 *
 * COLOURED BY STYLE, NEVER GOOD-TO-BAD, and this is the rule the whole module
 * exists to hold. The client asked for "color coded to good and worse"; a heavy
 * blitz is not good or bad for a receiver. It means more single coverage AND
 * faster throws, and this project has measured neither direction. So the ramp
 * runs warm (heavy) to cool (light) rather than green to red, and never reuses
 * the positive/negative tokens that mean hit and miss everywhere else in this
 * app. A reader who sees green here would take it as a recommendation.
 */

// Relative, with the extension: `lib/core` is imported both by the Next bundler
// and by Node's test runner, and only the bundler understands `@/*`.
import type { DefenseCharting } from "./types.ts";

/** Where a defense sits on the aggression ramp. */
export type BlitzBand = "heavy" | "average" | "light";

export type BlitzStyle = {
  band: BlitzBand;
  /** Shown as the headline, because the rank alone means nothing without it. */
  label: string;
  /**
   * Tailwind classes for the pill. Warm-to-cool, NOT green-to-red — see the
   * module docstring. `positive` and `negative` are deliberately absent.
   */
  tone: string;
};

const STYLES: Record<BlitzBand, BlitzStyle> = {
  heavy: {
    band: "heavy",
    label: "BLITZ-HEAVY",
    tone: "bg-target/15 text-target",
  },
  average: {
    band: "average",
    label: "AVERAGE PRESSURE",
    tone: "bg-panel-inset text-muted",
  },
  light: {
    band: "light",
    label: "BLITZ-LIGHT",
    tone: "bg-accent-indigo/15 text-accent-indigo",
  },
};

/**
 * Thirds of the RATED field, not fixed rate thresholds.
 *
 * A fixed cut ("30% is heavy") would drift with the league: the mean blitz rate
 * was 27.4% in 2023, 29.0% in 2024 and 29.7% in 2025, so a defense could change
 * band without changing anything it does. Thirds of the field say what the
 * label is actually claiming — this defense is in the top third for aggression
 * — and that is the same convention the player page's rank bands already use.
 *
 * `fieldSize` is the number of defenses carrying a rank at this cutoff, which
 * is not always 32: a defense under the dropback floor is unmeasured and is
 * excluded from the ordering rather than sorted to the bottom.
 */
export function blitzStyle(rank: number, fieldSize: number): BlitzStyle {
  if (fieldSize <= 0) return STYLES.average;
  const third = fieldSize / 3;
  if (rank <= third) return STYLES.heavy;
  if (rank > fieldSize - third) return STYLES.light;
  return STYLES.average;
}

/**
 * The style for a board row, or null when there is nothing to say.
 *
 * Null covers three different silences and deliberately does not distinguish
 * them here — college (no source), a defense under the dropback floor, and week
 * 1 (nothing has been played). A caller that needs to explain the silence has
 * `charting.games` and the sport to do it with.
 */
export function blitzStyleFor(
  row: { opponentBlitzRank: number | null },
  fieldSize: number,
): BlitzStyle | null {
  if (row.opponentBlitzRank === null) return null;
  return blitzStyle(row.opponentBlitzRank, fieldSize);
}

/**
 * A rate as a whole percent.
 *
 * NO DECIMAL PLACE. Entering week 2 a defense has about 35 dropbacks behind its
 * rate, which supports neither a tenth of a percent nor the impression of one.
 */
export function formatRate(rate: number | null): string {
  if (rate === null) return "—";
  return `${Math.round(rate * 100)}%`;
}

/**
 * How firm this figure is, in the reader's terms.
 *
 * EVERY SURFACE MUST SHOW THIS. FTN publishes two to three days after a week is
 * played, so a week-2 board is reading one game — around 35 dropbacks, where a
 * 30% rate carries a standard error of about 8 points. By week 8 it is seven
 * games and the number is worth leaning on. A rate shown without its sample
 * invites the same weight in both cases.
 */
export function sampleNote(charting: DefenseCharting): string {
  const games = charting.games;
  if (games <= 0) return "no games charted yet";
  const plural = games === 1 ? "game" : "games";
  return `${games} ${plural} · ${charting.dropbacks} dropbacks`;
}

/**
 * True when a sport can carry charting at all.
 *
 * Asked of the SPORT rather than of the row, so a surface can drop the column
 * entirely for college instead of printing a permanent line of dashes. The
 * first-quarter columns take the same approach for the same reason.
 */
export function sportHasCharting(sport: string): boolean {
  return sport === "nfl";
}
