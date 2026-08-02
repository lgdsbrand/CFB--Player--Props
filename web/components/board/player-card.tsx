import Link from "next/link";

import { MarketRow } from "@/components/board/market-row";
import { TeamChip } from "@/components/board/team-chip";
import { formatKickoff } from "@/lib/core/format";
import { playerHref } from "@/lib/core/player-params";
import { gradeFor, gradeToneToken } from "@/lib/core/grade";
import { gradeGames, hitRate, type HitRateSummary } from "@/lib/core/hit-rate";
import type { PlayerCard as PlayerCardData } from "@/lib/core/board-view";
import type { Market, PlayerGameLogRow } from "@/lib/core/types";

/**
 * One player's card: header, then a sub-card per market.
 *
 * Mirrors the client's pitcher card (CLAUDE.md §7) — rounded card, player name
 * with a position pill, GRADE and CONF badges, then per-market sub-cards. The
 * GRADE is a coarser restatement of the confidence percentage and nothing more;
 * see `lib/core/grade.ts` for why it deliberately carries no new information.
 *
 * HIT RATES ARE COMPUTED HERE, per market, because they depend on the LINE and
 * the line is only known at render time. Passing the log down and grading it in
 * the leaf keeps one read per page rather than one per card.
 */
export function PlayerCard({
  card,
  marketsByKey,
  gameLog,
  hitRateWindow,
  edgeThreshold,
}: {
  card: PlayerCardData;
  marketsByKey: Map<string, Market>;
  gameLog: PlayerGameLogRow[];
  hitRateWindow: number;
  edgeThreshold: number;
}) {
  const grade = gradeFor(card.topConfidence);
  const tone = gradeToneToken(grade);

  // CLAUDE.md §7 says the detail view opens "on row click". A stretched link
  // gives the whole card that behaviour while keeping exactly one anchor, so
  // the card is not a nest of links and the accessible name is the player's.
  const href = playerHref({
    playerId: card.playerId,
    season: card.markets[0].season,
    week: card.markets[0].week,
  });

  return (
    <article className="panel relative flex flex-col gap-3 p-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-bold">
              <Link
                href={href}
                className="hover:text-accent-cyan transition-colors after:absolute after:inset-0 after:content-['']"
              >
                {card.playerName}
              </Link>
            </h3>
            {card.positionGroup ? (
              <span className="pill bg-accent-indigo/15 text-accent-cyan">
                {card.positionGroup}
              </span>
            ) : null}
          </div>

          <div className="text-muted flex flex-wrap items-center gap-1.5 text-[0.6875rem]">
            <TeamChip
              abbreviation={card.teamAbbreviation}
              color={card.teamColor}
              altColor={card.teamAltColor}
              title={card.teamSchool}
            />
            <span className="text-dim">
              {card.neutralSite ? "vs" : card.isHome ? "vs" : "@"}
            </span>
            <TeamChip
              abbreviation={card.opponentAbbreviation}
              color={null}
              altColor={null}
              title={card.opponentSchool}
            />
            <span className="text-dim">·</span>
            <span>{formatKickoff(card.startDate)}</span>
            {card.neutralSite ? (
              <span className="text-dim">· neutral</span>
            ) : null}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {card.opponentRankVsPosition !== null ? (
            <span
              className="pill bg-panel-inset text-muted"
              title="Opponent rank vs this position — 1 allows the most, so a LOW number is the softer matchup"
            >
              Rk {card.opponentRankVsPosition}
            </span>
          ) : null}
          {grade ? (
            <span
              className={
                "pill " +
                (tone === "positive"
                  ? "bg-positive/15 text-positive"
                  : tone === "target"
                    ? "bg-target/15 text-target"
                    : "bg-panel-inset text-muted")
              }
              title="Grade restates the confidence percentage — it is not a second rating"
            >
              {grade}
            </span>
          ) : null}
        </div>
      </header>

      <div className="flex flex-col gap-2">
        {card.markets.map((row) => (
          <MarketRow
            key={row.projectionId}
            row={row}
            hitRate={summarise(row.line, row.side, marketsByKey.get(row.marketKey), gameLog, hitRateWindow)}
            hitRateWindow={hitRateWindow}
            edgeThreshold={edgeThreshold}
          />
        ))}
      </div>
    </article>
  );
}

/**
 * Grade this player's past games against the line now showing.
 *
 * Returns null when there is nothing to grade — no line, or a market whose stat
 * column is not one the game log carries. Null renders as an explicit "no line
 * to grade against" rather than as a zero, which would read as "never hits".
 */
function summarise(
  line: number | null,
  side: "over" | "under" | null,
  market: Market | undefined,
  gameLog: PlayerGameLogRow[],
  window: number,
): HitRateSummary | null {
  if (line === null || side === null || !market) return null;

  // Binary markets grade on the OVER side whatever the call was, so a green dot
  // means the player scored. Grading on the call would paint five green dots
  // for a player who has not scored all season — accurate against "under 0.5"
  // and the opposite of what a reader takes from it.
  const gradeSide = market.isBinary ? "over" : side;

  const graded = gradeGames(gameLog, market.statColumn, line, gradeSide);
  if (graded.length === 0) return null;
  return hitRate(graded, window);
}
