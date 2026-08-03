import Link from "next/link";
import { notFound } from "next/navigation";

import { LastFive } from "@/components/board/last-five";
import { ProjectionBar } from "@/components/board/projection-bar";
import { TeamChip } from "@/components/board/team-chip";
import { DefenseDetail } from "@/components/player/defense-detail";
import { GameLogTable } from "@/components/player/game-log-table";
import { HitRateChart } from "@/components/player/hit-rate-chart";
import { MarketTabs } from "@/components/player/market-tabs";
import { SplitGrid } from "@/components/player/split-grid";
import { SiteHeader } from "@/components/site-header";
import type { RawParams } from "@/lib/core/board-params";
import { defenseStatForMarket, rankBasis } from "@/lib/core/defense-view";
import { isSupabaseConfigured } from "@/lib/core/env";
import {
  formatAmericanOdds,
  formatConfidence,
  formatEdge,
  formatKickoff,
  formatLine,
} from "@/lib/core/format";
import { gradeGames, hitRate, type GradedGame } from "@/lib/core/hit-rate";
import { parsePlayerParams } from "@/lib/core/player-params";
import {
  rankBands,
  rankSplits,
  venueSplits,
  windowSplits,
} from "@/lib/core/splits";
import type { BoardRow, Market, PositionGroup } from "@/lib/core/types";
import { getAiRead } from "@/lib/data/ai-reads";
import { getPlayerBoardRows } from "@/lib/data/board";
import { getMarkets } from "@/lib/data/catalogue";
import { getAppConfig } from "@/lib/data/config";
import {
  getDefenseGameLog,
  getDefenseRanksAt,
  getDefenseRatings,
  rankKey,
} from "@/lib/data/defense";
import { getPlayerQuotes, SYNTHETIC_BOOK_KEY } from "@/lib/data/odds";
import { getPlayerGameLog, getPlayerIdentity } from "@/lib/data/players";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";

/**
 * Player detail (CLAUDE.md §7).
 *
 * One market is selected at a time and EVERYTHING BELOW IS GRADED AGAINST ITS
 * LINE — the chart, the splits and the log. That is not a layout convenience: a
 * hit rate is a question about a line, so "his L10" is meaningless until the
 * line is named, and showing five markets' worth at once would produce five
 * different answers to what looks like one question.
 *
 * THE GAME LOG STOPS BEFORE THIS WEEK. Every read here passes `before: week`,
 * the same cutoff the board's last-5 dots use. On a completed week that hides a
 * result the reader may want, and it is still right: showing week 10's outcome
 * beside week 10's projection marks the model's homework with the answer sheet
 * open, and the two surfaces would disagree about the same player's L5.
 */

