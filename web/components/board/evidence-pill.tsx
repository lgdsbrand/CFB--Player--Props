import {
  evidenceFor,
  evidenceTitle,
  formatEvidence,
} from "@/lib/core/evidence";

/**
 * How many games of evidence sit behind a player's card.
 *
 * WHY THE SAMPLE AND NOT THE PRIOR SHARE. Both numbers are on the row, and only
 * one of them survives being read alone. In the opening weeks a transfer
 * carries a LOWER prior weight than a returning starter — the changed-team
 * discount applies to the only evidence either has — so a card reading "25%
 * priors" beside another reading "50% priors" says the opposite of the truth
 * about which is better grounded. The effective sample says 3.0 against 6.0,
 * and less is always less. The share is stated once per slate instead, above
 * the board, where it is a statement about the week rather than a ranking of
 * players. See `lib/core/evidence.ts`.
 *
 * IT RENDERS IN EVERY WEEK, not only the opening ones. A marker that appeared
 * on an opening slate and vanished afterwards would be read as a warning label
 * on the week; this is a property of each player, and in week 8 it still
 * separates a 9.2-game starter from a 2.0-game backup.
 */
export function EvidencePill({
  priorWeight,
  effectiveSample,
}: {
  priorWeight: number | null;
  effectiveSample: number | null;
}) {
  const evidence = evidenceFor({ priorWeight, effectiveSample });
  if (!evidence) return null;

  return (
    <span
      className={
        "pill " +
        (evidence.isThin
          ? "bg-target/15 text-target"
          : "bg-panel-inset text-muted")
      }
      title={evidenceTitle(evidence)}
    >
      {formatEvidence(evidence)}
    </span>
  );
}
