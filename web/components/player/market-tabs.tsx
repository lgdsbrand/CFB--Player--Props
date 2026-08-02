import Link from "next/link";

import { formatConfidence } from "@/lib/core/format";
import { playerHref, type PlayerParams } from "@/lib/core/player-params";
import type { BoardRow } from "@/lib/core/types";

/**
 * Every market this player has this week, one of them selected.
 *
 * Doubles as the stat selector (CLAUDE.md §7) and as the at-a-glance summary:
 * each tab carries its own call, so the whole week is readable without clicking
 * anything, and selecting one re-grades the chart, splits and log below against
 * that market's line.
 *
 * ONE CHART, NOT FIVE. A quarterback carries five markets and stacking five
 * charts down a page makes none of them read. The prototype's stat dropdown
 * solves the same problem; these are the same control with the answer already
 * on the label.
 */
export function MarketTabs({
  rows,
  activeKey,
  params,
}: {
  rows: BoardRow[];
  activeKey: string;
  params: PlayerParams;
}) {
  return (
    <nav
      aria-label="Markets"
      className="flex flex-wrap gap-1.5"
    >
      {rows.map((row) => {
        const active = row.marketKey === activeKey;
        return (
          <Link
            key={row.projectionId}
            href={playerHref(params, { market: row.marketKey })}
            aria-current={active ? "page" : undefined}
            className={
              "flex items-center gap-2 rounded-xl border px-3 py-2 transition-colors " +
              (active
                ? "border-accent-cyan/50 bg-accent-indigo/10"
                : "border-border-subtle bg-panel hover:border-border-strong")
            }
          >
            <span
              className={
                "flex items-center gap-1.5 text-[0.6875rem] font-bold uppercase tracking-label " +
                (active ? "text-ink" : "text-muted")
              }
            >
              {row.marketEmoji ? <span aria-hidden>{row.marketEmoji}</span> : null}
              {row.marketLabel ?? row.marketName}
            </span>
            <Headline row={row} />
          </Link>
        );
      })}
    </nav>
  );
}

/**
 * The tab's number.
 *
 * Three cases, and the middle one is the common one right now: a binary market
 * shows the anytime-scorer probability (never a called side — see
 * `market-row.tsx`), a called market shows side and confidence, and a market
 * with no line yet says so rather than borrowing a number from somewhere else.
 */
function Headline({ row }: { row: BoardRow }) {
  if (row.isBinary) {
    return row.modelProbOver === null ? (
      <span className="text-dim text-[0.625rem]">—</span>
    ) : (
      <span className="gradient-text text-sm font-extrabold leading-none">
        {formatConfidence(row.modelProbOver)}
      </span>
    );
  }

  if (row.hasCall && row.side && row.confidence !== null) {
    return (
      <span className="flex items-center gap-1.5">
        <span
          className={
            "pill " +
            (row.side === "over"
              ? "bg-positive/15 text-positive"
              : "bg-negative/15 text-negative")
          }
        >
          {row.side}
        </span>
        <span className="gradient-text text-sm font-extrabold leading-none">
          {formatConfidence(row.confidence)}
        </span>
      </span>
    );
  }

  return <span className="pill bg-panel-inset text-muted">Lean</span>;
}
