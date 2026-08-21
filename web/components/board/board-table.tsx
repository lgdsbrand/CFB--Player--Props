import Link from "next/link";

import { EvidencePill } from "@/components/board/evidence-pill";
import { LastFive } from "@/components/board/last-five";
import { ProjectionBar } from "@/components/board/projection-bar";
import { TableRow } from "@/components/board/table-row";
import { TeamChip } from "@/components/board/team-chip";
import {
  callFor,
  displayQuantile,
  gradeRow,
  seasonToDate,
} from "@/lib/core/board-view";
import { rankBasis } from "@/lib/core/defense-view";
import {
  formatAmericanOdds,
  formatConfidence,
  formatEdge,
  formatGameLine,
  formatKickoff,
  formatLine,
  formatVenue,
  meetsEdgeThreshold,
} from "@/lib/core/format";
import {
  formatHitRate,
  hitRate,
  hitRateTone,
  type GradedGame,
} from "@/lib/core/hit-rate";
import { playerHref } from "@/lib/core/player-params";
import type { BoardRow, Market, PlayerGameLogRow } from "@/lib/core/types";

/**
 * The board as a table: one row per PROP.
 *
 * WHY THIS EXISTS BESIDE THE CARD GRID, measured on 2025 week 9 rather than
 * asserted. With MARKET set to All a card carries up to six sub-cards and runs
 * to 963px, and a row here is 59px:
 *
 *   1440px   cards 68px per prop (three columns)   table 59px   1.2x
 *    768px   cards 161px per prop (one column)     table 59px   2.7x
 *    390px   cards 167px per prop (one column)     table 59px   2.8x
 *
 * SO THE HEIGHT IS NOT THE WHOLE ARGUMENT AT DESKTOP, and it is honest to say
 * so: at 1440 the three-column grid packs props about as tightly as this does.
 * What the grid cannot do at any width is line them up. Every figure here sits
 * in a column, so a reader can run one finger down CHANCE or L5 across the
 * whole slate instead of re-finding the number inside each bordered block. On
 * one column — every phone, and every tablet — it is both, at ~2.7x.
 *
 * The card grid is still the better layout for ONE market, where a card holds a
 * single sub-card and can afford the projection bar and the dots inline. Neither
 * replaces the other — see `resolveBoardView`.
 *
 * MODELLED ON THE CLIENT'S REFERENCE, NOT COPIED FROM IT. His screenshot is an
 * MLB product; three of its columns do not survive the port and are deliberately
 * absent:
 *
 *   L20      a college team plays 12-13 games, so L20 would be the season for
 *            every player and would print the same figure as SZN beside it.
 *   H2H      teams meet once a year at most, and the transfer portal means last
 *            year's meeting often involved a different roster (CLAUDE.md §6).
 *            The column would be empty or a one-game sample on nearly every row.
 *   PARK     no football analogue. Weather is the equivalent and has its own
 *            panel on the game and player pages.
 *
 * OPP RK takes H2H's place: it answers the same "is this a good matchup"
 * question with a number this product actually computes (CLAUDE.md §5).
 */
