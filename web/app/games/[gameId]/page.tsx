import Link from "next/link";
import { notFound } from "next/navigation";

import { TeamChip } from "@/components/board/team-chip";
import { GameLive } from "@/components/live/game-live";
import { BookOddsPanel } from "@/components/games/book-odds-panel";
import { CoachPanel } from "@/components/games/coach-panel";
import { EvWagersPanel } from "@/components/games/ev-wagers-panel";
import { MatchupGrid } from "@/components/games/matchup-grid";
import { ModelLinePanel } from "@/components/games/model-line-panel";
import { RecordsPanel } from "@/components/games/records-panel";
import { SharpRetailPanel } from "@/components/games/sharp-retail-panel";
import { StartersPanel } from "@/components/games/starters-panel";
import { TeamComparisonPanel } from "@/components/games/team-comparison-panel";
import { WeatherPanel } from "@/components/games/weather-panel";
import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import {
  boardHref,
  gamesHref,
  parseBoardParams,
  type RawParams,
} from "@/lib/core/board-params";
import { DEFAULT_SPORT, type Sport } from "@/lib/core/sport";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount, formatKickoff, formatVenue } from "@/lib/core/format";
import { currentCoach, summarizeCoach } from "@/lib/core/coach";
import { favourite, gameMatchups } from "@/lib/core/game-view";
import { hasKickedOff } from "@/lib/core/kickoff";
import { LIVE_SPORTS } from "@/lib/core/live";
import { rankStrength } from "@/lib/core/team-strength";
import { getCoaches } from "@/lib/data/coaches";
import { getTeamStrength } from "@/lib/data/team-strength";
import { getAppConfig } from "@/lib/data/config";
import { getDefenseRatings } from "@/lib/data/defense";
import { getEvWagers } from "@/lib/data/ev";
import { getLiveScores } from "@/lib/data/live";
import {
  getEarliestSeason,
  getGameBookOdds,
  getGameOddsSummaries,
  getGameProjections,
  getHeadToHead,
  getSeasonResults,
} from "@/lib/data/game-odds";
import { getGame } from "@/lib/data/games";
import { getStartersAndInjuries } from "@/lib/data/starters";
import { getGameConditions } from "@/lib/data/weather";

/**
 * One game: the line, the books' prices, the records, the conditions and the
 * position matchups. The PROPS ARE NOT HERE.
 *
 * THEY USED TO BE, as one table per team, and the client asked for them off
 * the page (2026-10-06): the board already lists a game's props with every
 * filter, sort and card the tables lacked, so the tables were a second, weaker
 * copy of it at the bottom of a page about the game. The page now leads with a
 * button to that game on the board instead, and dropping the tables also
 * dropped this page's largest read (up to 1,000 board rows).
 *
 * The header still counts the game's props and calls, from `v_slate_games`,
 * so a reader knows what is behind the button before pressing it.
 */

