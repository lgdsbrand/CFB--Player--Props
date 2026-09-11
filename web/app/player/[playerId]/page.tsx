import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { EvidencePill } from "@/components/board/evidence-pill";
import { LastFive } from "@/components/board/last-five";
import { ProjectionBar } from "@/components/board/projection-bar";
import { TeamChip } from "@/components/board/team-chip";
import { WeatherPanel } from "@/components/games/weather-panel";
import { NotConfigured } from "@/components/not-configured";
import { DefenseDetail } from "@/components/player/defense-detail";
import { GameLogTable } from "@/components/player/game-log-table";
import { GameTabs } from "@/components/player/game-tabs";
import { HitRateChart } from "@/components/player/hit-rate-chart";
import { LadderPanel } from "@/components/player/ladder-panel";
import { MarketTabs } from "@/components/player/market-tabs";
import { SplitGrid } from "@/components/player/split-grid";
import { SiteHeader } from "@/components/site-header";
import { BOARD_PATH, scopedHref, type RawParams } from "@/lib/core/board-params";
import { defenseStatForMarket, rankBasis } from "@/lib/core/defense-view";
import { isSupabaseConfigured } from "@/lib/core/env";
import { evidenceFor } from "@/lib/core/evidence";
import {
  formatAmericanOdds,
  formatConfidence,
  formatEdge,
  formatGameLine,
  formatKickoff,
  formatLine,
  formatVenue,
} from "@/lib/core/format";
import {
  gradeGames,
  hitRate,
  priorSeasonCount,
  topUpFromPriorSeason,
  type GradedGame,
} from "@/lib/core/hit-rate";
import {
  orderedGames,
  resolveGameId,
  rowsForGame,
} from "@/lib/core/player-games";
import { parsePlayerParams, playerHref } from "@/lib/core/player-params";
import {
  rankBands,
  rankSplits,
  venueSplits,
  windowSplits,
} from "@/lib/core/splits";
import type { BoardRow, Market, PositionGroup } from "@/lib/core/types";
import {
  borrowsPriorSeasonForm,
  DEFAULT_SPORT,
  type Sport,
} from "@/lib/core/sport";
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
import { getGameConditions } from "@/lib/data/weather";

/**
 * Bars on the hit-rate chart. Also the floor on the sample a young NFL season is
 * topped up to, so the chart is never shorter than a hit-rate window beside it.
 */
