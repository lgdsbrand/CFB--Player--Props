import Link from "next/link";

import { TeamChip } from "@/components/board/team-chip";
import {
  agreement,
  formatRecord,
  sideLabel,
  type CheatSheetRow,
} from "@/lib/core/cheat-sheet";
import { formatConfidence, formatDateShort, formatLine } from "@/lib/core/format";
import { playerHref } from "@/lib/core/player-params";

/**
 * One entry on the cheat sheet.
 *
 * DENSE ON PURPOSE. A full week puts 170-400 entries on this page, so a card
 * per entry would be a scroll with no shape. This is a row: identity on the
 * left, what the streak is in the middle, how strong it is on the right, at one
 * line on desktop and two on a phone.
 *
 * THE MODEL'S CALL IS PART OF THE ENTRY, NOT A FOOTNOTE. A streak is what has
 * already happened against a line drawn today; the call is a forecast. Where
 * they disagree the badge says so in words, because a reader who takes a 5-0
 * streak off this page while the board calls the other side has been shown two
 * answers by one product and told about neither.
 */
export function SheetRow({ row }: { row: CheatSheetRow }) {
  const stance = agreement(row);
  const perfect = row.hitRate >= 1;

  return (
    <Link
      href={playerHref({
        playerId: row.playerId,
        season: row.season,
        week: row.week,
        market: row.marketKey,
        game: row.gameId,
      })}
      className="border-border-subtle hover:border-border-strong flex flex-col gap-2 border-b px-3 py-2.5 transition-colors last:border-0 sm:flex-row sm:items-center sm:gap-3"
    >
      {/* min-w-0 on every flex child that holds text. Without it a child
          refuses to shrink below its content and pushes the row wide instead,
          which is how this product previously put 76px of horizontal scroll on
          a 390px viewport. */}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <TeamChip
          abbreviation={row.teamAbbreviation}
          color={row.teamColor}
          altColor={row.teamAltColor}
        />
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="text-ink truncate text-sm font-bold">
            {row.playerName}
          </span>
          <span className="text-dim truncate text-[0.6875rem]">
            {row.positionGroup ? `${row.positionGroup} · ` : ""}
            {row.isHome && !row.neutralSite ? "vs" : "@"}{" "}
            {row.opponentAbbreviation ?? row.opponentSchool}
            {row.startDate ? ` · ${formatDateShort(row.startDate)}` : ""}
          </span>
        </span>
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-muted truncate text-[0.6875rem] font-semibold uppercase tracking-label">
          {row.marketEmoji ? `${row.marketEmoji} ` : ""}
          {row.marketLabel ?? row.marketKey}
        </span>
        <span className="flex items-baseline gap-1.5">
          <span
            className={
              "text-xs font-extrabold " +
              (row.hitSide === "over" ? "text-positive" : "text-negative")
            }
          >
            {sideLabel(row)}
          </span>
          {/* A binary market's line is a fixed 0.5 that means "scored at all",
              so printing it beside the word SCORED would be noise. */}
          {!row.isBinary && row.line !== null ? (
            <span className="text-muted text-xs tabular-nums">
              {formatLine(row.line)}
            </span>
          ) : null}
        </span>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <span className="flex w-16 flex-col items-end gap-0.5">
          <span
            className={
              "text-base font-extrabold leading-none tabular-nums " +
              (perfect ? "gradient-text" : "text-ink")
            }
          >
            {Math.round(row.hitRate * 100)}%
          </span>
          {/* The record, always. A percentage over an unstated denominator is
              the one number a sheet like this can most easily lie with. */}
          <span className="text-dim text-[0.625rem] tabular-nums">
            {formatRecord(row.hits, row.decided)} L{row.windowSize}
          </span>
        </span>

        {/* BOTH COLUMNS ARE FIXED-WIDTH, and both widths were set by looking at
            the rendered page rather than guessed. "Model agrees" wrapped onto
            two lines at every width tested, which turned a badge into a
            paragraph — so the pill is one word and "model" moved to the caption
            below. That caption is `whitespace-nowrap`, so a box narrower than
            its text does not clip, it OVERFLOWS: at `w-20` it ran left into the
            record beside it and rendered "5-0 L5Model UNDER 93%". */}
        <span className="flex w-32 shrink-0 flex-col items-end gap-0.5">
          {stance === "no-call" ? (
            <span className="label-caption text-dim">No call</span>
          ) : (
            <>
              <span
                className={
                  "pill " +
                  (stance === "agrees"
                    ? "bg-positive/15 text-positive"
                    : "bg-target/15 text-target")
                }
              >
                {stance === "agrees" ? "Agrees" : "Differs"}
              </span>
              {row.displayConfidence !== null ? (
                <span className="text-dim whitespace-nowrap text-[0.625rem] tabular-nums">
                  Model {row.modelSide?.toUpperCase()}{" "}
                  {formatConfidence(row.displayConfidence)}
                </span>
              ) : null}
            </>
          )}
        </span>
      </div>
    </Link>
  );
}
