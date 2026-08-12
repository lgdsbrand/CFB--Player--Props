import Link from "next/link";

import { BoardControls } from "@/components/board/board-controls";
import { PlayerCard } from "@/components/board/player-card";
import { WeeklyTargets } from "@/components/board/weekly-targets";
import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { WeekStrip } from "@/components/week-strip";
import {
  type BoardParams,
  boardHref,
  BOARD_PATH,
  CARDS_PER_PAGE,
  parseBoardParams,
  resetBoardHref,
  type RawParams,
} from "@/lib/core/board-params";
import { offenseOnBoard } from "@/lib/core/board-scope";
import { groupIntoCards, lineCoverage } from "@/lib/core/board-view";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount } from "@/lib/core/format";
import { buildWeeklyTargets } from "@/lib/core/targets";
import {
  getBoardCardKeys,
  getBoardCounts,
  getRowsForCards,
  type BoardCounts,
  type BoardFilters,
} from "@/lib/data/board";
import { getConferences, getMarkets } from "@/lib/data/catalogue";
import { getAppConfig } from "@/lib/data/config";
import { getDefenseRatings } from "@/lib/data/defense";
import { getGameLogsByPlayer } from "@/lib/data/players";
import { findWeek, getSlateGames, getSlateWeeks } from "@/lib/data/slate";
import { getTeamDirectory } from "@/lib/data/teams";

/**
 * The main board (CLAUDE.md §7).
 *
 * One card per player-game, each holding a sub-card per market, leading with
 * the OVER/UNDER call and the confidence percentage. Server-rendered per week
 * with every filter in the URL — see `lib/core/board-params.ts` for why that is
 * a correctness requirement rather than a preference.
 */

