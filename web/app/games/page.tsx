import Link from "next/link";

import { GameCard } from "@/components/games/game-card";
import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { WeekStrip } from "@/components/week-strip";
import { parseBoardParams, type RawParams } from "@/lib/core/board-params";
import { offenseOnBoard } from "@/lib/core/board-scope";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount } from "@/lib/core/format";
import { gameMatchups } from "@/lib/core/game-view";
import { getConferences } from "@/lib/data/catalogue";
import { getDefenseRatings } from "@/lib/data/defense";
import { getSlateGames } from "@/lib/data/games";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";
import { getTeamDirectory } from "@/lib/data/teams";

/**
 * Analyze Games — the week's slate, game first.
 *
 * The board answers "which prop should I look at". This answers "what is going
 * on in this game", which is the question a reader actually arrives with, and
 * it is built entirely from output that already exists: the schedule, the
 * consensus spread and total from CFBD, the poll ranks, and the position splits
 * the model already consumes.
 *
 * IT IS NOT A GAME PREDICTION MODEL. CLAUDE.md §10 puts full game-outcome and
 * spread prediction out of scope, and that was reaffirmed with the client when
 * this view was agreed: it is a view over existing outputs. The only numbers
 * here describing the game itself are the book's.
 */

export default async function Games({
  searchParams,
}: {
  searchParams: Promise<RawParams>;
}) {
  if (!isSupabaseConfigured()) {
    return (
      <Shell>
        <NotConfigured />
      </Shell>
    );
  }

  const raw = await searchParams;
  const [weeks, conferences] = await Promise.all([
    getSlateWeeks(),
    getConferences(),
  ]);

  // The board's parser, not a second one. Season, week and conference mean the
  // same thing on both pages and are validated the same way; a parallel parser
  // is how two surfaces come to disagree about what `conference=SEC` selects.
  const params = parseBoardParams(raw, { edgesOnlyDefault: false });
  const active = findWeek(weeks, params.season, params.week);

  if (!active) {
    return (
      <Shell>
        <EmptySlate />
      </Shell>
    );
  }

  const [games, ratings] = await Promise.all([
    getSlateGames(active.season, active.week),
    // Pinned to the week on screen, never "the latest": a rating from a later
    // cutoff knows results the reader is being asked to look ahead at.
    getDefenseRatings(active.season, active.week),
  ]);

  const teamDirectory = await getTeamDirectory(
    active.season,
    games.flatMap((game) => [game.homeTeamId, game.awayTeamId]),
  );

  // SAME SCOPE AS THE BOARD, for the reason recorded in `board-scope.ts`: a
  // slate carries plenty of games with nobody in a displayed conference, and a
  // card that leads to an empty board is worse than no card, because the reader
  // concludes the DATA is missing rather than the filter. Either side
  // qualifying is enough — a game with one displayed team still shows that
  // team's players.
  const shown = games.filter(
    (game) =>
      offenseOnBoard(teamDirectory.get(game.homeTeamId), params.conference) ||
      offenseOnBoard(teamDirectory.get(game.awayTeamId), params.conference),
  );

  const conferenceLabel = params.conference ?? "displayed conferences";

  return (
    <Shell>
      <div className="flex flex-col gap-1">
        <span className="label-caption">Legends Sports · College Football</span>
        <h1 className="text-2xl font-extrabold tracking-tight">Analyze Games</h1>
      </div>

      <WeekStrip weeks={weeks} active={active} basePath="/games" />

      <div className="flex flex-wrap items-center gap-2">
        <ConferencePill
          href="/games"
          active={params.conference === undefined}
          label="All displayed"
          season={active.season}
          week={active.week}
        />
        {conferences.map((conference) => (
          <ConferencePill
            key={conference.id}
            href={`/games?conference=${encodeURIComponent(conference.name)}`}
            active={params.conference === conference.name}
            label={conference.abbreviation ?? conference.name}
            season={active.season}
            week={active.week}
          />
        ))}
      </div>

      <p className="text-muted text-xs">
        {formatCount(shown.length)} of {formatCount(games.length)} games —{" "}
        {shown.length === games.length
          ? "the whole slate"
          : `those with a team in the ${conferenceLabel}`}
        . Spreads and totals are the book&rsquo;s consensus, not a model output.
      </p>

      {shown.length === 0 ? (
        <div className="panel p-6">
          <h2 className="section-header mb-2">No games here</h2>
          <p className="text-muted max-w-prose text-sm">
            No game this week has a team in the {conferenceLabel}.{" "}
            <Link href="/games" className="text-accent-cyan hover:underline">
              Clear the conference filter
            </Link>{" "}
            to see the rest of the slate.
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {shown.map((game) => (
            <GameCard
              key={game.gameId}
              game={game}
              matchups={gameMatchups(game, ratings)}
            />
          ))}
        </div>
      )}
    </Shell>
  );
}

function ConferencePill({
  href,
  active,
  label,
  season,
  week,
}: {
  href: string;
  active: boolean;
  label: string;
  season: number;
  week: number;
}) {
  // The week has to travel with the filter or changing conference would drop
  // the reader back onto the default week.
  const separator = href.includes("?") ? "&" : "?";
  return (
    <Link
      href={`${href}${separator}season=${season}&week=${week}`}
      aria-current={active ? "true" : undefined}
      className={
        "rounded-full border px-2.5 py-1 text-[0.6875rem] font-bold uppercase tracking-label transition-colors " +
        (active
          ? "border-accent-cyan/40 bg-accent-cyan/10 text-accent-cyan"
          : "border-border-subtle bg-panel text-muted hover:border-border-strong")
      }
    >
      {label}
    </Link>
  );
}

function EmptySlate() {
  return (
    <div className="panel p-6">
      <h1 className="section-header mb-2">No slate yet</h1>
      <p className="text-muted max-w-prose text-sm">
        No week has model output, so there is nothing to analyze. Run the
        projection job to populate it.
      </p>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader activeHref="/games" />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}
