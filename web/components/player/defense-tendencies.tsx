import {
  blitzStyle,
  formatRate,
  sampleNote,
} from "@/lib/core/charting-view";
import type { DefenseCharting } from "@/lib/core/types";

/**
 * What this week's opponent DOES, beside the panel for what it ALLOWS
 * (migration 0072).
 *
 * THE TWO PANELS ANSWER DIFFERENT QUESTIONS and are deliberately not merged.
 * Defense Detail is production conceded, opponent-adjusted, ranked good-to-bad.
 * This is scheme: how often the coordinator sends pressure and how many he puts
 * in the box. The first is an outcome he partly controls; the second is a choice
 * he makes, which is why it repeats itself far more strongly from one half of a
 * season to the next (+0.62 against about +0.18) and why it is the one raw
 * number this project ranks.
 *
 * THE LABEL IS THE HEADLINE, NOT THE RANK. "3 of 32" means nothing until a
 * reader knows which end is which — and this rank runs the opposite way to the
 * one directly above it, because "first in blitz rate" conventionally means
 * blitzes most. Printing the words first makes the number unambiguous instead
 * of requiring a convention to be remembered.
 */
export function DefenseTendencies({
  charting,
  fieldSize,
  opponentLabel,
}: {
  charting: DefenseCharting | null;
  fieldSize: number;
  opponentLabel: string;
}) {
  if (!charting) {
    return (
      <p className="text-dim text-xs">
        No charting for this defense yet. Scheme data covers the NFL only, and
        the first figures land in week 2 — the charting for a week&apos;s games
        is published two to three days after they are played.
      </p>
    );
  }

  const rated = charting.blitzRate !== null && charting.blitzRank !== null;
  const style = rated ? blitzStyle(charting.blitzRank!, fieldSize) : null;

  return (
    <div className="flex flex-col gap-3">
      {rated && style ? (
        <div className="panel-inset flex flex-col gap-2 px-3 py-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className={"pill " + style.tone}>{style.label}</span>
            <span className="text-dim text-[0.625rem]">
              {charting.blitzRank} of {fieldSize} for blitz rate
            </span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <Figure
              label="Blitz rate"
              value={formatRate(charting.blitzRate)}
              hint="Share of dropbacks with an extra rusher"
            />
            <Figure
              label="Rushers"
              value={
                charting.meanPassRushers === null
                  ? "—"
                  : charting.meanPassRushers.toFixed(2)
              }
              hint="Average number rushing the passer"
            />
            <Figure
              label="Heavy box"
              value={formatRate(charting.heavyBoxRate)}
              hint="Share of charted plays with 7+ in the box. No rank — see the note below."
            />
          </div>

          <p className="text-dim text-[0.625rem]">{sampleNote(charting)}</p>
        </div>
      ) : (
        <p className="text-dim text-xs">
          {opponentLabel} has {sampleNote(charting)} charted entering this week —
          not enough dropbacks to state a blitz rate yet.
        </p>
      )}

      {/*
        SAID PLAINLY, because the colour is the thing most likely to be
        misread. Every other coloured figure in this app means good or bad.
      */}
      <p className="text-dim text-[0.625rem]">
        The colour is a <strong className="text-muted">style</strong>, not a
        verdict: warm for blitz-heavy, cool for blitz-light. A blitz means more
        single coverage and faster throws at once, and this model has not
        measured which wins. For whether the matchup is soft, read the
        opponent-adjusted rank above.
        {charting.heavyBoxRate !== null ? (
          <>
            {" "}
            Heavy box carries no rank on purpose — it repeats itself far more
            weakly than blitz rate does, and ranking it here would make the two
            look equally firm.
          </>
        ) : null}
      </p>
    </div>
  );
}

function Figure({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="flex flex-col gap-0.5" title={hint}>
      <span className="label-caption">{label}</span>
      <span className="text-base font-extrabold leading-none tabular-nums">
        {value}
      </span>
    </div>
  );
}
