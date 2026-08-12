import Link from "next/link";
import { notFound } from "next/navigation";

import { TeamChip } from "@/components/board/team-chip";
import { MatchupGrid } from "@/components/games/matchup-grid";
import { PropsTable } from "@/components/games/props-table";
import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { boardHref, parseBoardParams, type RawParams } from "@/lib/core/board-params";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount, formatKickoff, formatVenue } from "@/lib/core/format";
import { favourite, gameMatchups, groupPropsByTeam } from "@/lib/core/game-view";
import { getBoardRows } from "@/lib/data/board";
import { getMarkets } from "@/lib/data/catalogue";
import { getAppConfig } from "@/lib/data/config";
import { getDefenseRatings } from "@/lib/data/defense";
import { getGame } from "@/lib/data/games";

/**
 * One game: the line, the position matchups, and every prop on both teams.
 *
 * NO NEW DATABASE OBJECT BEHIND THIS PAGE. `v_board_rows` already carries the
 * spread, the total, both poll ranks and the venue on every row, because the
 * board card needed them — so a game view is a regrouping of rows that already
 * exist rather than a new read path. The index needed `v_slate_games` only
 * because counting rows per game is an aggregate.
 *
 * THE PROPS ARE UNFILTERED, unlike the board. The board defaults to the
 * displayed conferences because it is a list someone scans; this page is
 * already scoped to one game the reader chose, and hiding half of it because
 * the visiting team is Group of Five would be answering a question nobody
 * asked. The INDEX applies the conference scope, so the reader never arrives
 * here from a link that leads nowhere.
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

  const [config, markets, ratings, page] = await Promise.all([
    getAppConfig(),
    // Only for the display order of a player's markets. Cached seed data, so
    // this costs a map lookup rather than a round trip on most requests.
    getMarkets(),
    getDefenseRatings(game.season, game.week),
    // Every row for this game across both teams: ~140 on the largest game so
    // far, comfortably inside PostgREST's cap. `total` is checked below rather
    // than assumed, because a truncated read is indistinguishable from a short
    // one and the failure would be a team quietly missing players.
    getBoardRows({
      season: game.season,
      week: game.week,
      gameId,
      displayedConferencesOnly: false,
      limit: 1000,
      sort: "confidence",
    }),
  ]);

  const truncated = page.total > page.rows.length;
  const matchups = gameMatchups(game, ratings);
  const teams = groupPropsByTeam(game, page.rows, {
    marketOrder: new Map(markets.map((market) => [market.key, market.sortOrder])),
  });
  const line = favourite(game);
  const venue = formatVenue({
    name: game.venueName,
    city: game.venueCity,
    state: game.venueState,
  });

  const colorsFor = (teamId: number) =>
    teamId === game.homeTeamId
      ? { color: game.homeColor, altColor: game.homeAltColor }
      : { color: game.awayColor, altColor: game.awayAltColor };

  return (
    <Shell>
      <Link
        href={`/games?season=${game.season}&week=${game.week}`}
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

        {/* Stated on the page, not just in a comment. The client asked for a
            game prediction model and it is out of scope (CLAUDE.md §10); the
            honest version of that is saying whose numbers these are. */}
        <p className="text-dim text-[0.6875rem]">
          The spread and total are the median across{" "}
          {game.gameLineProviders ?? 0} sportsbook
          {game.gameLineProviders === 1 ? "" : "s"}, ingested from
          CollegeFootballData. They are the market&rsquo;s numbers — this tool
          does not model game outcomes.
        </p>
      </header>

      <MatchupGrid matchups={matchups} />

      {truncated ? (
        <p className="border-negative/40 bg-negative/5 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-negative font-bold uppercase tracking-label">
            Partial game
          </span>{" "}
          — this game has {formatCount(page.total)} rows and only{" "}
          {formatCount(page.rows.length)} were read, so some players are missing.
        </p>
      ) : null}

      {teams.map((team) => (
        <PropsTable
          key={team.teamId}
          team={team}
          colors={colorsFor(team.teamId)}
          edgeThreshold={config.edgeThreshold}
        />
      ))}

      <Link
        href={boardHref(boardParams, {
          season: game.season,
          week: game.week,
          game: gameId,
          conference: undefined,
        })}
        className="cta w-fit px-3 py-2"
      >
        Open this game on the board →
      </Link>
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

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader activeHref="/games" />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}
