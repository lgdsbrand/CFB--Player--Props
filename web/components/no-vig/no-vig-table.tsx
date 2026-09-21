import { TeamChip } from "@/components/board/team-chip";
import { NO_VIG_COLUMNS } from "@/components/no-vig/columns";
import { formatAmericanOdds, formatLine } from "@/lib/core/format";
import {
  booksDisagreeOnLine,
  consensusDelta,
  holdBand,
  type HoldBand,
  type NoVigRow,
} from "@/lib/core/no-vig";
import { playerHref } from "@/lib/core/player-params";
import type { Sport } from "@/lib/core/sport";
import Link from "next/link";

/**
 * The no-vig table: one row per BOOK QUOTE, not per prop.
 *
 * That grain is the point of the page. `v_board_rows` keeps one pick per
 * projection through a sportsbook-priority lateral, so the board can only ever
 * show one book's price; here the same prop appears once per book that posts
 * it, which is what makes the comparison possible. On 2026 week 1 that is 1,246
 * quotes across 671 props — roughly half of them with a rival at the same line.
 *
 * COLUMNS EARN THEIR PLACE OR GO. The client's MLB reference has no equivalent
 * page, so nothing here is copied; each column answers one of the two questions
 * a reader brings: is this price fair (HOLD, FAIR), and is it the best available
 * (BOOK, vs MKT, the best-price marks).
 */