export default async function PlayerDetail({
  params,
  searchParams,
}: {
  params: Promise<{ playerId: string }>;
  searchParams: Promise<RawParams>;
}) {
  const { playerId: rawId } = await params;
  const playerId = Number.parseInt(rawId, 10);
  if (!Number.isFinite(playerId)) notFound();

  if (!isSupabaseConfigured()) {
    return (
      <Shell>
        <div className="panel p-6">
          <h1 className="section-header mb-2">Not configured</h1>
          <p className="text-muted text-sm">
            Set the Supabase environment variables in{" "}
            <code className="font-mono">web/.env.local</code>, then reload.
          </p>
        </div>
      </Shell>
    );
  }

  const raw = await searchParams;
  const requested = parsePlayerParams(playerId, raw);

  const [identity, weeks, config, markets] = await Promise.all([
    getPlayerIdentity(playerId),
    getSlateWeeks(),
    getAppConfig(),
    getMarkets(),
  ]);

  // An id nobody has heard of is a 404. An id with no rows this week is not —
  // that is a real player on a bye, or below the usage floor, and it deserves
  // an explanation under his own name.
  if (!identity) notFound();

  const active = findWeek(weeks, requested.season, requested.week);
  if (!active) {
    return (
      <Shell>
        <NotOnSlate name={identity.name} reason="No week has model output yet." />
      </Shell>
    );
  }

  // Filters follow the RESOLVED week, not the requested one: an unknown week
  // falls back to the latest, and links built from the request would then point
  // at a week the page is not showing.
  const resolved = {
    playerId,
    season: active.season,
    week: active.week,
    market: requested.market,
  };

  const rows = await getPlayerBoardRows(playerId, active.season, active.week);
  if (rows.length === 0) {
    return (
      <Shell>
        <NotOnSlate
          name={identity.name}
          reason={`Nothing projected for ${identity.name} in ${active.season} week ${active.week}. He is on a bye, did not clear the usage floor, or his team is not on this slate.`}
          season={active.season}
          week={active.week}
        />
      </Shell>
    );
  }

  const marketsByKey = new Map(markets.map((market) => [market.key, market]));
  const ordered = orderMarkets(rows, marketsByKey);
  const activeRow =
    ordered.find((row) => row.marketKey === requested.market) ?? ordered[0];
  const activeMarket = marketsByKey.get(activeRow.marketKey);

  // Position comes from the board row (season-scoped) rather than from
  // `players` (most recent known) — a transfer can change it between seasons.
  const position = activeRow.positionGroup ?? identity.positionGroup;

  const [gameLog, quotes, aiRead, ratings, defenseGames] = await Promise.all([
    getPlayerGameLog(playerId, {
      season: active.season,
      before: active.week,
      limit: 30,
    }),
    getPlayerQuotes(playerId, active.season, active.week),
    getAiRead(playerId, active.season, active.week),
    getDefenseRatings(active.season, active.week, { positionGroup: position }),
    getDefenseGameLog(
      activeRow.opponentTeamId,
      active.season,
      position,
      { before: active.week },
    ),
  ]);

  // Binary markets grade on the OVER whatever the call was, so a green bar
  // means the player scored — see `market-row.tsx` for the full reasoning.
  const gradeSide = activeMarket?.isBinary ? "over" : (activeRow.side ?? "over");
  const graded: GradedGame[] =
    activeMarket && activeRow.line !== null
      ? gradeGames(gameLog, activeMarket.statColumn, activeRow.line, gradeSide)
      : [];

  const ranksByGame = await rankLookup(
    graded,
    active.season,
    position,
  );

  const ranked = ratings.filter((rating) => rating.rankVsPosition !== null);
  const bands = rankBands(ranked.length);
  const byRank = rankSplits(graded, ranksByGame, bands);
  const opponentRating = ratings.find(
    (rating) => rating.defenseTeamId === activeRow.opponentTeamId,
  );

  return (
    <Shell>
      <Link
        href={`/?season=${active.season}&week=${active.week}`}
        className="text-muted hover:text-accent-cyan w-fit text-xs"
      >
        ← Back to the board
      </Link>

      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-extrabold tracking-tight">
            {activeRow.playerName}
          </h1>
          <span className="pill bg-accent-indigo/15 text-accent-cyan">
            {position}
          </span>
        </div>
        <div className="text-muted flex flex-wrap items-center gap-1.5 text-xs">
          <TeamChip
            abbreviation={activeRow.teamAbbreviation}
            color={activeRow.teamColor}
            altColor={activeRow.teamAltColor}
            title={activeRow.teamSchool}
          />
          <span className="text-dim">{activeRow.isHome ? "vs" : "@"}</span>
          <TeamChip
            abbreviation={activeRow.opponentAbbreviation}
            color={null}
            altColor={null}
            title={activeRow.opponentSchool}
          />
          <span className="text-dim">·</span>
          <span>{formatKickoff(activeRow.startDate)}</span>
          <span className="text-dim">·</span>
          <span>
            {active.season} Week {active.week}
          </span>
          {activeRow.neutralSite ? (
            <span className="text-dim">· neutral site</span>
          ) : null}
          {activeRow.conferenceName ? (
            <span className="text-dim">· {activeRow.conferenceName}</span>
          ) : null}
        </div>
      </header>

      <MarketTabs rows={ordered} activeKey={activeRow.marketKey} params={resolved} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-4">
          <section className="panel flex flex-col gap-3 p-4">
            <header className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="section-header">
                {activeRow.marketLabel ?? activeRow.marketName}
              </h2>
              <Call row={activeRow} market={activeMarket} />
            </header>

            {activeRow.line === null ? (
              <p className="text-muted max-w-prose text-xs">
                No line posted for this market yet, so there is nothing to grade
                past games against. The projected range below is the model&rsquo;s
                lean; the call and confidence fill in when a book posts.
              </p>
            ) : (
              <HitRateChart
                points={graded.slice(0, 10).map((game) => ({
                  gameId: game.gameId,
                  week: game.week,
                  value: game.value,
                  opponent: game.opponentAbbreviation ?? "—",
                  isHome: game.isHome,
                  neutralSite: game.neutralSite,
                  hit: game.hit,
                }))}
                line={activeRow.line}
                unit={activeMarket?.unit ?? null}
                side={gradeSide}
              />
            )}

            <LastFive
              summary={
                graded.length > 0
                  ? hitRate(graded, config.hitRateWindows[0] ?? 5)
                  : null
              }
              side={gradeSide}
              window={config.hitRateWindows[0] ?? 5}
              verb={activeMarket?.isBinary ? "scored" : undefined}
            />
          </section>

          <section className="panel flex flex-col gap-3 p-4">
            <h2 className="section-header">Hit-rate splits</h2>
            {graded.length === 0 ? (
              <p className="text-dim text-xs">
                Nothing to split until this market has a line.
              </p>
            ) : (
              <>
                <div className="flex flex-col gap-1.5">
                  <span className="label-caption">Recent form</span>
                  <SplitGrid splits={windowSplits(graded, config.hitRateWindows)} />
                </div>

                <div className="flex flex-col gap-1.5">
                  <span className="label-caption">Venue</span>
                  <SplitGrid
                    splits={venueSplits(graded)}
                    note="Secondary in college football — neutral sites are common and schedules are uneven."
                  />
                </div>

                <div className="flex flex-col gap-1.5">
                  <span className="label-caption">
                    By opponent rank vs {position}
                  </span>
                  <SplitGrid
                    splits={byRank.splits}
                    emptyLabel="No past opponent carried a rating at its own cutoff."
                    note={
                      `Bands are thirds of the ${ranked.length} defenses rated vs ${position}, ` +
                      `each opponent taken at the rank it held entering that week. ` +
                      `Rank 1 is the best defense, on ${rankBasis(position).label}.` +
                      (byRank.unranked > 0
                        ? ` ${byRank.unranked} game${byRank.unranked === 1 ? "" : "s"} set aside as unrated.`
                        : "")
                    }
                  />
                </div>
              </>
            )}
          </section>

          <section className="panel flex flex-col gap-3 p-4">
            <h2 className="section-header">Game log</h2>
            <GameLogTable
              games={graded}
              unit={activeMarket?.unit ?? null}
              rankByGameId={ranksByGame}
            />
            {graded.length === 0 && gameLog.length > 0 ? (
              <p className="text-dim text-xs">
                {gameLog.length} completed game
                {gameLog.length === 1 ? "" : "s"} on record, but this market has
                no line to grade them against.
              </p>
            ) : null}
          </section>
        </div>

        <div className="flex flex-col gap-4">
          <DefenseDetail
            opponentSchool={activeRow.opponentSchool}
            opponentAbbreviation={activeRow.opponentAbbreviation}
            position={position}
            rows={defenseGames}
            rank={opponentRating?.rankVsPosition ?? null}
            rankedDefenses={ranked.length}
            gamesRated={opponentRating?.gamesIncluded ?? null}
            highlight={
              activeMarket ? defenseStatForMarket(activeMarket.statColumn) : null
            }
            asOfWeek={active.week}
          />

          <section className="panel flex flex-col gap-3 p-4">
            <h2 className="section-header flex items-center gap-2">
              <span aria-hidden>⚡</span>
              AI read
            </h2>
            {aiRead ? (
              <>
                <p className="text-sm leading-relaxed">{aiRead.content}</p>
                <p className="text-dim text-[0.625rem]">
                  {aiRead.model} · prompt {aiRead.promptVersion}
                </p>
              </>
            ) : (
              <p className="text-muted max-w-prose text-xs">
                No read generated for this player this week. Reads are written
                once per week by the pipeline and cached — the page never calls a
                model. Generation ships in Phase 5.
              </p>
            )}
          </section>

          <Odds
            row={activeRow}
            quotes={quotes.filter((q) => q.marketKey === activeRow.marketKey)}
          />

          <Projection row={activeRow} market={activeMarket} />
        </div>
      </div>
    </Shell>
  );
}

