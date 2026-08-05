import { formatCount } from "@/lib/core/format";
import { THIN_EVIDENCE_GAMES, type SlateEvidence } from "@/lib/core/evidence";

/**
 * What this week's board is built on, when that is worth saying.
 *
 * MEASURED FROM THE SLATE ON SCREEN. The thin-row count and the rating coverage
 * are counted in `getBoardCounts` against this week, so the note cannot
 * describe a week other than the one being shown, and it cannot go stale — an
 * ingest that fills the ratings in changes the sentence with no deploy.
 *
 * THE PRIOR SHARE IS DELIBERATELY ABSENT. It is on every row and it is the
 * wrong number to count with: on 2025 week 1, counting rows where last season
 * carries ≥40% reports 1,979 of 3,068 when the true answer is all of them —
 * the 1,089 it omits are transfers, whose priors were discounted hardest and
 * whose projections are the thinnest on the board. See `lib/core/evidence.ts`.
 *
 * IT DOES NOT APOLOGISE FOR THE BOARD. Weeks 1-2 graded as the best-calibrated
 * stratum of the season, so the caveat here is COVERAGE, not accuracy, and the
 * copy has to keep those apart — a hedge on the calls would misrepresent the
 * backtest in the more damaging direction.
 */
export function EvidenceNote({ slate }: { slate: SlateEvidence }) {
  if (!slate.show) return null;

  return (
    <p className="border-accent-cyan/25 bg-accent-cyan/5 text-muted rounded-xl border px-3 py-2 text-xs">
      <span className="text-accent-cyan font-bold uppercase tracking-label">
        {slate.openingWeekend
          ? "Opening weekend"
          : slate.mostlyThin
            ? "Thin evidence"
            : "No matchup adjustment"}
      </span>{" "}
      —{" "}
      {slate.mostlyThin ? (
        <>
          {formatCount(slate.thin)} of {formatCount(slate.rows)} projections
          here rest on fewer than {THIN_EVIDENCE_GAMES} effective games — this
          season&rsquo;s, plus last season&rsquo;s discounted to the weight the
          model still gives them.{" "}
        </>
      ) : null}
      {slate.matchup === "none" ? (
        <>
          No defense carries a rating entering this week — ratings are built
          from games played this season — so nothing on this board is
          matchup-adjusted and the opponent-rank filter has nothing to select
          on.{" "}
        </>
      ) : slate.matchup === "partial" ? (
        <>
          {formatCount(slate.ranked)} of {formatCount(slate.rows)} rows face a
          defense with a rating; the rest are unadjusted, their opponents having
          played too little of this season to rate.{" "}
        </>
      ) : null}
      {slate.openingWeekend ? (
        <>
          Graded against past seasons, the opening weekends came out the
          best-calibrated stretch of the year, so the caveat is coverage rather
          than accuracy: players with no prior season — true freshmen, JUCO and
          FCS arrivals, about a quarter of opening-weekend production — cannot
          be projected at all and are simply absent from this board.
        </>
      ) : null}
    </p>
  );
}