export function NoVigTable({
  rows,
  sport,
}: {
  rows: readonly NoVigRow[];
  /** The page's league. A quote row carries none, and its player link needs one. */
  sport: Sport;
}) {
  return (
    // WIDE CONTENT SCROLLS INSIDE ITS OWN BOX. Nine columns do not fit a phone,
    // and the alternative — letting the page scroll sideways — was a real bug
    // on this site once: one extra nav link pushed the document 0.7px wide and
    // every route scrolled horizontally.
    <div className="panel overflow-x-auto">
      <table className="w-full min-w-[54rem] border-collapse text-sm">
        <thead>
          {/*
            HEADERS COME FROM `NO_VIG_COLUMNS`, not from nine hand-written
            cells. Each one carries the same sentence the expandable glossary
            above the table prints, and reading them from one array is what
            stops a tooltip and the glossary from ever saying different things
            about the same column.

            THE DOTTED UNDERLINE IS THE POINT, not decoration. A bare `title`
            is invisible: nothing on screen suggests there is anything to hover.
            The underline is the only affordance a pointer user gets, and touch
            users are served by the glossary instead, which is why that exists
            rather than tooltips alone.
          */}
          <tr className="text-dim text-left text-[0.625rem] font-bold uppercase tracking-label">
            {NO_VIG_COLUMNS.map((column, index) => (
              <th
                key={column.label}
                scope="col"
                className={
                  "py-2 font-bold " +
                  // First and last cells keep the table's outer gutter.
                  (index === 0 || index === NO_VIG_COLUMNS.length - 1
                    ? "px-3 "
                    : "px-2 ") +
                  (column.align === "right" ? "text-right" : "")
                }
              >
                <span
                  // The underline INHERITS the header's colour rather than
                  // setting its own. It was `decoration-border-subtle` first,
                  // which is rgba(255,255,255,0.06) — a 1px dotted line in it
                  // is invisible at this text size, which makes the affordance
                  // worse than no affordance: the tooltip is there and nothing
                  // says so. Caught in a screenshot, not by typecheck or lint.
                  className="cursor-help underline decoration-dotted underline-offset-2"
                  title={column.help}
                >
                  {column.label}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={row.lineId}
              className={
                "border-border-subtle/60 border-t " +
                (index % 2 === 1 ? "bg-panel-inset/40" : "")
              }
            >
              <td className="px-3 py-2 align-middle">
                <div className="flex items-center gap-2">
                  <TeamChip
                    abbreviation={row.teamAbbreviation}
                    color={row.teamColor}
                    altColor={row.teamAltColor}
                    title={row.teamSchool}
                  />
                  <div className="flex min-w-0 flex-col">
                    <Link
                      href={playerHref({
                        playerId: row.playerId,
                        // From the PAGE: a quote row carries no sport, and
                        // without one an NFL player opened as a college player.
                        sport,
                        season: row.season,
                        week: row.week,
                        // The player page opens on the market this quote is
                        // about, so the two surfaces are talking about the same
                        // number when the reader arrives.
                        market: row.marketKey,
                        game: row.gameId,
                      })}
                      className="hover:text-accent-cyan truncate font-semibold"
                    >
                      {row.playerName}
                    </Link>
                    <span className="text-dim truncate text-[0.625rem] font-semibold uppercase tracking-label">
                      {row.positionGroup ?? "—"} ·{" "}
                      {row.isHome ? "vs" : "@"} {row.opponentAbbreviation}
                    </span>
                  </div>
                </div>
              </td>

              <td className="text-muted px-2 py-2 align-middle text-[0.6875rem] font-semibold uppercase tracking-label">
                <span className="flex items-center gap-1">
                  {row.marketEmoji ? (
                    <span aria-hidden>{row.marketEmoji}</span>
                  ) : null}
                  {row.marketLabel}
                </span>
              </td>

              <td className="px-2 py-2 text-right align-middle">
                <span className="font-mono text-xs font-bold tabular-nums">
                  {formatLine(row.line)}
                </span>
                {booksDisagreeOnLine(row) ? (
                  <span
                    className="text-dim ml-1 text-[0.625rem]"
                    title={`Books post ${row.linesOnMarket} different lines for this prop. Prices are only compared against the ${row.booksAtLine} at this one.`}
                  >
                    ×{row.linesOnMarket}
                  </span>
                ) : null}
              </td>

              <td className="px-2 py-2 align-middle text-xs">
                {row.sportsbookName}
              </td>

              {/* Posted, then fair, side by side: the charge is the difference,
                  and reading it as two prices is what makes it concrete. */}
              <td className="px-2 py-2 text-right align-middle font-mono text-xs tabular-nums">
                <PriceCell
                  over={formatAmericanOdds(row.overPrice)}
                  under={formatAmericanOdds(row.underPrice)}
                  bestOver={row.isBestOver && row.booksAtLine > 1}
                  bestUnder={row.isBestUnder && row.booksAtLine > 1}
                />
              </td>

              <td className="text-muted px-2 py-2 text-right align-middle font-mono text-xs tabular-nums">
                <PriceCell
                  over={formatAmericanOdds(row.fairPriceOver)}
                  under={formatAmericanOdds(row.fairPriceUnder)}
                />
              </td>

              <td className="px-2 py-2 text-right align-middle">
                <HoldCell hold={row.hold} />
              </td>

              <td className="px-2 py-2 text-right align-middle font-mono text-xs tabular-nums">
                <span className="text-ink">
                  {(row.fairProbOver * 100).toFixed(1)}%
                </span>
                <span className="text-dim"> / </span>
                <span className="text-muted">
                  {(row.fairProbUnder * 100).toFixed(1)}%
                </span>
              </td>

              <td className="px-3 py-2 text-right align-middle">
                <ConsensusCell row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Over above under, with the best price on each side marked. */
function PriceCell({
  over,
  under,
  bestOver = false,
  bestUnder = false,
}: {
  over: string;
  under: string;
  bestOver?: boolean;
  bestUnder?: boolean;
}) {
  return (
    <div className="flex flex-col items-end leading-tight">
      <span className={bestOver ? "text-positive font-bold" : undefined}>
        {over}
        {bestOver ? <span title="Best over price at this line"> ★</span> : null}
      </span>
      <span className={bestUnder ? "text-positive font-bold" : undefined}>
        {under}
        {bestUnder ? <span title="Best under price at this line"> ★</span> : null}
      </span>
    </div>
  );
}

const HOLD_TONE: Record<HoldBand, string> = {
  keen: "text-positive",
  typical: "text-ink",
  dear: "text-target",
  wide: "text-negative",
};

const HOLD_TITLE: Record<HoldBand, string> = {
  keen: "Keen — below 5%, cheaper than anything measured on a full week",
  typical: "Typical for this market — the slate averages about 6.6%",
  dear: "Dear — 7% or more, toward the top of the observed range",
  wide: "Wide — 9% or more, worse than any quote measured so far",
};

function HoldCell({ hold }: { hold: number }) {
  const band = holdBand(hold);
  return (
    <span
      className={"font-mono text-xs font-bold tabular-nums " + HOLD_TONE[band]}
      title={HOLD_TITLE[band]}
    >
      {(hold * 100).toFixed(2)}%
    </span>
  );
}

/**
 * How far this book sits from the others at the same line.
 *
 * A dash, NOT a zero, when it is the only book there. Zero would say the book
 * agrees with the market; the truth is that there is no market to agree with,
 * and the two must not render the same.
 */
function ConsensusCell({ row }: { row: NoVigRow }) {
  const delta = consensusDelta(row);

  if (delta === null) {
    return (
      <span className="text-dim text-xs" title="Only book posting this line">
        —
      </span>
    );
  }

  const points = delta * 100;
  const spread = (row.lineProbOverMax - row.lineProbOverMin) * 100;

  return (
    <span
      className={
        "font-mono text-xs tabular-nums " +
        (Math.abs(points) >= 1 ? "text-ink font-bold" : "text-muted")
      }
      title={
        `This book's fair over probability is ${points >= 0 ? "+" : ""}` +
        `${points.toFixed(1)} points against the mean of the ${row.booksAtLine} ` +
        `books at this line, which span ${spread.toFixed(1)} points.`
      }
    >
      {points >= 0 ? "+" : ""}
      {points.toFixed(1)}
    </span>
  );
}
