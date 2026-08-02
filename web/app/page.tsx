import Link from "next/link";

import { BoardControls } from "@/components/board/board-controls";
import { PlayerCard } from "@/components/board/player-card";
import { SiteHeader } from "@/components/site-header";
import { WeekStrip } from "@/components/week-strip";
import {
  boardHref,
  CARDS_PER_PAGE,
  parseBoardParams,
  type RawParams,
} from "@/lib/core/board-params";
import { groupIntoCards } from "@/lib/core/board-view";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount, formatDateRange } from "@/lib/core/format";
import {
  getBoardCardKeys,
  getBoardCounts,
  getRowsForCards,
  type BoardFilters,
} from "@/lib/data/board";
import { getConferences, getMarkets } from "@/lib/data/catalogue";
import { getAppConfig } from "@/lib/data/config";
import { getGameLogsByPlayer } from "@/lib/data/players";
import { findWeek, getSlateGames, getSlateWeeks } from "@/lib/data/slate";

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
        <div className="panel p-6">
          <h1 className="section-header mb-2">Not configured</h1>
          <p className="text-muted text-sm">
            Set <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
            <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in{" "}
            <code className="font-mono">web/.env.local</code>, then reload.
          </p>
        </div>
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
    search: resolved.search,
    edgesOnly: resolved.edgesOnly,
    edgeThreshold: config.edgeThreshold,
    minConfidence: resolved.minConfidence,
    maxOpponentRank: resolved.maxOpponentRank,
    sort: resolved.sort,
  };

  const [counts, games, cardKeys] = await Promise.all([
    getBoardCounts(active.season, active.week, config.edgeThreshold),
    getSlateGames(active.season, active.week),
    getBoardCardKeys(filters),
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

  const gameLogs = await getGameLogsByPlayer(
    cards.map((card) => card.playerId),
    { season: active.season, before: active.week },
  );

  const marketsByKey = new Map(markets.map((market) => [market.key, market]));
  const leansOnly = counts.withCall - counts.withBookLine;

  return (
    <Shell>
      <div className="flex flex-col gap-1">
        <span className="label-caption">Legends Sports · College Football</span>
        <h1 className="text-2xl font-extrabold tracking-tight">
          Player Props Board
        </h1>
        <p className="text-muted text-sm">
          {active.season} Week {active.week} ·{" "}
          {formatDateRange(active.firstKickoff, active.lastKickoff)} ·{" "}
          {active.games} games · {formatCount(counts.rows)} projections,{" "}
          {formatCount(leansOnly)} still awaiting a line
        </p>
      </div>

      <WeekStrip weeks={weeks} active={active} />

      <BoardControls
        params={resolved}
        markets={markets}
        conferences={conferences}
        games={games}
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

      {counts.withBookLine > 0 ? (
        <p className="border-target/30 bg-target/5 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-target font-bold uppercase tracking-label">
            Development lines
          </span>{" "}
          — no book has posted a real NCAAF prop yet, so lines here are each
          player&rsquo;s trailing average priced at −110/−110. Those de-vig to
          exactly 0.500, which makes every edge shown equal to confidence minus
          50%. Treat the calls as real and the edges as placeholders.
        </p>
      ) : null}

      {cards.length === 0 ? (
        <div className="panel p-6">
          <h2 className="section-header mb-2">No players match</h2>
          <p className="text-muted max-w-prose text-sm">
            Nothing on this slate meets these filters. Widen the confidence or
            opponent-rank thresholds, or{" "}
            <Link
              href={boardHref(resolved, {
                position: undefined,
                market: undefined,
                game: undefined,
                conference: undefined,
                search: undefined,
                minConfidence: undefined,
                maxOpponentRank: undefined,
                edgesOnly: false,
              })}
              className="text-accent-cyan hover:underline"
            >
              clear the filters
            </Link>
            .
          </p>
        </div>
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

      <p className="text-dim text-xs">
        Opponent rank 1 allows the most to the position — a low number is the
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

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader />
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