export function BoardTable({
  rows,
  marketsByKey,
  gameLogs,
  hitRateWindows,
  hitRateWindow,
  edgeThreshold,
}: {
  rows: BoardRow[];
  marketsByKey: Map<string, Market>;
  gameLogs: Map<number, PlayerGameLogRow[]>;
  /** One hit-rate column per configured window, from `app_config`. */
  hitRateWindows: number[];
  /** The window the detail's last-N dot row uses — the HIT RATE pill group. */
  hitRateWindow: number;
  edgeThreshold: number;
}) {
  // Nine fixed columns — expander, player, prop, chance, proj, edge, odds, szn,
  // opp rk — plus one per configured hit-rate window. Derived rather than
  // written as a literal because the detail row spans it, and a colSpan that
  // drifts from the header is invisible until a column is added.
  const columnCount = 9 + hitRateWindows.length;

  return (
    <div className="panel overflow-hidden">
      {/*
        SAID IN WORDS, NOT LEFT TO A CUT-OFF EDGE. This board has learned once
        already that a horizontal scroller with no affordance reads as a
        rendering fault rather than as "there is more this way" — the week strip
        sliced cards mid-word and was reported as broken. The `.scroll-fade-x`
        mask that fixed it is wrong here: it fades both edges whether or not the
        content overflows, so at 1440, where this table fits, it would
        permanently dim the player names and the OPP RK column for no reason.
        A line that disappears at `lg` costs nothing and cannot mislead.
      */}
      <p className="text-dim border-border-subtle/60 border-b px-3 py-2 text-[0.625rem] lg:hidden">
        Scroll sideways for chance, odds and hit rates.
      </p>

      {/*
        `min-w-[62rem]` and a scrolling parent, rather than a stacked card
        fallback at phone width. A table whose columns collapse into stacked
        labels is a card again, and worse than the card view already here — so
        below ~1000px this scrolls sideways and the reader keeps the alignment
        the layout exists for. The wrapper is what scrolls, never the body.
      */}
      <div className="overflow-x-auto">
        <table
          aria-label="Player props, one row per prop"
          className="w-full min-w-[62rem] border-collapse text-left"
        >
          <thead>
            <tr className="label-caption">
              <th scope="col" className="w-8 py-2.5 pl-3" />
              <th scope="col" className="py-2.5 pr-3 font-semibold">
                Player
              </th>
              <th scope="col" className="py-2.5 pr-3 font-semibold">
                Prop
              </th>
              <th scope="col" className="py-2.5 pr-3 text-right font-semibold">
                Chance
              </th>
              <th scope="col" className="py-2.5 pr-3 text-right font-semibold">
                Proj
              </th>
              <th scope="col" className="py-2.5 pr-3 text-right font-semibold">
                Edge
              </th>
              <th scope="col" className="py-2.5 pr-3 font-semibold">
                Odds
              </th>
              {hitRateWindows.map((window) => (
                <th
                  key={window}
                  scope="col"
                  className="py-2.5 pr-3 text-right font-semibold"
                  title={`Hit rate over this player's last ${window} games played, graded against the line showing now`}
                >
                  L{window}
                </th>
              ))}
              <th
                scope="col"
                className="py-2.5 pr-3 text-right font-semibold"
                title="Hit rate across this season up to (not including) the week on screen"
              >
                Szn
              </th>
              <th
                scope="col"
                className="py-2.5 pr-3 text-right font-semibold"
                title="Opponent rank vs this position — 1 is the best defense, so a HIGH number is the softer matchup"
              >
                Opp Rk
              </th>
            </tr>
          </thead>

          <tbody>
            {rows.map((row, index) => (
              <PropRow
                key={row.projectionId}
                row={row}
                market={marketsByKey.get(row.marketKey)}
                games={gameLogs.get(row.playerId) ?? []}
                hitRateWindows={hitRateWindows}
                hitRateWindow={hitRateWindow}
                edgeThreshold={edgeThreshold}
                columnCount={columnCount}
                striped={index % 2 === 1}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PropRow({
  row,
  market,
  games,
  hitRateWindows,
  hitRateWindow,
  edgeThreshold,
  columnCount,
  striped,
}: {
  row: BoardRow;
  market: Market | undefined;
  games: PlayerGameLogRow[];
  hitRateWindows: number[];
  hitRateWindow: number;
  edgeThreshold: number;
  columnCount: number;
  striped: boolean;
}) {
  const call = callFor(row);

  // GRADED ONCE, then sliced per column. The card grades per market because it
  // shows one window; this shows three, and re-running `gradeGames` for each
  // would walk the same season three times per row — 150 passes on a 50-row
  // page — for identical output.
  const graded = gradeRow(row, market, games);
  const label = `${row.playerName} ${row.marketLabel ?? row.marketName}`;

  return (
    <TableRow
      label={label}
      columnCount={columnCount}
      striped={striped}
      summary={
        <>
          <td className="py-2 pr-3 align-middle">
            <PlayerCell row={row} />
          </td>
          <td className="py-2 pr-3 align-middle">
            <PropCell row={row} call={call} />
          </td>
          <td className="py-2 pr-3 text-right align-middle">
            <ChanceCell call={call} />
          </td>
          <td className="text-muted py-2 pr-3 text-right align-middle font-mono text-xs tabular-nums">
            <ProjCell row={row} />
          </td>
          <td className="py-2 pr-3 text-right align-middle">
            <EdgeCell edge={row.edge} edgeThreshold={edgeThreshold} />
          </td>
          <td className="py-2 pr-3 align-middle">
            <OddsCell row={row} />
          </td>
          {hitRateWindows.map((window) => (
            <HitRateCell
              key={window}
              graded={graded}
              summary={graded.length > 0 ? hitRate(graded, window) : null}
            />
          ))}
          <HitRateCell graded={graded} summary={seasonToDate(graded)} />
          <td className="text-muted py-2 pr-3 text-right align-middle font-mono text-xs tabular-nums">
            {row.opponentRankVsPosition ?? "—"}
          </td>
        </>
      }
      detail={
        <RowDetail
          row={row}
          graded={graded}
          call={call}
          hitRateWindow={hitRateWindow}
        />
      }
    />
  );
}

function PlayerCell({ row }: { row: BoardRow }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <div className="flex items-center gap-1.5">
        <Link
          href={playerHref({
            playerId: row.playerId,
            season: row.season,
            week: row.week,
          })}
          className="hover:text-accent-cyan truncate text-xs font-bold transition-colors"
        >
          {row.playerName}
        </Link>
        {row.positionGroup ? (
          <span className="pill bg-accent-indigo/15 text-accent-cyan shrink-0">
            {row.positionGroup}
          </span>
        ) : null}
      </div>
      <div className="text-dim flex items-center gap-1 text-[0.625rem]">
        <TeamChip
          abbreviation={row.teamAbbreviation}
          color={row.teamColor}
          altColor={row.teamAltColor}
          title={row.teamSchool}
        />
        <span>{row.neutralSite ? "vs" : row.isHome ? "vs" : "@"}</span>
        <TeamChip
          abbreviation={row.opponentAbbreviation}
          color={null}
          altColor={null}
          title={row.opponentSchool}
        />
        <span className="truncate">· {formatKickoff(row.startDate)}</span>
      </div>
    </div>
  );
}

/**
 * The prop itself: the side and line a reader is being asked about, then what
 * market it is — the reading order of the client's reference row.
 *
 * A BINARY MARKET GETS NO SIDE PILL. "Over 0.5 offensive touchdowns" is the
 * structural line every anytime-TD row carries whether or not a book is taking
 * bets on it, so an OVER pill there would imply a call the model did not make.
 * The probability in the next column is the whole claim.
 */
function PropCell({
  row,
  call,
}: {
  row: BoardRow;
  call: ReturnType<typeof callFor>;
}) {
  return (
    <div className="flex items-center gap-1.5">
      {call.kind === "call" ? (
        <span
          className={
            "pill shrink-0 " +
            (call.side === "over"
              ? "bg-positive/15 text-positive"
              : "bg-negative/15 text-negative")
          }
        >
          {call.side}
        </span>
      ) : call.kind === "lean" ? (
        <span
          className="pill bg-panel-inset text-dim shrink-0"
          title="No book has posted a line for this prop yet, so there is nothing to call it against. The projection is in the next column."
        >
          Lean
        </span>
      ) : null}

      {row.line !== null && call.kind !== "binary" ? (
        <span className="font-mono text-xs font-bold tabular-nums">
          {formatLine(row.line)}
        </span>
      ) : null}

      <span className="text-muted flex items-center gap-1 truncate text-[0.6875rem] font-semibold uppercase tracking-label">
        {row.marketEmoji ? <span aria-hidden>{row.marketEmoji}</span> : null}
        {row.marketLabel ?? row.marketName}
      </span>
    </div>
  );
}

/**
 * The headline number, and the reason the core owns the three states.
 *
 * `callFor` has already inverted anytime TD — a row whose called side is `under`
 * on a 12% scorer reports as `binary` carrying 12%, not as a call carrying 88%.
 */
function ChanceCell({ call }: { call: ReturnType<typeof callFor> }) {
  if (call.kind === "binary") {
    return call.probability === null ? (
      <span className="text-dim text-xs">—</span>
    ) : (
      <span
        className="gradient-text text-sm font-extrabold tabular-nums"
        title="Probability this player scores a touchdown — an anytime-scorer probability, never a projected count"
      >
        {formatConfidence(call.probability)}
      </span>
    );
  }

  if (call.kind === "call") {
    return (
      <span
        className="gradient-text text-sm font-extrabold tabular-nums"
        title="Share of the projected distribution past the line"
      >
        {formatConfidence(call.confidence)}
      </span>
    );
  }

  return <span className="text-dim text-xs">—</span>;
}

/**
 * The projected median — SECONDARY detail, never the headline (CLAUDE.md §1),
 * which is why it is muted, small and monospaced rather than gradient-filled.
 *
 * It earns its column on the rows that have nothing else. College books post
 * props on Thursday or Friday for Saturday games, so for most of a live week
 * most rows carry no line, no call, no confidence and no edge — and without this
 * they would be a row of dashes. The model's lean IS the answer on those rows
 * (CLAUDE.md §7), and this is where it shows.
 *
 * Binary markets get a dash: p10 and p90 collapse onto the same point, so there
 * is no meaningful projected count to print beside a probability.
 */
function ProjCell({ row }: { row: BoardRow }) {
  if (row.isBinary) return <>—</>;
  const median = displayQuantile(row.projectedMedian);
  return <>{median === null ? "—" : formatLine(median)}</>;
}

function EdgeCell({
  edge,
  edgeThreshold,
}: {
  edge: number | null;
  edgeThreshold: number;
}) {
  if (edge === null) return <span className="text-dim text-xs">—</span>;
  return (
    <span
      className={
        "font-mono text-xs font-bold tabular-nums " +
        (meetsEdgeThreshold(edge, edgeThreshold) ? "text-target" : "text-muted")
      }
    >
      {formatEdge(edge)}
    </span>
  );
}

function OddsCell({ row }: { row: BoardRow }) {
  if (!row.hasBookLine || !row.sportsbookName) {
    return <span className="text-dim text-[0.6875rem]">No line yet</span>;
  }
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted truncate text-[0.625rem] font-semibold">
        {row.sportsbookName}
      </span>
      <span className="text-dim font-mono text-[0.6875rem] tabular-nums">
        {formatAmericanOdds(row.overPrice)} /{" "}
        {formatAmericanOdds(row.underPrice)}
      </span>
    </div>
  );
}

const TONE_CLASS = {
  positive: "text-positive",
  negative: "text-negative",
  muted: "text-muted",
} as const;

/**
 * One hit-rate column.
 *
 * THE DENOMINATOR IS IN THE TOOLTIP FOR A REASON. "100%" off two decided games
 * and "100%" off eleven are the same three characters and very different claims,
 * and this layout has no room to print both. The card view says it inline; here
 * it is one hover away, and a rate with a thin sample is still never dressed up
 * as more than it is because the tone bands (`hitRateTone`) keep the middle grey.
 */
function HitRateCell({
  graded,
  summary,
}: {
  graded: GradedGame[];
  summary: ReturnType<typeof hitRate> | null;
}) {
  if (graded.length === 0 || !summary) {
    return (
      <td
        className="text-dim py-2 pr-3 text-right align-middle text-xs"
        title="No line to grade past games against"
      >
        —
      </td>
    );
  }

  return (
    <td
      className={
        "py-2 pr-3 text-right align-middle font-mono text-xs font-bold tabular-nums " +
        TONE_CLASS[hitRateTone(summary.rate)]
      }
      title={`${summary.hits} of ${summary.decided} decided${
        summary.pushes > 0 ? `, ${summary.pushes} pushed` : ""
      }`}
    >
      {formatHitRate(summary.rate)}
    </td>
  );
}

/**
 * What the row expands into: everything the card sub-card shows that a table
 * cell cannot.
 *
 * THIS IS WHY THE TABLE DOES NOT LOSE ANYTHING. The projected-vs-line bar and
 * the last-N dot row are named in CLAUDE.md §7 as components to carry over from
 * the client's pitcher card, and a flat table has nowhere to put either. Putting
 * them behind the row's own toggle keeps both a click away instead of dropping
 * them — which also answers the second half of the client's message, the
 * collapsible card that opens to show more.
 */
function RowDetail({
  row,
  graded,
  call,
  hitRateWindow,
}: {
  row: BoardRow;
  graded: GradedGame[];
  call: ReturnType<typeof callFor>;
  hitRateWindow: number;
}) {
  const venue = formatVenue({
    name: row.venueName,
    city: row.venueCity,
    state: row.venueState,
  });
  const gameLine = formatGameLine(row.teamSpread, row.gameTotal);

  return (
    <div className="panel-inset flex flex-col gap-2.5 p-3">
      {row.isBinary ? null : (
        <ProjectionBar
          median={row.projectedMedian}
          p10={row.projectedP10}
          p90={row.projectedP90}
          line={row.line}
          side={row.side}
        />
      )}

      <LastFive
        summary={graded.length > 0 ? hitRate(graded, hitRateWindow) : null}
        side={row.isBinary ? "over" : row.side}
        window={hitRateWindow}
        verb={row.isBinary ? "scored" : undefined}
      />

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <EvidencePill
          priorWeight={row.priorWeight}
          effectiveSample={row.effectiveSample}
        />
        {row.opponentRankVsPosition !== null && row.positionGroup ? (
          <span className="text-dim text-[0.625rem]">
            Rk {row.opponentRankVsPosition} vs {row.positionGroup} on{" "}
            {rankBasis(row.positionGroup).label}
          </span>
        ) : null}
        {gameLine ? (
          <span className="text-dim text-[0.625rem] tabular-nums">
            {gameLine}
          </span>
        ) : null}
        {venue ? (
          <span className="text-dim truncate text-[0.625rem]">{venue}</span>
        ) : null}
        {call.kind === "lean" ? (
          <span className="text-dim text-[0.625rem]">
            No book line yet — the range above is the model&rsquo;s lean.
          </span>
        ) : null}

        <Link
          href={playerHref({
            playerId: row.playerId,
            season: row.season,
            week: row.week,
          })}
          className="text-accent-cyan ml-auto shrink-0 text-[0.625rem] font-bold uppercase tracking-label hover:underline"
        >
          Full detail →
        </Link>
      </div>
    </div>
  );
}
