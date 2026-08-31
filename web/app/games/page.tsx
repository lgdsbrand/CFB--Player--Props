import Link from "next/link";

import { DayStrip } from "@/components/day-strip";
import { GameCard } from "@/components/games/game-card";
import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { WeekStrip } from "@/components/week-strip";
import {
  boardHref,
  parseBoardParams,
  type RawParams,
} from "@/lib/core/board-params";
import { offenseOnBoard } from "@/lib/core/board-scope";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount } from "@/lib/core/format";
import { gameMatchups } from "@/lib/core/game-view";
import {
  findSlateDay,
  narrowToDay,
  slateDays,
} from "@/lib/core/slate-days";
import { getConferences } from "@/lib/data/catalogue";
import { getDefenseRatings } from "@/lib/data/defense";
import { getSlateGames } from "@/lib/data/games";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";
import { getTeamDirectory } from "@/lib/data/teams";
import { kickoffCutoff, playedCount, upcomingGames } from "@/lib/core/kickoff";
import { getSlateConditions } from "@/lib/data/weather";

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

/**
 * Where this page lives. Named once because the day strip, the conference
 * pills, the week strip and the empty state all have to agree on it.
 */
const GAMES_PATH = "/games";

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

  const [games, ratings, conditions] = await Promise.all([
    getSlateGames(active.season, active.week),
    // Pinned to the week on screen, never "the latest": a rating from a later
    // cutoff knows results the reader is being asked to look ahead at.
    getDefenseRatings(active.season, active.week),
    getSlateConditions(active.season, active.week),
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
  // AND STILL TO KICK. A game that has been played carries a final score and
  // a settled set of props, so listing it here offers the reader a card whose
  // every number is a result rather than a prediction. See `lib/core/kickoff.ts`
  // for why this is keyed on kickoff and not on `completed`.
  const cutoff = kickoffCutoff();
  const inConference = upcomingGames(games, cutoff).filter(
    (game) =>
      offenseOnBoard(teamDirectory.get(game.homeTeamId), params.conference) ||
      offenseOnBoard(teamDirectory.get(game.awayTeamId), params.conference),
  );
  const played = playedCount(games, cutoff);

  // THE DAYS COME FROM THE GAMES THAT SURVIVED THE CONFERENCE FILTER, not from
  // the whole slate — the one decision in this block that could have gone the
  // other way. Deriving them from the slate would put a count on each pill that
  // the page then contradicts, and would offer days holding nothing a reader
  // filtered to the SEC can see: a control that leads to an empty page, which
  // this product has already decided twice not to ship. It also takes the strip
  // off screen when a conference narrows the week to one day, which is exactly
  // the point at which it can no longer change anything.
  //
  // The cost is that the pills move when the conference changes. That is
  // correct: they describe the slate being looked at, not the calendar.
  const days = slateDays(inConference);
  const activeDay = findSlateDay(days, params.day);

  // Season and week come from the RESOLVED week, not the URL — an unknown week
  // falls back to the latest, and every link on this page has to carry the week
  // actually on screen or it would send the reader somewhere else. `day` is
  // likewise the resolved value, so a stale day in a shared link corrects
  // itself in the strip instead of leaving a pill lit that filters nothing.
  const linkParams = {
    ...params,
    season: active.season,
    week: active.week,
    day: activeDay?.key,
  };

  const shown = narrowToDay(inConference, activeDay);

  const conferenceLabel = params.conference ?? "displayed conferences";

  // Two independent narrowings, so the sentence is composed rather than
  // branched: either can be absent and the other still has to read as English.
  const narrowings = [
    activeDay ? `on ${activeDay.label}` : undefined,
    // Keyed off the CONFERENCE step, not off `shown`, because with a day
    // selected `shown` is always smaller than the slate — which would print
    // "those with a team in the displayed conferences" on a page that had not
    // dropped a single game for that reason.
    inConference.length === games.length
      ? undefined
      : `with a team in the ${conferenceLabel}`,
  ].filter((clause): clause is string => clause !== undefined);

  return (
    <Shell>
      <div className="flex flex-col gap-1">
        <span className="label-caption">Legends Sports · College Football</span>
        <h1 className="text-2xl font-extrabold tracking-tight">Analyze Games</h1>
      </div>

      <WeekStrip weeks={weeks} active={active} basePath={GAMES_PATH} />

      <DayStrip
        days={days}
        activeDay={activeDay?.key}
        params={linkParams}
        basePath={GAMES_PATH}
      />

      <div className="flex flex-wrap items-center gap-2">
        <ConferencePill
          href={boardHref(linkParams, { conference: undefined }, GAMES_PATH)}
          active={params.conference === undefined}
          label="All displayed"
        />
        {conferences.map((conference) => (
          <ConferencePill
            key={conference.id}
            // Built through `boardHref` rather than by hand so the day survives
            // a conference change. The hand-built version carried the season
            // and week and nothing else, so adding a second filter above it
            // would have silently cleared the first on every click.
            href={boardHref(
              linkParams,
              { conference: conference.name },
              GAMES_PATH,
            )}
            active={params.conference === conference.name}
            label={conference.abbreviation ?? conference.name}
          />
        ))}
      </div>

      {played > 0 ? (
        <p className="text-dim border-border-subtle rounded-xl border px-3 py-2 text-xs">
          <span className="text-muted font-bold uppercase tracking-label">
            Already played
          </span>{" "}
          — {formatCount(played)}{" "}
          {played === 1 ? "game has" : "games have"} kicked off this week and{" "}
          {played === 1 ? "is" : "are"}{" "}
          {/* Explicit, not a literal space. A space that follows an expression
              OPENING a line is dropped by the JSX transform — the same defect
              that once rendered "confidenceis" on the home page. */}
          no longer listed. Those props are settled, so they are results rather
          than plays; a player&rsquo;s own page still shows how each one
          finished.
        </p>
      ) : null}

      <p className="text-muted text-xs">
        {/* Denominator is the games STILL TO KICK, not the whole week. Against
            the full slate this line would read "6 of 99" while the strip beside
            it offered only the days those 6 fall on, which reads as an
            off-by-one rather than as a filter. */}
        {formatCount(shown.length)} of {formatCount(games.length - played)}{" "}
        games —{" "}
        {narrowings.length === 0
          ? "the whole slate"
          : `those ${narrowings.join(" ")}`}
        . Spreads and totals are the book&rsquo;s consensus, not a model output.
      </p>

      {shown.length === 0 ? (
        <div className="panel p-6">
          <h2 className="section-header mb-2">No games here</h2>
          <p className="text-muted max-w-prose text-sm">
            No game this week has a team in the {conferenceLabel}.{" "}
            <Link
              href={boardHref(linkParams, { conference: undefined }, GAMES_PATH)}
              className="text-accent-cyan hover:underline"
            >
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
              conditions={conditions.get(game.gameId) ?? null}
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
}: {
  href: string;
  active: boolean;
  label: string;
}) {
  // The href arrives complete. It used to be assembled here, appending the
  // season and week to a path the caller had half-built, which worked only for
  // as long as those were the only two things a conference link had to carry.
  return (
    <Link
      href={href}
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