const CHART_GAMES = 10;

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
        <NotConfigured />
      </Shell>
    );
  }

  const raw = await searchParams;
  const requested = parsePlayerParams(playerId, raw);
  // What the LINK claims. A player id determines its sport and the URL only
  // claims one, so this is checked against the player's own rows below and
  // corrected with a redirect when the two disagree.
  const sport = requested.sport;

  // EVERY READ IN THIS WAVE IS CACHED, which is the point of it being alone.
  // The page's cost is the number of times it WAITS, not the number of queries
  // it runs, because a wave's members overlap — so a wave with one uncached
  // member costs exactly as much as an uncached wave. `getPlayerIdentity` used
  // to sit here and made this wait a full round trip on every view; it is now
  // fetched only on the paths that actually need it, all of which end in a
  // message rather than a board. Measured at ~415ms per round trip from a
  // development machine (lib/data/cache.ts).
  const [weeks, config, markets] = await Promise.all([
    getSlateWeeks(sport),
    getAppConfig(),
    getMarkets(),
  ]);

  const active = findWeek(weeks, requested.season, requested.week);
  if (!active) {
    return (
      <Shell sport={sport}>
        <NotOnSlate
          name={await nameOr404(playerId)}
          reason="No week has model output yet."
          sport={sport}
        />
      </Shell>
    );
  }

  const rows = await getPlayerBoardRows(playerId, active.season, active.week);
  if (rows.length === 0) {
    const identity = await getPlayerIdentity(playerId);
    if (!identity) notFound();
    // NO ROWS CAN MEAN THE LINK NAMED THE WRONG LEAGUE. A player from the other
    // sport, looked up in this sport's week list, lands on a week he has no rows
    // in — so ask the player, and re-request under his own league before
    // concluding he is off the slate.
    if (identity.sport !== sport) {
      redirect(playerHref({ ...requested, sport: identity.sport }));
    }
    const name = identity.name;
    return (
      <Shell sport={sport}>
        <NotOnSlate
          name={name}
          reason={`Nothing projected for ${name} in ${active.season} week ${active.week}. He is on a bye, did not clear the usage floor, or his team is not on this slate.`}
          sport={sport}
          season={active.season}
          week={active.week}
        />
      </Shell>
    );
  }

  // EVERYTHING BELOW DESCRIBES ONE GAME, so the game is resolved before the
  // market is. A handful of teams play twice inside CFBD's nine-or-ten-day week
  // 1 — see `player-games.ts` — and this page used to read every row for the
  // week, which rendered each market twice and then described whichever game
  // sorted first in the header, the defense panel and the conditions.
  const games = orderedGames(rows);
  const activeGameId = resolveGameId(games, requested.game);
  const gameRows = rowsForGame(rows, activeGameId);

  // Filters follow the RESOLVED week and game, not the requested ones: an
  // unknown week falls back to the latest and an unknown game to the next one,
  // and links built from the request would then point somewhere the page is not
  // showing.
  const resolved = {
    playerId,
    sport,
    season: active.season,
    week: active.week,
    market: requested.market,
    game: games.length > 1 ? (activeGameId ?? undefined) : undefined,
  };

  const marketsByKey = new Map(markets.map((market) => [market.key, market]));
  const ordered = orderMarkets(gameRows, marketsByKey);
  const activeRow =
    ordered.find((row) => row.marketKey === requested.market) ?? ordered[0];

  // THE LINK CLAIMED THE WRONG SPORT. A player id determines its league; the
  // URL only claims one, and every link into this page before 2026-09-11 claimed
  // none, which parses as college. The week list above was read for that sport
  // and the header, defense ratings and back link below would all describe it,
  // so the page is re-requested under the right league rather than patched one
  // field at a time. It cannot loop: the redirect carries the row's own sport.
  if (activeRow.sport !== sport) {
    redirect(playerHref({ ...requested, sport: activeRow.sport }));
  }
  const activeMarket = marketsByKey.get(activeRow.marketKey);

  // Position comes from the board row (season-scoped) rather than from
  // `players` (most recent known) — a transfer can change it between seasons.
  // The fallback costs a round trip and is worth keeping: `position_group` is
  // left-joined through `player_team_seasons`, so a projection written for a
  // player whose roster row is missing carries none, and everything below —
  // the defense panel, the splits, the rank bands — is scoped by it.
  const position =
    activeRow.positionGroup ?? (await getPlayerIdentity(playerId))?.positionGroup;
  if (!position) notFound();

  const [gameLog, quotes, aiRead, ratings, defenseGames, conditions] =
    await Promise.all([
      getPlayerGameLog(playerId, {
        season: active.season,
        before: active.week,
        limit: 30,
        // From the ROW, not the URL. Links into this page do not carry
        // `?sport=`, so the URL would call every NFL player a college one.
        includePriorSeason: borrowsPriorSeasonForm(activeRow.sport),
      }),
      getPlayerQuotes(playerId, active.season, active.week),
      getAiRead(playerId, active.season, active.week),
      getDefenseRatings(active.season, active.week, sport, {
        positionGroup: position,
      }),
      getDefenseGameLog(
        activeRow.opponentTeamId,
        active.season,
        position,
        { before: active.week },
      ),
      // Joins this wave rather than forming its own: a wave costs one round
      // trip whatever its width, so the conditions read is effectively free
      // here and would cost a full ~415ms as a sixth wait.
      getGameConditions(activeRow.gameId),
    ]);

  // Binary markets grade on the OVER whatever the call was, so a green bar
  // means the player scored — see `market-row.tsx` for the full reasoning.
  const gradeSide = activeMarket?.isBinary ? "over" : (activeRow.side ?? "over");
  const graded: GradedGame[] =
    activeMarket && activeRow.line !== null
      ? gradeGames(gameLog, activeMarket.statColumn, activeRow.line, gradeSide)
      : [];

  // EVERYTHING BELOW READS THIS SAMPLE, not `graded`. On the NFL the log also
  // holds last season, and this keeps all of this season while topping it up
  // to the chart's ten games — so the bars, the L-windows, the venue and rank
  // splits and the log all describe the same games. Once this season has ten,
  // last season is gone from every one of them.
  const sample = topUpFromPriorSeason(
    graded,
    active.season,
    Math.max(CHART_GAMES, ...config.hitRateWindows),
  );
  const borrowed = priorSeasonCount(sample, active.season);

  const ranksByGame = await rankLookup(sample, position);

  const venue = formatVenue({
    name: activeRow.venueName,
    city: activeRow.venueCity,
    state: activeRow.venueState,
  });
  const gameLine = formatGameLine(activeRow.teamSpread, activeRow.gameTotal);

  const ranked = ratings.filter((rating) => rating.rankVsPosition !== null);
  // THE FIELD SIZE FALLS BACK TO THE LARGEST RANK SEEN. Entering week 1 nothing
  // is rated yet, but a topped-up sample's games from last season carry real
  // ranks — and thirds of zero defenses put every one of them in "Soft".
  const fieldSize =
    ranked.length > 0 ? ranked.length : Math.max(0, ...ranksByGame.values());
  const bands = rankBands(fieldSize);
  const byRank = rankSplits(sample, ranksByGame, bands);
  const opponentRating = ratings.find(
    (rating) => rating.defenseTeamId === activeRow.opponentTeamId,
  );

  return (
    <Shell sport={sport}>
      <Link
        // The board this player was on: same league, same week. It was built
        // from season and week alone, so leaving an NFL player landed on an
        // EMPTY college board (reported from the live site 2026-09-11).
        href={scopedHref(BOARD_PATH, {
          sport: activeRow.sport,
          season: active.season,
          week: active.week,
        })}
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
          <EvidencePill
            priorWeight={activeRow.priorWeight}
            effectiveSample={activeRow.effectiveSample}
          />
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

        {/*
          The venue reads as its own line here rather than as another chip in
          the row above, which already carries both teams, the kickoff, the week
          and the conference. Not truncated: this page has the width the card
          does not.
        */}
        {/*
          Venue and game line share a line here. The card splits them because
          it truncates; this page has the width, and they are both answers to
          "what game is this".
        */}
        {venue || gameLine ? (
          <p className="text-dim text-xs">
            {venue}
            {venue && gameLine ? " · " : ""}
            {gameLine ? (
              <span
                className="tabular-nums"
                title={
                  "Game spread from " +
                  activeRow.teamSchool +
                  "'s perspective, and the game total. Context for the prop, not a model output."
                }
              >
                {gameLine}
              </span>
            ) : null}
          </p>
        ) : null}
      </header>

      <GameTabs games={games} activeGameId={activeGameId} params={resolved} />

      <MarketTabs rows={ordered} activeKey={activeRow.marketKey} params={resolved} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {/* `min-w-0` because a grid child defaults to `min-width: auto`, which
            means "never shrink below your content". The Recharts container and
            the panel header both then pushed the column past the viewport —
            measured at 76px of horizontal overflow on a 390px phone. */}
        <div className="flex min-w-0 flex-col gap-4">
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
                points={sample.slice(0, CHART_GAMES).map((game) => ({
                  gameId: game.gameId,
                  season: game.season,
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
                step={activeMarket?.ladderStep ?? null}
                season={active.season}
              />
            )}

            <LastFive
              summary={
                sample.length > 0
                  ? hitRate(sample, config.hitRateWindows[0] ?? 5)
                  : null
              }
              side={gradeSide}
              window={config.hitRateWindows[0] ?? 5}
              verb={activeMarket?.isBinary ? "scored" : undefined}
              season={active.season}
            />
          </section>

          <section className="panel flex flex-col gap-3 p-4">
            <h2 className="section-header">Hit-rate splits</h2>
            {sample.length === 0 ? (
              <p className="text-dim text-xs">
                Nothing to split until this market has a line.
              </p>
            ) : (
              <>
                {borrowed > 0 ? (
                  <p className="text-dim text-[0.625rem]">
                    {borrowedNote(sample.length - borrowed, borrowed, active)}
                  </p>
                ) : null}

                <div className="flex flex-col gap-1.5">
                  <span className="label-caption">Recent form</span>
                  <SplitGrid splits={windowSplits(sample, config.hitRateWindows)} />
                </div>

                <div className="flex flex-col gap-1.5">
                  <span className="label-caption">Venue</span>
                  <SplitGrid
                    splits={venueSplits(sample)}
                    note={
                      activeRow.sport === "cfb"
                        ? "Secondary in college football — neutral sites are common and schedules are uneven."
                        : undefined
                    }
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
                      (ranked.length > 0
                        ? `Bands are thirds of the ${ranked.length} defenses rated vs ${position}, `
                        : `No defense is rated vs ${position} entering this week yet, so bands are thirds of ranks 1–${fieldSize}, the largest a past opponent here held, `) +
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
              games={sample}
              unit={activeMarket?.unit ?? null}
              rankByGameId={ranksByGame}
              season={active.season}
            />
            {sample.length === 0 && gameLog.length > 0 ? (
              <p className="text-dim text-xs">
                {gameLog.length} completed game
                {gameLog.length === 1 ? "" : "s"} on record, but this market has
                no line to grade them against.
              </p>
            ) : null}
          </section>
        </div>

        {/* `min-w-0` because a grid child defaults to `min-width: auto`, which
            means "never shrink below your content". The Recharts container and
            the panel header both then pushed the column past the viewport —
            measured at 76px of horizontal overflow on a 390px phone. */}
        <div className="flex min-w-0 flex-col gap-4">
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

          <WeatherPanel conditions={conditions} />

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
                once per week by <code className="font-mono">generate_ai_reads</code>{" "}
                and cached — the page never calls a model. The job exits without
                writing while <code className="font-mono">ai_adapter</code> is{" "}
                <code className="font-mono">none</code>, which is a supported
                configuration, not a failure.
              </p>
            )}
          </section>

          <Odds
            row={activeRow}
            quotes={quotes.filter((q) => q.marketKey === activeRow.marketKey)}
          />

          <Projection row={activeRow} market={activeMarket} />

          {/* Below the range, for the same reason the range is below the call:
              both are the distribution being interrogated, not the claim. A
              binary market has no ladder — anytime TD is one probability, and a
              ladder of it would be that number repeated. */}
          <LadderPanel
            ladder={activeRow.ladder}
            line={activeRow.line}
            median={activeRow.projectedMedian}
            unit={activeMarket?.unit ?? null}
          />
        </div>
      </div>
    </Shell>
  );
}

/**
 * Each past opponent's rank as it stood entering THAT game's week.
 *
 * Keyed by game id so the log and the splits agree cell for cell.
 *
 * ASKED ONCE PER SEASON. A topped-up NFL sample holds last season's games too,
 * and a week number alone names two different cutoffs: week 12 of last season
 * is not week 12 of this one, and looking it up in this season would pin a 2025
 * game to a 2026 rank.
 */
async function rankLookup(
  graded: GradedGame[],
  position: PositionGroup,
): Promise<Map<number, number>> {
  const bySeason = new Map<number, GradedGame[]>();
  for (const game of graded) {
    const list = bySeason.get(game.season) ?? [];
    list.push(game);
    bySeason.set(game.season, list);
  }

  const byGame = new Map<number, number>();
  await Promise.all(
    [...bySeason].map(async ([season, games]) => {
      const ranks = await getDefenseRanksAt(
        season,
        position,
        games.map((game) => ({
          defenseTeamId: game.opponentTeamId,
          week: game.week,
        })),
      );
      for (const game of games) {
        const rank = ranks.get(rankKey(game.opponentTeamId, game.week));
        if (rank !== undefined) byGame.set(game.gameId, rank);
      }
    }),
  );
  return byGame;
}

/**
 * Says why the splits hold last season's games, and that it is temporary.
 *
 * One string rather than JSX with expressions, because JSX drops the space
 * after a leading expression and this sentence has four of them.
 */
function borrowedNote(
  current: number,
  borrowed: number,
  active: { season: number; week: number },
): string {
  const thisSeason =
    current === 0
      ? "No games yet"
      : `Only ${current} game${current === 1 ? "" : "s"}`;
  return (
    `${thisSeason} this season before week ${active.week}, so these splits ` +
    `include the ${borrowed} most recent from ${active.season - 1}. They drop ` +
    `out as this season's games replace them.`
  );
}

/**
 * The player's name, or a 404 if no such player exists.
 *
 * ONLY CALLED ON PATHS THAT END IN A MESSAGE. An id nobody has heard of is a
 * 404; an id with no rows this week is not — that is a real player on a bye, or
 * below the usage floor, and it deserves an explanation under his own name. The
 * distinction needs `players`, so it costs a round trip, and it is paid on the
 * paths that need it rather than on every page view.
 */
async function nameOr404(playerId: number): Promise<string> {
  const identity = await getPlayerIdentity(playerId);
  if (!identity) notFound();
  return identity.name;
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
          — no book posted this one. It is the player&rsquo;s trailing average
          priced at −110/−110, which de-vigs to exactly 0.500 and makes the edge
          a restatement of confidence rather than a disagreement with a market.
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
            p10 to p90 of the projected distribution, floored at zero — some
            markets are fitted with a family whose left tail runs below a value
            the stat can take. The call above is the share of that distribution
            past the line, which is the claim being made; the median is context.
          </p>
        </>
      )}

      <Evidence row={row} />
    </section>
  );
}

