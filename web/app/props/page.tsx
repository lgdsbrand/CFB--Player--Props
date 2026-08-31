import Link from "next/link";

import { BoardControls } from "@/components/board/board-controls";
import { BoardTable } from "@/components/board/board-table";
import { PlayerCard } from "@/components/board/player-card";
import { WeeklyTargets } from "@/components/board/weekly-targets";
import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { DayStrip } from "@/components/day-strip";
import { WeekStrip } from "@/components/week-strip";
import {
  type BoardParams,
  boardHref,
  BOARD_PATH,
  CARDS_PER_PAGE,
  parseBoardParams,
  resetBoardHref,
  resolveBoardView,
  ROWS_PER_PAGE,
  type RawParams,
} from "@/lib/core/board-params";
import { offenseOnBoard } from "@/lib/core/board-scope";
import { findSlateDay, slateDays } from "@/lib/core/slate-days";
import { kickoffCutoff, playedCount, upcomingGames } from "@/lib/core/kickoff";
import {
  groupIntoCards,
  lineCoverage,
  type PlayerCard as PlayerCardData,
} from "@/lib/core/board-view";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount } from "@/lib/core/format";
import { buildWeeklyTargets } from "@/lib/core/targets";
import {
  getBoardCardKeys,
  getBoardCounts,
  getBoardRowCount,
  getBoardRowPage,
  getRowsForCards,
  type BoardCounts,
  type BoardFilters,
} from "@/lib/data/board";
import type { BoardRow } from "@/lib/core/types";
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

  // ONE instant for every read this render fires, so the rows, the counts and
  // the day pills cannot be measured against three different clocks across a
  // kickoff. See `lib/core/kickoff.ts`.
  const cutoff = kickoffCutoff();

  const filters: BoardFilters = {
    kickoffCutoff: cutoff,
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

  const view = resolveBoardView(resolved);

  const [counts, games, ratings] = await Promise.all([
    getBoardCounts(active.season, active.week, config.edgeThreshold, {
      kickoffCutoff: cutoff,
    }),
    getSlateGames(active.season, active.week),
    // Pinned to as_of_week = the week on screen, never "the latest": a rating
    // from a later cutoff knows results the reader is being asked to predict.
    getDefenseRatings(active.season, active.week),
  ]);

  // AFTER the fetch, not before: the days of a week are derived from its games,
  // so this is the first point they can be known. An unrecognised day resolves
  // to undefined — all days — and `dayParams` carries the resolved value rather
  // than the requested one, so a stale link corrects itself in the strip
  // instead of leaving a pill highlighted that filters nothing.
  // DERIVED FROM THE GAMES STILL TO KICK, not from the week. A pill for a day
  // whose games are all played would filter the board to nothing and read as a
  // fault; the board itself has already dropped those rows. Same rule the
  // conference filter follows on Analyze Games — the strip describes the slate
  // being looked at, not the calendar.
  const live = upcomingGames(games, cutoff);
  const played = playedCount(games, cutoff);
  const days = slateDays(live);
  const activeDay = findSlateDay(days, resolved.day);
  const dayParams = { ...resolved, day: activeDay?.key };
  const boardFilters: BoardFilters = activeDay
    ? { ...filters, gameIds: activeDay.gameIds }
    : filters;

  // THE TWO LAYOUTS PAGE DIFFERENT THINGS, and that is why the fetch branches
  // rather than one path feeding both. A card is one player holding every
  // market he has, so the card path must establish which PLAYERS make the page
  // before it can fetch their rows. A table row is one prop, so the table pages
  // rows — but by the same two-step, establishing WHICH rows before fetching
  // them, because the one-query version does not survive the free tier's
  // `statement_timeout` (measured, in `getBoardRowPage`).
  //
  // The table path does still page an exact total rather than a scan capped at
  // `KEY_SCAN_MAX_ROWS`, so unlike the card path it cannot be truncated — which
  // is why the "partial slate" banner is card-only below.
  const board =
    view === "table"
      ? await loadTable(boardFilters, resolved.page)
      : await loadCards(boardFilters, resolved.page);

  const [gameLogs, teamDirectory] = await Promise.all([
    getGameLogsByPlayer(board.playerIds, {
      season: active.season,
      before: active.week,
    }),
    getTeamDirectory(
      active.season,
      games.flatMap((game) => [game.homeTeamId, game.awayTeamId]),
    ),
  ]);

  const { page, totalPages } = board;

  // The conference filter applies to the OFFENSE, matching the board: someone
  // narrowed to the SEC wants SEC players to look at, and the soft defense is a
  // fact about whoever they are playing.
  // Always narrowed, never open. With no conference filter the fallback is the
  // displayed conferences rather than every FBS offense, because that is what
  // the board itself falls back to — see `lib/core/board-scope.ts` for the 17
  // dead links that came of the two disagreeing.
  const targets = buildWeeklyTargets(live, ratings, {
    includeOffense: (teamId) =>
      offenseOnBoard(teamDirectory.get(teamId), resolved.conference),
  });

  // The game selector must only offer games the board can populate, for the
  // same reason the targets panel must. A slate carries plenty of G5-vs-G5
  // games with nobody in a displayed conference: measured on 2025 week 8, five
  // of the first eight games in this dropdown returned an empty board. EITHER
  // side qualifying is enough, since a game with one displayed team still shows
  // that team's players.
  const selectableGames = live.filter(
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

      <DayStrip
        days={days}
        activeDay={activeDay?.key}
        params={dayParams}
      />

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

      <BoardControls
        params={resolved}
        markets={markets}
        conferences={conferences}
        games={selectableGames}
        hitRateWindows={config.hitRateWindows}
        resultCount={board.resultCount}
        resultNoun={board.kind === "table" ? "prop" : "player"}
        view={view}
      />

      {board.kind === "cards" && board.truncated ? (
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

      {coverage.evenPricedBookLine > 0 ? (
        <p className="border-target/30 bg-target/5 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-target font-bold uppercase tracking-label">
            Even-priced
          </span>{" "}
          —{" "}
          {coverage.evenPricedBookLine === coverage.bookLine
            ? `all ${formatCount(coverage.bookLine)} rows priced by a book carry`
            : `${formatCount(coverage.evenPricedBookLine)} of the ${formatCount(
                coverage.bookLine,
              )} rows priced by a book carry`}{" "}
          a two-way price that de-vigs to exactly 0.500 — the book pricing both
          sides the same, which is normal on a thin college prop and means it is
          not taking a side. The edge on those rows is therefore the model&rsquo;s
          own confidence minus 50%, not a disagreement with the market, so a
          confident call prints a large edge whatever the book thinks. The call
          still stands; read the edge as a restatement of it.
        </p>
      ) : null}

      {board.resultCount === 0 ? (
        <EmptyBoard
          params={resolved}
          counts={counts}
          edgeThreshold={config.edgeThreshold}
        />
      ) : board.kind === "table" ? (
        <BoardTable
          rows={board.rows}
          marketsByKey={marketsByKey}
          gameLogs={gameLogs}
          hitRateWindows={config.hitRateWindows}
          hitRateWindow={resolved.hitRateWindow}
          edgeThreshold={config.edgeThreshold}
        />
      ) : (
        /*
          `items-start`, not the default stretch. CSS grid makes every item in a
          row as tall as the tallest, and nothing inside a card grows to fill —
          so a player with three markets was padded to match the six-market card
          beside him. Measured at 1440: every card on the page rendered exactly
          963px, which on the three-market ones is ~400px of bordered empty
          panel. The gap is still there, but it is now between cards where it
          reads as spacing, rather than inside one where it reads as a card that
          failed to load.
        */
        <section className="grid items-start gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {board.cards.map((card) => (
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
            {board.kind === "cards" && board.truncated ? " (capped)" : ""}
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
        distribution past the line.
      </p>
    </Shell>
  );
}

/**
 * One page of the board, in whichever shape the layout needs.
 *
 * Both variants carry `playerIds` so the page can fetch game logs once without
 * caring which layout produced them — the hit rates on a card and in a table
 * row are graded from the same logs by the same core function.
 */
type BoardPageContent =
  | {
      kind: "table";
      rows: BoardRow[];
      playerIds: number[];
      page: number;
      totalPages: number;
      /** Matching PROPS across the whole week, not just this page. */
      resultCount: number;
    }
  | {
      kind: "cards";
      cards: PlayerCardData[];
      playerIds: number[];
      page: number;
      totalPages: number;
      /** Matching PLAYERS across the whole week, not just this page. */
      resultCount: number;
      /** The key scan hit the row cap, so the week is not fully represented. */
      truncated: boolean;
    };

/**
 * Table view: page the filtered rows directly.
 *
 * COUNT FIRST, THEN CLAMP, THEN READ — in that order, and the order is the
 * point. PostgREST answers a range past the end of a result with an error
 * ("Requested range not satisfiable"), not with an empty page, so asking for
 * page 999 of a 70-page week 500s the board. Both a stale link and a filter
 * narrowed while the reader sat on page 6 produce exactly that request. Knowing
 * the total before asking for a range makes it unaskable.
 *
 * `getBoardRowPage` is itself a two-step read for a separate, measured reason —
 * see its comment; the single wide query it replaces does not finish inside the
 * free tier's `statement_timeout`.
 */
async function loadTable(
  filters: BoardFilters,
  requestedPage: number,
): Promise<BoardPageContent> {
  const total = await getBoardRowCount(filters);
  const totalPages = Math.max(Math.ceil(total / ROWS_PER_PAGE), 1);
  const page = Math.min(requestedPage, totalPages);

  const rows = await getBoardRowPage({
    ...filters,
    limit: ROWS_PER_PAGE,
    offset: (page - 1) * ROWS_PER_PAGE,
  });

  return {
    kind: "table",
    rows,
    playerIds: rows.map((row) => row.playerId),
    page,
    totalPages,
    resultCount: total,
  };
}

/**
 * Card view: establish which player-games make the page, then fetch their rows.
 *
 * Unchanged behaviour, moved out of the component so the two layouts read as
 * two paths rather than as one path with conditionals threaded through it.
 */
async function loadCards(
  filters: BoardFilters,
  requestedPage: number,
): Promise<BoardPageContent> {
  const cardKeys = await getBoardCardKeys(filters);

  const totalPages = Math.max(
    Math.ceil(cardKeys.keys.length / CARDS_PER_PAGE),
    1,
  );
  const page = Math.min(requestedPage, totalPages);
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

  return {
    kind: "cards",
    cards,
    playerIds: cards.map((card) => card.playerId),
    page,
    totalPages,
    resultCount: cardKeys.keys.length,
    truncated: cardKeys.truncated,
  };
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
