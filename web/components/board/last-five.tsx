import { formatHitRate, type HitRateSummary } from "@/lib/core/hit-rate";

/**
 * The recent-form row: "LAST 5 (80%) HITS UNDER · ● ● ● ● ●".
 *
 * Straight from the client's pitcher card (CLAUDE.md §7), with one addition
 * their board does not need: an explicit empty state. College books post props
 * late and selectively, so a market with no line has nothing to grade past
 * games against — and a row of grey circles saying "no line yet" is honest
 * where five green ones would be a fabrication.
 *
 * CIRCLES READ RIGHT-TO-LEFT IN TIME: the leftmost is the most recent game,
 * matching the order the game log returns and the way the client's board reads.
 */
export function LastFive({
  summary,
  side,
  window,
  verb,
}: {
  summary: HitRateSummary | null;
  side: "over" | "under" | null;
  window: number;
  /**
   * Overrides the "hits over/under" caption. Binary markets are graded on the
   * OVER side so a green dot means the player scored; describing that as "hits
   * over" would be technically true and unreadable.
   */
  verb?: string;
}) {
  if (!summary || summary.games.length === 0) {
    return (
      <div className="text-dim flex items-center gap-1.5 text-[0.625rem] font-semibold uppercase tracking-label">
        Last {window}
        <span className="text-dim/70 normal-case tracking-normal">
          — no line to grade against
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="text-dim text-[0.625rem] font-semibold uppercase tracking-label">
        Last {window}
      </span>
      <span
        className={
          "text-[0.625rem] font-bold " +
          (summary.rate !== null && summary.rate >= 0.6
            ? "text-positive"
            : "text-muted")
        }
      >
        {formatHitRate(summary.rate)}
      </span>
      {verb ?? side ? (
        <span className="text-dim text-[0.625rem] font-semibold uppercase tracking-label">
          {verb ?? `hits ${side}`}
        </span>
      ) : null}

      <span className="flex items-center gap-1" aria-hidden>
        {summary.games.map((game) => (
          <span
            key={game.gameId}
            title={`Wk ${game.week} vs ${game.opponentAbbreviation ?? "?"}: ${game.value} (${game.outcome})`}
            className={
              "size-2 rounded-full " +
              (game.hit === null
                ? "bg-muted/50"
                : game.hit
                  ? "bg-positive"
                  : "bg-negative")
            }
          />
        ))}
      </span>

      <span className="sr-only">
        {summary.hits} of {summary.decided} recent games{" "}
        {verb ?? `hit the ${side} side`}
        {summary.pushes > 0 ? `, ${summary.pushes} pushed` : ""}.
      </span>
    </div>
  );
}