export default async function GamePage({
  params,
  searchParams,
}: {
  params: Promise<{ gameId: string }>;
  searchParams: Promise<RawParams>;
}) {
  if (!isSupabaseConfigured()) {
    return (
      <Shell>
        <NotConfigured />
      </Shell>
    );
  }

  const { gameId: rawId } = await params;
  const gameId = Number.parseInt(rawId, 10);
  if (!Number.isFinite(gameId)) notFound();

  const game = await getGame(gameId);
  if (!game) notFound();

  const raw = await searchParams;
  const boardParams = parseBoardParams(raw, { edgesOnlyDefault: false });

  const [
    ratings,
    conditions,
    bookOdds,
    seasonGames,
    meetings,
    earliestSeason,
    projections,
    summaries,
    config,
    strengthRows,
    coaches,
    roster,
    evWagers,
    liveRows,
  ] = await Promise.all([
    getDefenseRatings(game.season, game.week, game.sport),
    // Joins the existing wave rather than forming its own. A wave costs one
    // round trip whatever its width, so this read is effectively free here and
    // would cost a full one as a fifth wait.
    getGameConditions(gameId),
    getGameBookOdds(gameId),
    getSeasonResults(game.season, [game.homeTeamId, game.awayTeamId], game.startDate),
    getHeadToHead(game.homeTeamId, game.awayTeamId, game.startDate),
    getEarliestSeason(game.sport),
    getGameProjections([gameId]),
    getGameOddsSummaries([gameId]),
    // Cached; only for the edge threshold the model panel highlights at.
    getAppConfig(),
    // Entering this game's week, so nothing after kickoff (migration 0077).
    getTeamStrength(game.season, game.week, game.sport),
    getCoaches(game.season, [game.homeTeamId, game.awayTeamId]),
    // This game's week, as it stood going into the game (migration 0086).
    getStartersAndInjuries(game.season, game.week, [game.homeTeamId, game.awayTeamId]),
    getEvWagers([gameId]),
    LIVE_SPORTS.includes(game.sport) && !game.completed ? getLiveScores([gameId]) : [],
  ]);

  // +EV rows are the last capture's prices: an offer before kickoff, history
  // after it, so the panel is shown only while the game is still to play.
  const notStarted = !game.completed && !hasKickedOff(game.startDate);

  const strength = rankStrength(strengthRows, [game.homeTeamId, game.awayTeamId]);
  const coachFor = (teamId: number) => {
    const coach = currentCoach(coaches.byTeam.get(teamId) ?? []);
    return coach ? summarizeCoach(coach, coaches.seasons, teamId, game.season) : null;
  };

  const matchups = gameMatchups(game, ratings);
  const line = favourite(game);
  const venue = formatVenue({
    name: game.venueName,
    city: game.venueCity,
    state: game.venueState,
  });

  const boardLink = boardHref(boardParams, {
    season: game.season,
    week: game.week,
    // FROM THE GAME, NOT FROM `boardParams`. The index card links here as a
    // bare `/games/<id>`, so `?sport=` is usually absent and
    // `parseBoardParams` has already defaulted it to college — which would
    // send a reader looking at an NFL game to the college board.
    sport: game.sport,
    games: [gameId],
    conference: undefined,
  });

  return (
    <Shell sport={game.sport}>
      <Link
        href={gamesHref(game)}
        className="text-muted hover:text-ink w-fit text-xs font-semibold transition-colors"
      >
        ← All games
      </Link>

      <header className="panel flex flex-col gap-4 p-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="label-caption">
            {game.season} · Week {game.week}
          </span>
          <span className="text-muted text-xs">{formatKickoff(game.startDate)}</span>
          {game.neutralSite ? (
            <span className="pill bg-panel-inset text-muted">Neutral site</span>
          ) : null}
        </div>

        <div className="flex flex-col gap-2">
          <TeamHeading
            rank={game.awayPollRank}
            abbreviation={game.awayAbbreviation}
            school={game.awaySchool}
            color={game.awayColor}
            altColor={game.awayAltColor}
            points={game.completed ? game.awayPoints : null}
          />
          <TeamHeading
            rank={game.homePollRank}
            abbreviation={game.homeAbbreviation}
            school={game.homeSchool}
            color={game.homeColor}
            altColor={game.homeAltColor}
            points={game.completed ? game.homePoints : null}
            atHome={!game.neutralSite}
          />
        </div>

        {/* Live state until `games` has the final, which the headings print. */}
        {LIVE_SPORTS.includes(game.sport) && !game.completed ? (
          <GameLive
            gameId={gameId}
            startDate={game.startDate}
            home={game.homeAbbreviation ?? game.homeSchool}
            away={game.awayAbbreviation ?? game.awaySchool}
            initial={liveRows[0] ?? null}
          />
        ) : null}

        <div className="border-border-subtle flex flex-wrap items-baseline gap-x-6 gap-y-2 border-t pt-3">
          <Stat label="Spread">
            {line
              ? line.points === 0
                ? "Pick'em"
                : `${line.abbreviation ?? line.school} -${line.points.toFixed(1)}`
              : "Not priced"}
          </Stat>
          <Stat label="Total">
            {game.gameTotal !== null ? game.gameTotal.toFixed(1) : "—"}
          </Stat>
          <Stat label="Books">
            {game.gameLineProviders !== null && game.gameLineProviders > 0
              ? formatCount(game.gameLineProviders)
              : "—"}
          </Stat>
          <Stat label="Props">{formatCount(game.projections)}</Stat>
          <Stat label="With a call">{formatCount(game.calls)}</Stat>
        </div>

        {venue ? <p className="text-dim text-xs">{venue}</p> : null}

        {/* A game with no props gets no button. A link that opens an empty
            board reads as a broken board, not as "nothing here yet". */}
        {game.projections > 0 ? (
          <Link href={boardLink} className="cta w-fit px-3 py-2">
            See this game&rsquo;s props on the board →
          </Link>
        ) : (
          <p className="text-muted text-xs">
            No props for this game yet.
          </p>
        )}

        {/* Stated on the page, not just in a comment: whose numbers these
            are. These are CFBD's consensus; the game model's numbers and its
            edge against one book (CLAUDE.md §11) are in their own panel
            below, so a reader never has to guess which line is whose. */}
        <p className="text-dim text-[0.6875rem]">
          The spread and total are the median across{" "}
          {game.gameLineProviders ?? 0} sportsbook
          {game.gameLineProviders === 1 ? "" : "s"}, ingested from
          CollegeFootballData. They are the market&rsquo;s numbers. The game
          model&rsquo;s fair line and edge are in its panel below.
        </p>
      </header>

      {/* Above the matchups on purpose. Conditions are one glance and they
          frame everything below — a 20 mph crosswind is the reason a passing
          matchup that looks soft may not play soft. The position table is the
          deep-dive and reads slower, so it follows. */}
      {/* The game model's panels (CLAUDE.md §11). Its fair line first,
          labelled and with no call (G4), then the market's prices by book. */}
      <ModelLinePanel
        projection={projections.get(gameId) ?? null}
        home={game.homeAbbreviation ?? game.homeSchool}
        away={game.awayAbbreviation ?? game.awaySchool}
        completed={game.completed}
        edgeThreshold={config.edgeThreshold}
      />

      <SharpRetailPanel
        summary={summaries.get(gameId)}
        home={game.homeAbbreviation ?? game.homeSchool}
        away={game.awayAbbreviation ?? game.awaySchool}
      />

      {notStarted && summaries.has(gameId) ? (
        <EvWagersPanel
          wagers={evWagers}
          home={game.homeAbbreviation ?? game.homeSchool}
          away={game.awayAbbreviation ?? game.awaySchool}
        />
      ) : null}

      {(["full", "h1", "q1"] as const).map((period) => (
        <BookOddsPanel
          key={period}
          period={period}
          odds={bookOdds}
          home={game.homeAbbreviation ?? game.homeSchool}
          away={game.awayAbbreviation ?? game.awaySchool}
        />
      ))}

      <RecordsPanel
        season={game.season}
        away={{ teamId: game.awayTeamId, label: game.awayAbbreviation ?? game.awaySchool }}
        home={{ teamId: game.homeTeamId, label: game.homeAbbreviation ?? game.homeSchool }}
        seasonGames={seasonGames}
        meetings={meetings}
        earliestSeason={earliestSeason}
      />

      {strengthRows.length > 0 ? (
        <TeamComparisonPanel
          away={game.awayAbbreviation ?? game.awaySchool}
          home={game.homeAbbreviation ?? game.homeSchool}
          awayStrength={strength.get(game.awayTeamId)}
          homeStrength={strength.get(game.homeTeamId)}
          week={game.week}
          league={game.sport === "nfl" ? "NFL teams" : "FBS teams"}
        />
      ) : null}

      {roster.depth.length > 0 || roster.injuries.length > 0 ? (
        <StartersPanel
          sport={game.sport}
          season={game.season}
          week={game.week}
          away={{ teamId: game.awayTeamId, label: game.awaySchool }}
          home={{ teamId: game.homeTeamId, label: game.homeSchool }}
          depth={roster.depth}
          injuries={roster.injuries}
        />
      ) : null}

      <CoachPanel
        season={game.season}
        away={{ school: game.awaySchool, coach: coachFor(game.awayTeamId) }}
        home={{ school: game.homeSchool, coach: coachFor(game.homeTeamId) }}
      />

      <WeatherPanel conditions={conditions} />

      <MatchupGrid matchups={matchups} />

    </Shell>
  );
}

function TeamHeading({
  rank,
  abbreviation,
  school,
  color,
  altColor,
  points,
  atHome = false,
}: {
  rank: number | null;
  abbreviation: string | null;
  school: string;
  color: string | null;
  altColor: string | null;
  points: number | null;
  atHome?: boolean;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <TeamChip abbreviation={abbreviation} color={color} altColor={altColor} />
      <span className="text-ink min-w-0 flex-1 truncate text-lg font-extrabold">
        {rank !== null ? (
          <span className="text-accent-cyan mr-1.5 text-sm font-extrabold tabular-nums">
            #{rank}
          </span>
        ) : null}
        {school}
      </span>
      {atHome ? <span className="label-caption shrink-0">Home</span> : null}
      {points !== null ? (
        <span className="text-ink shrink-0 text-lg font-extrabold tabular-nums">
          {points}
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

function Shell({
  children,
  sport = DEFAULT_SPORT,
}: {
  children: React.ReactNode;
  sport?: Sport;
}) {
  return (
    <>
      <SiteHeader activeHref="/games" sport={sport} />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}