/**
 * How much the model knows about this player, stated in full.
 *
 * THE BOARD SHOWS ONE OF THESE NUMBERS AND THIS SHOWS BOTH, because this is
 * where there is room to explain the second. `prior_weight` read alone inverts
 * in the opening weeks — a transfer's prior is discounted, so he carries a
 * SMALLER share of it than a returning starter who has played exactly as many
 * games this season, which is none. The card therefore shows the effective
 * sample, and the share appears here beside the sentence that keeps it honest.
 */
function Evidence({ row }: { row: BoardRow }) {
  const evidence = evidenceFor(row);
  if (!evidence) return null;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="label-caption">Evidence</span>
        <span className="text-muted text-xs tabular-nums">
          {evidence.games.toFixed(1)} effective games ·{" "}
          {Math.round(evidence.priorShare * 100)}% carried by last season
        </span>
      </div>
      <p className="text-dim text-[0.625rem]">
        Effective games are this season&rsquo;s, plus last season&rsquo;s
        discounted to the weight above. That weight shrinks as the season
        accumulates, and starts lower for a player who changed team — transfers
        and NIL mean prior-year production often happened somewhere else, so a
        small share here can mean the prior was discounted rather than that this
        season has taken over.
        {evidence.isThin
          ? " Under four effective games, the projection is doing more extrapolating than measuring."
          : ""}
      </p>
    </div>
  );
}

function NotOnSlate({
  name,
  reason,
  sport,
  season,
  week,
}: {
  name: string;
  reason: string;
  sport: Sport;
  season?: number;
  week?: number;
}) {
  return (
    <div className="panel flex flex-col gap-2 p-6">
      <h1 className="text-xl font-extrabold tracking-tight">{name}</h1>
      <p className="text-muted max-w-prose text-sm">{reason}</p>
      <Link
        // Straight to the board, in this league. It pointed at `/` with season
        // and week only, which reached the board through the legacy-link
        // redirect and lost the sport on the way.
        href={scopedHref(BOARD_PATH, { sport, season, week })}
        className="text-accent-cyan w-fit text-sm hover:underline"
      >
        Back to the board
      </Link>
    </div>
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
      <SiteHeader sport={sport} />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}
