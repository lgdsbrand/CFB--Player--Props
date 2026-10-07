import {
  formatPoints,
  lineMove,
  sharpRetailGap,
  spreadLabel,
  spreadMoveToward,
  totalMoveLabel,
  type GameOddsSummary,
} from "@/lib/core/game-lines";

/**
 * Sharp against retail on one game: the consensus, Pinnacle and the US books'
 * median, for the full-game spread and total.
 *
 * MOVED HERE FROM THE SLATE TABLE (client, 2026-10-06). The table became one
 * opinion per market — the book line the model's edge was priced against —
 * and the market survey it used to carry lives with the game it describes.
 * Same numbers and the same wording as those columns had, from the same
 * `v_game_odds_summary` row. "Sharp" means sharp PRICES (CLAUDE.md §11),
 * never betting percentages.
 */
export function SharpRetailPanel({
  summary,
  home,
  away,
}: {
  summary: GameOddsSummary | undefined;
  home: string;
  away: string;
}) {
  const spreads = summary?.spreads;
  const totals = summary?.totals;
  if (!spreads && !totals) return null;

  const spreadMove = spreadMoveToward(
    lineMove(spreads?.consensusLine ?? null, spreads?.consensusFirstLine ?? null),
    home,
    away,
  );
  const totalMove = totalMoveLabel(
    lineMove(totals?.consensusLine ?? null, totals?.consensusFirstLine ?? null),
  );
  const gap = sharpRetailGap(spreads);
  const points = (value: number | null | undefined) =>
    value === null || value === undefined ? null : formatPoints(value);

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">⚖️ Sharp vs retail</h2>
        <p className="text-muted text-xs">
          Consensus is the median across every book we capture. Sharp is
          Pinnacle; retail is the median of the US books (DraftKings, FanDuel,
          BetMGM, BetRivers, Caesars, Fanatics). A retail spread a point or
          more off Pinnacle&rsquo;s is highlighted.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        <Stat
          label="Spread · consensus"
          value={spreadLabel(spreads?.consensusLine ?? null, home, away)}
          note={spreadMove ? `moved ${spreadMove.points} to ${spreadMove.team}` : null}
        />
        <Stat
          label="Spread · sharp"
          value={spreadLabel(spreads?.sharpLine ?? null, home, away)}
        />
        <Stat
          label="Spread · retail"
          value={spreadLabel(spreads?.retailLine ?? null, home, away)}
          // The side where retail hands out MORE points than Pinnacle: home
          // when retail's home line sits above the sharp one, away otherwise.
          note={
            gap === null ? null : `+${formatPoints(gap)} on ${gap > 0 ? home : away} vs sharp`
          }
          highlight={gap !== null && Math.abs(gap) >= 1}
        />
        <Stat label="Books" value={spreads?.books?.toString() ?? null} />
        <Stat
          label="Total · consensus"
          value={points(totals?.consensusLine)}
          note={totalMove}
        />
        <Stat label="Total · sharp" value={points(totals?.sharpLine)} />
        <Stat label="Total · retail" value={points(totals?.retailLine)} />
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  note,
  highlight,
}: {
  label: string;
  value: string | null;
  note?: string | null;
  highlight?: boolean;
}) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="label-caption">{label}</span>
      <span
        className={
          "text-sm font-extrabold whitespace-nowrap tabular-nums " +
          (value === null ? "text-dim" : highlight ? "text-target" : "text-ink")
        }
      >
        {value ?? "—"}
      </span>
      {note ? <span className="text-dim text-[0.6875rem]">{note}</span> : null}
    </span>
  );
}
