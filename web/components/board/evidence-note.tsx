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
 *
 * COLLAPSED TO ONE LINE, AND THE LINE STILL CARRIES THE NUMBER. The expanded
 * form ran to five lines of prose above the first card and dominated the
 * opening-weekend board, which is what the client saw and objected to. The fix
 * that was NOT taken was deleting it: this is the only thing on screen that
 * says most of an opening board rests on almost nothing, and a reader who never
 * opens the detail must still come away with that fact. So the summary states
 * the count itself rather than teasing it — "1,769 of 2,848 thinly evidenced"
 * is the claim, and the detail explains what thin means and what it does not.
 *
 * `<details>` RATHER THAN CLIENT STATE, so this stays a server component with
 * no hydration cost, keyboard support and open-by-find-in-page for free.
 */
export function EvidenceNote({ slate }: { slate: SlateEvidence }) {
  if (!slate.show) return null;

  const label = slate.openingWeekend
    ? "Opening weekend"
    : slate.mostlyThin
      ? "Thin evidence"
      : "No matchup adjustment";

  // The one-line claim. Thinness wins where both apply: it is a statement about
  // the projections themselves, while the missing ratings qualify only the
  // matchup panels, and an opening slate has both.
  const headline = slate.mostlyThin
    ? `${formatCount(slate.thin)} of ${formatCount(slate.rows)} rows thinly evidenced`
    : "no defense carries a rating entering this week";

  return (
    <details className="border-accent-cyan/25 bg-accent-cyan/5 group rounded-xl border px-3 py-2">
      {/*
        WRAPS RATHER THAN TRUNCATES. With `truncate` this read "1,829 of 3,068
        rows thinly ..." at 390px — the count survived and the word that says
        what the count MEANS did not, which is the wrong half to lose from a
        caveat. Two lines on a phone is still a fifth of the block this
        replaced.
      */}
      <summary className="text-muted flex cursor-pointer list-none items-center gap-2 text-xs [&::-webkit-details-marker]:hidden">
        <span className="text-accent-cyan font-bold uppercase tracking-label">
          {label}
        </span>
        <span className="min-w-0 flex-1">{headline}</span>
        <span
          aria-hidden
          className="text-dim shrink-0 transition-transform group-open:rotate-180"
        >
          ▾
        </span>
      </summary>

      <p className="text-muted mt-2 text-xs">
        {slate.mostlyThin ? (
          <>
            {formatCount(slate.thin)} of {formatCount(slate.rows)} projections
            here rest on fewer than {THIN_EVIDENCE_GAMES}{" "}
            {/* Explicit: the plain source space after the expression above was
                being dropped in the build and the board read "4effective games"
                on every opening-weekend slate. Verified in the served HTML, not
                just on a screenshot. */}
            effective games — this season&rsquo;s, plus last season&rsquo;s
            discounted to the weight the model still gives them.{" "}
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
    </details>
  );
}
