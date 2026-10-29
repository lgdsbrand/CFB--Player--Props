import Link from "next/link";

import { TeamChip } from "@/components/board/team-chip";
import { ShareButton } from "@/components/share/share-button";
import { favourite, softestMatchup, type PositionMatchup } from "@/lib/core/game-view";
import { formatCount, formatKickoff, formatVenue } from "@/lib/core/format";
import { gameScoreTier, type GamePower } from "@/lib/core/power";
import { shareGameFileName, shareGameTitle } from "@/lib/core/share-game";
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
 * The spread and total are the BOOK'S numbers, from CFBD. The one model number
 * is the projected score beside each team (client, 2026-10-09): the game
 * model's for college (CLAUDE.md §11), the fantasy model's for the NFL
 * (2026-10-19). The page picks which; the edges stay on the Lines view.
 */
export function GameCard({
  game,
  matchups,
  conditions = null,
  score = null,
  power = null,
}: {
  game: GameSummary;
  matchups: PositionMatchup[];
  conditions?: GameConditions | null;
  score?: { home: number; away: number } | null;
  /** Power ranks and game score at this week (client, 2026-10-29). */
  power?: GamePower | null;
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

  // The share button sits BESIDE the card's link, not inside it: a button in
  // an <a> is invalid HTML, and its preview dialog's taps would follow the link.
  return (
    // `min-w-0` on both: as a grid item the wrapper would otherwise grow to its
    // content's width and push the page 14px wider than a 390px phone.
    <div className="relative flex min-w-0">
      <Link
        href={`/games/${game.gameId}`}
        className="panel hover:border-border-strong flex min-w-0 flex-1 flex-col gap-3 p-4 transition-colors"
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
          {score ? (
            <span className="label-caption -mb-1 self-end">Proj. score</span>
          ) : null}
          <TeamLine
            rank={game.awayPollRank}
            abbreviation={game.awayAbbreviation}
            school={game.awaySchool}
            color={game.awayColor}
            altColor={game.awayAltColor}
            favoured={line?.isHome === false}
            projected={score?.away ?? null}
            powerRank={power?.awayRank ?? null}
          />
          <TeamLine
            rank={game.homePollRank}
            abbreviation={game.homeAbbreviation}
            school={game.homeSchool}
            color={game.homeColor}
            altColor={game.homeAltColor}
            favoured={line?.isHome === true}
            projected={score?.home ?? null}
            powerRank={power?.homeRank ?? null}
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
          {power ? (
            <Stat label="Game score">
              <span
                title="0-100: two highly ranked teams in a close game score highest (60% both teams' power rank, 40% how close the game is)."
                className={
                  gameScoreTier(power.gameScore) === "hot"
                    ? "text-target"
                    : gameScoreTier(power.gameScore) === "good"
                      ? "text-positive"
                      : undefined
                }
              >
                {power.gameScore}
              </span>
            </Stat>
          ) : null}
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

        {/* Right padding keeps the venue clear of the share button. */}
        <p className="text-dim min-h-6 truncate pr-20 text-[0.6875rem] leading-6">{venue ?? ""}</p>
      </Link>
      <div className="absolute right-3 bottom-3">
        <ShareButton
          compact
          imagePath={`/games/${game.gameId}/share-image`}
          pagePath={`/games/${game.gameId}`}
          fileName={shareGameFileName(game)}
          title={shareGameTitle(game)}
        />
      </div>
    </div>
  );
}

function TeamLine({
  rank,
  abbreviation,
  school,
  color,
  altColor,
  favoured,
  projected,
  powerRank = null,
  atHome = false,
}: {
  rank: number | null;
  powerRank?: number | null;
  abbreviation: string | null;
  school: string;
  color: string | null;
  altColor: string | null;
  favoured: boolean;
  projected: number | null;
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
      {/* Power rank beside the poll rank: the poll covers 25 teams, power all
          of them (worker/core/power.py). */}
      {powerRank !== null ? (
        <span
          className="label-caption text-muted shrink-0 tabular-nums"
          title="Power rank: points per game better than an average team, adjusted for the schedule."
        >
          Pwr {powerRank}
        </span>
      ) : null}
      {favoured ? (
        <span className="label-caption text-positive shrink-0">Fav</span>
      ) : null}
      {atHome ? <span className="label-caption text-dim shrink-0">Home</span> : null}
      {projected !== null ? (
        <span className="text-ink w-10 shrink-0 text-right text-base font-extrabold tabular-nums">
          {projected.toFixed(1)}
        </span>
      ) : null}
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