/**
 * Each past opponent's rank as it stood entering THAT game's week.
 *
 * Keyed by game id so the log and the splits agree cell for cell.
 */
async function rankLookup(
  graded: GradedGame[],
  season: number,
  position: PositionGroup,
): Promise<Map<number, number>> {
  if (graded.length === 0) return new Map();

  const ranks = await getDefenseRanksAt(
    season,
    position,
    graded.map((game) => ({
      defenseTeamId: game.opponentTeamId,
      week: game.week,
    })),
  );

  const byGame = new Map<number, number>();
  for (const game of graded) {
    const rank = ranks.get(rankKey(game.opponentTeamId, game.week));
    if (rank !== undefined) byGame.set(game.gameId, rank);
  }
  return byGame;
}

/** Markets in catalogue order, so the tabs match the board's sub-cards. */
function orderMarkets(
  rows: BoardRow[],
  marketsByKey: Map<string, Market>,
): BoardRow[] {
  return [...rows].sort(
    (a, b) =>
      (marketsByKey.get(a.marketKey)?.sortOrder ?? 999) -
      (marketsByKey.get(b.marketKey)?.sortOrder ?? 999),
  );
}

function Call({ row, market }: { row: BoardRow; market: Market | undefined }) {
  if (market?.isBinary) {
    return row.modelProbOver === null ? (
      <span className="pill bg-panel text-muted">No projection</span>
    ) : (
      <span className="flex items-baseline gap-2">
        <span className="gradient-text text-2xl font-extrabold leading-none">
          {formatConfidence(row.modelProbOver)}
        </span>
        <span className="text-dim text-[0.625rem] font-semibold uppercase tracking-label">
          to score
        </span>
      </span>
    );
  }

  if (row.hasCall && row.side && row.confidence !== null) {
    return (
      <span className="flex items-center gap-2">
        <span
          className={
            "pill " +
            (row.side === "over"
              ? "bg-positive/15 text-positive"
              : "bg-negative/15 text-negative")
          }
        >
          {row.side} {formatLine(row.line ?? 0)}
        </span>
        <span className="gradient-text text-2xl font-extrabold leading-none">
          {formatConfidence(row.confidence)}
        </span>
      </span>
    );
  }

  return <span className="pill bg-panel text-muted">Lean · no line</span>;
}

