import Link from "next/link";

import { TeamChip } from "@/components/board/team-chip";
import { favourite, softestMatchup, type PositionMatchup } from "@/lib/core/game-view";
import { formatCount, formatKickoff, formatVenue } from "@/lib/core/format";
import type { GameConditions, GameSummary } from "@/lib/core/types";
import { conditionsSummary } from "@/lib/core/weather-view";

/**
 * One game on the Analyze Games index.
 *
 * WHAT IT LEADS WITH IS THE POINT. A game card could show the spread, the
 * total, both records, both ranks and the prop count — and then a reader
 * scanning sixty of them takes in none of it. The hierarchy here is: who is
 * playing, what the book thinks, and the one matchup worth opening the game
 * for. Everything else is a caption.
 *
 * The spread and total are the BOOK'S numbers, from CFBD. Nothing on this card
 * is a model opinion about the game itself; CLAUDE.md §10 keeps game-outcome
 * prediction out of scope.
 */
export function GameCard({
  game,
  matchups,
  conditions = null,
}: {
  game: GameSummary;
  matchups: PositionMatchup[];
  conditions?: GameConditions | null;
}) {
  const line = favourite(game);
  const softest = softestMatchup(matchups);
  const venue = formatVenue({
    name: game.venueName,
    city: game.venueCity,
    state: game.venueState,
  });
  // Null on most cards before the forecast horizon, and that is the design:
  // sixty repetitions of "no forecast yet" would read as sixty faults. See
  // `conditionsSummary`.
  const weather = conditionsSummary(conditions);

  return (
    <Link
      href={`/games/${game.gameId}`}
      className="panel hover:border-border-strong flex flex-col gap-3 p-4 transition-colors"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="label-caption min-w-0 truncate">
          {formatKickoff(game.startDate)}
        </span>
        <span className="flex shrink-0 items-baseline gap-2">
          {game.neutralSite ? (
            <span className="label-caption text-dim">Neutral</span>
          ) : null}
          {weather ? (
            <span
              className={
                "text-[0.6875rem] font-semibold tabular-nums " +
                (weather.notable ? "text-target" : "text-muted")
              }
            >
              {weather.text}
            </span>
          ) : null}
        </span>
      </div>

      <div className="flex flex-col gap-1.5">
        <TeamLine
          rank={game.awayPollRank}
          abbreviation={game.awayAbbreviation}
          school={game.awaySchool}
          color={game.awayColor}
          altColor={game.awayAltColor}
          favoured={line?.isHome === false}
        />
        <TeamLine
          rank={game.homePollRank}
          abbreviation={game.homeAbbreviation}
          school={game.homeSchool}
          color={game.homeColor}
          altColor={game.homeAltColor}
          favoured={line?.isHome === true}
          atHome
        />
      </div>

      <div className="border-border-subtle flex flex-wrap items-baseline gap-x-4 gap-y-1 border-t pt-3">
        <Stat label="Line">
          {line ? (
            <>
              {line.abbreviation ?? line.school}{" "}
              {line.points === 0 ? "PK" : `-${line.points.toFixed(1)}`}
            </>
          ) : (
            <span className="text-dim font-normal">Not priced</span>
          )}
        </Stat>
        <Stat label="Total">
          {game.gameTotal !== null ? (
            game.gameTotal.toFixed(1)
          ) : (
            <span className="text-dim font-normal">—</span>
          )}
        </Stat>
        <Stat label="Props">
          {game.projections > 0 ? (
            formatCount(game.projections)
          ) : (
            <span className="text-dim font-normal">None yet</span>
          )}
        </Stat>
      </div>

      {softest ? (
        <p className="text-muted text-xs">
          <span className="text-target font-bold">Softest:</span>{" "}
          {softest.side.offenseAbbreviation ?? "—"} {softest.position}s draw a
          defense ranked{" "}
          <span className="text-ink font-bold tabular-nums">
            {softest.side.rank} of {softest.rankedDefenses}
          </span>{" "}
          against the position, where 1 is the best.
        </p>
      ) : (
        <p className="text-dim text-xs">
          No defense here is rated yet — ratings need a completed game behind the
          cutoff.
        </p>
      )}

      {venue ? <p className="text-dim truncate text-[0.6875rem]">{venue}</p> : null}
    </Link>
  );
}

function TeamLine({
  rank,
  abbreviation,
  school,
  color,
  altColor,
  favoured,
  atHome = false,
}: {
  rank: number | null;
  abbreviation: string | null;
  school: string;
  color: string | null;
  altColor: string | null;
  favoured: boolean;
  atHome?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <TeamChip abbreviation={abbreviation} color={color} altColor={altColor} />
      <span className="text-ink min-w-0 flex-1 truncate text-sm font-bold">
        {rank !== null ? (
          <span className="text-accent-cyan mr-1 text-xs font-extrabold tabular-nums">
            #{rank}
          </span>
        ) : null}
        {school}
      </span>
      {/* The favourite is marked rather than the spread being repeated on the
          row: the number is already in the Line stat below, and a second copy
          per team invites the reader to work out which perspective it is on. */}
      {favoured ? (
        <span className="label-caption text-positive shrink-0">Fav</span>
      ) : null}
      {atHome ? <span className="label-caption text-dim shrink-0">Home</span> : null}
    </div>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="label-caption">{label}</span>
      <span className="text-ink text-sm font-bold tabular-nums">{children}</span>
    </span>
  );
}
