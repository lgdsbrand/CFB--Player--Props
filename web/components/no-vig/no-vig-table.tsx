import { TeamChip } from "@/components/board/team-chip";
import { formatAmericanOdds, formatLine } from "@/lib/core/format";
import {
  booksDisagreeOnLine,
  consensusDelta,
  holdBand,
  type HoldBand,
  type NoVigRow,
} from "@/lib/core/no-vig";
import { playerHref } from "@/lib/core/player-params";
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
export function NoVigTable({ rows }: { rows: readonly NoVigRow[] }) {
  return (
    // WIDE CONTENT SCROLLS INSIDE ITS OWN BOX. Nine columns do not fit a phone,
    // and the alternative — letting the page scroll sideways — was a real bug
    // on this site once: one extra nav link pushed the document 0.7px wide and
    // every route scrolled horizontally.
    <div className="panel overflow-x-auto">
      <table className="w-full min-w-[54rem] border-collapse text-sm">
        <thead>
          <tr className="text-dim text-left text-[0.625rem] font-bold uppercase tracking-label">
            <th scope="col" className="px-3 py-2 font-bold">
              Player
            </th>
            <th scope="col" className="px-2 py-2 font-bold">
              Market
            </th>
            <th scope="col" className="px-2 py-2 text-right font-bold">
              Line
            </th>
            <th scope="col" className="px-2 py-2 font-bold">
              Book
            </th>
            <th scope="col" className="px-2 py-2 text-right font-bold">
              Posted
            </th>
            <th scope="col" className="px-2 py-2 text-right font-bold">
              Fair
            </th>
            <th scope="col" className="px-2 py-2 text-right font-bold">
              Hold
            </th>
            <th scope="col" className="px-2 py-2 text-right font-bold">
              Fair %
            </th>
            <th scope="col" className="px-3 py-2 text-right font-bold">
              vs Mkt
            </th>
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