export default async function Home({
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
  const [weeks, config, markets, conferences] = await Promise.all([
    getSlateWeeks(),
    getAppConfig(),
    getMarkets(),
    getConferences(),
  ]);

  const params = parseBoardParams(raw, {
    edgesOnlyDefault: config.edgesOnlyDefault,
  });
  const active = findWeek(weeks, params.season, params.week);

  if (!active) {
    return (
      <Shell>
        <div className="panel p-6">
          <h1 className="section-header mb-2">No slate yet</h1>
          <p className="text-muted max-w-prose text-sm">
            No week has model output. Run the projection job to populate the
            board:{" "}
            <code className="font-mono text-xs">
              python -m worker.jobs.run_projections --season 2025 --weeks 10
            </code>
          </p>
        </div>
      </Shell>
    );
  }

  // The strip may have resolved to a different week than the URL asked for
  // (an unknown week falls back to the latest), so filters follow the resolved
  // one — otherwise the board would read one week and label itself another.
  const resolved = { ...params, season: active.season, week: active.week };

  const filters: BoardFilters = {
    season: active.season,
    week: active.week,
    marketKey: resolved.market,
    positionGroup: resolved.position,
    gameId: resolved.game,
    conferenceName: resolved.conference,
    rankedOnly: resolved.rankedOnly,
    search: resolved.search,
    edgesOnly: resolved.edgesOnly,
    edgeThreshold: config.edgeThreshold,
    minConfidence: resolved.minConfidence,
    minOpponentRank: resolved.minOpponentRank,
    sort: resolved.sort,
  };

  const [counts, games, cardKeys, ratings] = await Promise.all([
    getBoardCounts(active.season, active.week, config.edgeThreshold),
    getSlateGames(active.season, active.week),
    getBoardCardKeys(filters),
    // Pinned to as_of_week = the week on screen, never "the latest": a rating
    // from a later cutoff knows results the reader is being asked to predict.
    getDefenseRatings(active.season, active.week),
  ]);

  const totalPages = Math.max(
    Math.ceil(cardKeys.keys.length / CARDS_PER_PAGE),
    1,
  );
  const page = Math.min(resolved.page, totalPages);
  const pageKeys = cardKeys.keys.slice(
    (page - 1) * CARDS_PER_PAGE,
    page * CARDS_PER_PAGE,
  );

  const rows = await getRowsForCards(pageKeys, filters);

  // Card order comes from the key scan, which applied the sort. Grouping the
  // refetched rows would otherwise order by whatever the second query returned.
  const order = new Map(
    pageKeys.map((key, index) => [`${key.playerId}-${key.gameId}`, index]),
  );
  const cards = groupIntoCards(rows).sort(
    (a, b) => (order.get(a.key) ?? 0) - (order.get(b.key) ?? 0),
  );

  const [gameLogs, teamDirectory] = await Promise.all([
    getGameLogsByPlayer(
      cards.map((card) => card.playerId),
      { season: active.season, before: active.week },
    ),
    getTeamDirectory(
      active.season,
      games.flatMap((game) => [game.homeTeamId, game.awayTeamId]),
    ),
  ]);

  // The conference filter applies to the OFFENSE, matching the board: someone
  // narrowed to the SEC wants SEC players to look at, and the soft defense is a
  // fact about whoever they are playing.
  // Always narrowed, never open. With no conference filter the fallback is the
  // displayed conferences rather than every FBS offense, because that is what
  // the board itself falls back to — see `lib/core/board-scope.ts` for the 17
  // dead links that came of the two disagreeing.
  const targets = buildWeeklyTargets(games, ratings, {
    includeOffense: (teamId) =>
      offenseOnBoard(teamDirectory.get(teamId), resolved.conference),
  });

  // The game selector must only offer games the board can populate, for the
  // same reason the targets panel must. A slate carries plenty of G5-vs-G5
  // games with nobody in a displayed conference: measured on 2025 week 8, five
  // of the first eight games in this dropdown returned an empty board. EITHER
  // side qualifying is enough, since a game with one displayed team still shows
  // that team's players.
  const selectableGames = games.filter(
    (game) =>
      offenseOnBoard(teamDirectory.get(game.homeTeamId), resolved.conference) ||
      offenseOnBoard(teamDirectory.get(game.awayTeamId), resolved.conference),
  );

  const marketsByKey = new Map(markets.map((market) => [market.key, market]));
  const coverage = lineCoverage(counts);

  return (
    <Shell>
      <div className="flex flex-col gap-1">
        <span className="label-caption">Legends Sports · College Football</span>
        <h1 className="text-2xl font-extrabold tracking-tight">
          Player Props Board
        </h1>
        {/*
          THE SLATE SUMMARY LINE WAS REMOVED HERE, deliberately, and this note
          is what stops it being re-added by someone reading the page top to
          bottom and finding the heading bare.

          It read "2026 Week 1 · Aug 29 – Sep 7 · 97 games · 2,848 projections,
          433 with a call, 2,415 still awaiting a line". The first half is
          restated card-for-card by the week strip immediately below, and the
          second half is pipeline bookkeeping: a reader does not act on how many
          rows are awaiting a line, and on an opening slate the number is large
          enough to read as a fault rather than as the designed late-line
          behaviour (CLAUDE.md §7).

          The facts it carried are all still on screen. The week, the dates and
          the game count are on the strip; the state of the pricing is the
          development-lines banner and the row count beside the filters, both of
          which appear only when they have something to say.
        */}
      </div>

      <WeekStrip weeks={weeks} active={active} basePath={BOARD_PATH} />

      <BoardControls
        params={resolved}
        markets={markets}
        conferences={conferences}
        games={selectableGames}
        hitRateWindows={config.hitRateWindows}
        resultCount={cardKeys.keys.length}
      />

      {cardKeys.truncated ? (
        <p className="border-negative/40 bg-negative/5 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-negative font-bold uppercase tracking-label">
            Partial slate
          </span>{" "}
          — this week has more rows than the board scans in one pass, so some
          players are missing. Narrow by position or market to see the whole
          slate. (Raise <code className="font-mono">KEY_SCAN_LIMIT</code> if this
          persists.)
        </p>
      ) : null}

      {/*
        KEYED ON THE SYNTHETIC BOOK, NOT ON "ANY LINE EXISTS". This banner used
        to fire whenever a row carried a book line and assert that no book had
        posted a real prop — so the first genuine line `ingest_odds` lands
        would have been announced as a placeholder, and the claim underneath it
        would have been false. What makes a line fake is which book wrote it.
      */}
      {coverage.developmentLine > 0 ? (
        <p className="border-target/30 bg-target/5 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-target font-bold uppercase tracking-label">
            Development lines
          </span>{" "}
          —{" "}
          {coverage.bookLine > 0
            ? `${formatCount(coverage.developmentLine)} of the ${formatCount(
                counts.withBookLine,
              )} priced rows here carry`
            : `every one of the ${formatCount(
                coverage.developmentLine,
              )} priced rows here carries`}{" "}
          a synthetic line: the player&rsquo;s trailing average at −110/−110,
          which de-vigs to exactly 0.500 and makes those edges equal to
          confidence minus 50%. Treat those calls as real and their edges as
          placeholders.{" "}
          {coverage.bookLine > 0
            ? `The other ${formatCount(coverage.bookLine)} come from a book.`
            : "No book has posted a real NCAAF prop yet."}
        </p>
      ) : null}

      {cards.length === 0 ? (
        <EmptyBoard
          params={resolved}
          counts={counts}
          edgeThreshold={config.edgeThreshold}
        />
      ) : (
        <section className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {cards.map((card) => (
            <PlayerCard
              key={card.key}
              card={card}
              marketsByKey={marketsByKey}
              gameLog={gameLogs.get(card.playerId) ?? []}
              hitRateWindow={resolved.hitRateWindow}
              edgeThreshold={config.edgeThreshold}
            />
          ))}
        </section>
      )}

      {totalPages > 1 ? (
        <nav
          aria-label="Board pages"
          className="flex items-center justify-center gap-3 pt-1"
        >
          <PageLink
            href={boardHref(resolved, { page: page - 1 })}
            disabled={page <= 1}
          >
            ← Prev
          </PageLink>
          <span className="text-muted text-xs">
            Page {page} of {formatCount(totalPages)}
            {cardKeys.truncated ? " (capped)" : ""}
          </span>
          <PageLink
            href={boardHref(resolved, { page: page + 1 })}
            disabled={page >= totalPages}
          >
            Next →
          </PageLink>
        </nav>
      ) : null}

      <WeeklyTargets
        targets={targets}
        teams={teamDirectory}
        params={resolved}
        conferenceLabel={resolved.conference ?? null}
        asOfWeek={active.week}
      />

      <p className="text-dim text-xs">
        Opponent rank 1 is the best defense against the position — a HIGH number is the
        softer matchup. Ranks come from opponent-adjusted yards allowed to that
        position: rushing for QB and RB, receiving for WR and TE, so a QB rank
        describes rushing only. Confidence is the share of the projected
        distribution past the line.{" "}
        <Link href="/health" className="hover:text-accent-cyan underline">
          System health
        </Link>
      </p>
    </Shell>
  );
}

/**
 * The board with nothing on it — and, where it can, WHY.
 *
 * "Nothing meets these filters" is true of every empty board and useful on
 * almost none of them. The common cause on this product is structural rather
 * than a user's mistake: EDGES ONLY compares against a book price, and for most
 * of a live week no book has posted one (CLAUDE.md §7), so the toggle empties
 * the board no matter how the other filters are set. Sending someone to widen a
 * confidence threshold in that state is advice that cannot work.
 *
 * So the two structural causes get named and given the one link that fixes
 * them, and the generic message is what is left when neither applies.
 */
function EmptyBoard({
  params,
  counts,
  edgeThreshold,
}: {
  params: BoardParams;
  counts: BoardCounts;
  edgeThreshold: number;
}) {
  const cleared = resetBoardHref(params);

  const showLeans = (
    <Link
      href={boardHref(params, { edgesOnly: false })}
      className="text-accent-cyan hover:underline"
    >
      Turn off Edges only
    </Link>
  );

  if (params.edgesOnly && counts.withBookLine === 0) {
    return (
      <div className="panel p-6">
        <h2 className="section-header mb-2">Nothing priced yet</h2>
        <p className="text-muted max-w-prose text-sm">
          No row on this slate carries a book line, so no row has an edge to
          measure — an edge is the model&rsquo;s probability minus a de-vigged
          book price, and there is no price. The{" "}
          {formatCount(counts.rows)} projections are still here.{" "}
          {showLeans} to see the model&rsquo;s leans.
        </p>
      </div>
    );
  }

  if (params.edgesOnly && counts.overThreshold === 0) {
    return (
      <div className="panel p-6">
        <h2 className="section-header mb-2">No edges this week</h2>
        <p className="text-muted max-w-prose text-sm">
          {formatCount(counts.withBookLine)} rows are priced, but none clears
          the {Math.round(edgeThreshold * 100)}% edge threshold — before any
          other filter is applied. {showLeans} to see the whole slate.
        </p>
      </div>
    );
  }

  return (
    <div className="panel p-6">
      <h2 className="section-header mb-2">No players match</h2>
      <p className="text-muted max-w-prose text-sm">
        Nothing on this slate meets these filters. Widen the confidence or
        opponent-rank thresholds, or{" "}
        <Link href={cleared} className="text-accent-cyan hover:underline">
          clear the filters
        </Link>
        .
      </p>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader activeHref={BOARD_PATH} />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}

function PageLink({
  href,
  disabled,
  children,
}: {
  href: string;
  disabled: boolean;
  children: React.ReactNode;
}) {
  if (disabled) {
    return (
      <span className="text-dim px-3 py-1.5 text-[0.6875rem] font-bold uppercase tracking-label">
        {children}
      </span>
    );
  }
  return (
    <Link
      href={href}
      className="border-border-subtle text-muted hover:text-ink hover:border-border-strong rounded-full border px-3 py-1.5 text-[0.6875rem] font-bold uppercase tracking-label transition-colors"
    >
      {children}
    </Link>
  );
}
