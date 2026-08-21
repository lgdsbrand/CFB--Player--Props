import Link from "next/link";

import { EvidencePill } from "@/components/board/evidence-pill";
import { MarketRow } from "@/components/board/market-row";
import { TeamChip } from "@/components/board/team-chip";
import { rankBasis } from "@/lib/core/defense-view";
import { formatGameLine, formatKickoff, formatVenue } from "@/lib/core/format";
import { playerHref } from "@/lib/core/player-params";
import { gradeFor, gradeToneToken } from "@/lib/core/grade";
import { summariseRow } from "@/lib/core/board-view";
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
  const venue = formatVenue({
    name: card.venueName,
    city: card.venueCity,
    state: card.venueState,
  });
  const gameLine = formatGameLine(card.teamSpread, card.gameTotal);

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

          {/*
            Venue on its own line, below the matchup rather than appended to it.
            The row above wraps at card width, and a stadium name averaging 20
            characters pushed the kickoff onto a second line on most cards —
            which put the time further from the teams than the venue, inverting
            what a reader needs first.

            `truncate` with the full string in `title`: the longest venue on
            record is 55 characters before the city is added, and the card is
            one of three across at xl.
          */}
          {venue ? (
            <p className="text-dim truncate text-[0.6875rem]" title={venue}>
              {venue}
            </p>
          ) : null}

          {/*
            The game line, on its own short line rather than appended to the
            venue. The venue line is `truncate`, so anything added to it is
            what gets cut first at 390px — and the spread is the half a reader
            would rather keep.

            Not labelled "SPREAD"/"TOTAL": a signed number beside a team and an
            "O/U" prefix are already unambiguous to this audience, and two more
            uppercase captions on a card that already carries four would cost
            more room than they earn.
          */}
          {gameLine ? (
            <p
              className="text-dim text-[0.6875rem] tabular-nums"
              title="Game spread from this player's team, and the game total. Context for the prop, not a model output."
            >
              {gameLine}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <EvidencePill
            priorWeight={card.priorWeight}
            effectiveSample={card.effectiveSample}
          />
          {card.opponentRankVsPosition !== null ? (
            <span
              className="pill bg-panel-inset text-muted"
              title={
                "Opponent rank vs this position — 1 is the best defense, so a HIGH " +
                "number is the softer matchup." +
                (card.positionGroup
                  ? ` Ranked on ${rankBasis(card.positionGroup).label}.`
                  : "")
              }
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
            hitRate={summariseRow(
              row,
              marketsByKey.get(row.marketKey),
              gameLog,
              hitRateWindow,
            )}
            hitRateWindow={hitRateWindow}
            edgeThreshold={edgeThreshold}
          />
        ))}
      </div>
    </article>
  );
}