function Odds({
  row,
  quotes,
}: {
  row: BoardRow;
  quotes: Awaited<ReturnType<typeof getPlayerQuotes>>;
}) {
  return (
    <section className="panel flex flex-col gap-2 p-4">
      <h2 className="section-header">Odds (book)</h2>
      {quotes.length === 0 ? (
        <p className="text-muted text-xs">
          No book has posted this market. College props go up Thursday or Friday
          for Saturday games, so an empty panel early in the week is expected.
        </p>
      ) : (
        <table className="w-full border-collapse text-left text-xs">
          <thead>
            <tr className="text-dim [&>th]:py-1 [&>th]:font-semibold [&>th]:uppercase [&>th]:tracking-label [&>th]:text-[0.625rem]">
              <th>Book</th>
              <th className="text-right">Line</th>
              <th className="text-right">Over</th>
              <th className="text-right">Under</th>
            </tr>
          </thead>
          <tbody>
            {quotes.map((quote) => (
              <tr
                key={quote.lineId}
                className="border-border-subtle border-t [&>td]:py-1.5"
              >
                <td className="text-muted">{quote.sportsbookName}</td>
                <td className="text-right font-semibold tabular-nums">
                  {formatLine(quote.line)}
                </td>
                <td className="text-right font-mono tabular-nums">
                  {formatAmericanOdds(quote.overPrice)}
                </td>
                <td className="text-right font-mono tabular-nums">
                  {formatAmericanOdds(quote.underPrice)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {row.edge !== null ? (
        <p className="text-dim text-[0.625rem]">
          Edge {formatEdge(row.edge)} — the model&rsquo;s probability minus the
          de-vigged book probability, not a difference between the projection and
          the line.
        </p>
      ) : null}

      {/*
        The board carries this warning at the top of the page; a player page
        reached by a direct link never passes through it, and the edge above is
        the single most misleading number on the screen without it.
      */}
      {row.sportsbookKey === SYNTHETIC_BOOK_KEY ? (
        <p className="border-target/30 bg-target/5 text-muted rounded-lg border px-2 py-1.5 text-[0.625rem]">
          <span className="text-target font-bold uppercase tracking-label">
            Development line
          </span>{" "}
          — no book has posted a real NCAAF prop yet. This line is the
          player&rsquo;s trailing average priced at −110/−110, which de-vigs to
          exactly 0.500 and makes the edge a restatement of confidence rather
          than a disagreement with a market.
        </p>
      ) : null}
    </section>
  );
}

/**
 * The projected range — SECONDARY DETAIL, and placed last for that reason
 * (CLAUDE.md §1).
 *
 * The client's pitcher card leads with a raw projection next to the line, which
 * can read as plainly wrong in individual cases even when the probability is
 * sound. This page leads with the call and the confidence; the distribution
 * behind them is available here for anyone who wants to interrogate it.
 */
function Projection({
  row,
  market,
}: {
  row: BoardRow;
  market: Market | undefined;
}) {
  return (
    <section className="panel flex flex-col gap-2 p-4">
      <h2 className="section-header">Projected range</h2>

      {market?.isBinary ? (
        <p className="text-muted text-xs">
          A yes/no market has no range — the output is the single probability
          above.
        </p>
      ) : (
        <>
          <ProjectionBar
            median={row.projectedMedian}
            p10={row.projectedP10}
            p90={row.projectedP90}
            line={row.line}
            side={row.side}
          />
          <p className="text-dim text-[0.625rem]">
            p10 to p90 of the projected distribution. The call above is the share
            of that distribution past the line, which is the claim being made —
            the median is context.
          </p>
        </>
      )}

      {row.priorWeight !== null ? (
        <p className="text-dim text-[0.625rem]">
          {Math.round(row.priorWeight * 100)}% of this projection comes from
          priors rather than this season. Transfers and NIL mean prior-year
          production often happened at another school, so that share is
          deliberately down-weighted and shrinks as the season accumulates.
        </p>
      ) : null}
    </section>
  );
}

function NotOnSlate({
  name,
  reason,
  season,
  week,
}: {
  name: string;
  reason: string;
  season?: number;
  week?: number;
}) {
  return (
    <div className="panel flex flex-col gap-2 p-6">
      <h1 className="text-xl font-extrabold tracking-tight">{name}</h1>
      <p className="text-muted max-w-prose text-sm">{reason}</p>
      <Link
        href={
          season !== undefined && week !== undefined
            ? `/?season=${season}&week=${week}`
            : "/"
        }
        className="text-accent-cyan w-fit text-sm hover:underline"
      >
        Back to the board
      </Link>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}
