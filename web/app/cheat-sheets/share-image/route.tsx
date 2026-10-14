import { shareSiteHost } from "@/lib/core/site";
import { ImageResponse } from "next/og";
import type { NextRequest } from "next/server";

import { SheetShareImage } from "@/components/share/sheet-share-image";
import { SHARE_HEIGHT, SHARE_WIDTH } from "@/components/share/share-theme";
import { parseBoardParams, type RawParams } from "@/lib/core/board-params";
import {
  CHEAT_WINDOWS,
  DEFAULT_CHEAT_WINDOW,
  cheatSheetMarkets,
  resolveCheatTier,
  rowsForGames,
  tierBand,
} from "@/lib/core/cheat-sheet";
import { isSupabaseConfigured } from "@/lib/core/env";
import { kickoffCutoff } from "@/lib/core/kickoff";
import { SHARE_SPORT_LABEL } from "@/lib/core/share-game";
import { shareSheetView } from "@/lib/core/share-sheet";
import { resolveSport } from "@/lib/core/sport";
import { getMarkets } from "@/lib/data/catalogue";
import { getCheatSheet } from "@/lib/data/cheat-sheet";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";
import { shareFonts } from "@/lib/og/fonts";

/**
 * The cheat sheet as a PNG, for the URL the page is on: same week, window,
 * tier, position, market, side and games. The filters are read exactly as
 * `app/cheat-sheets/page.tsx` reads them (the board's parser, the same tier,
 * market and window rules); change both together.
 */
export async function GET(request: NextRequest) {
  if (!isSupabaseConfigured()) return new Response("Not configured", { status: 503 });

  const raw: RawParams = Object.fromEntries(request.nextUrl.searchParams.entries());
  const sport = resolveSport(raw.sport);
  const params = parseBoardParams(raw, { edgesOnlyDefault: false });
  const [weeks, markets, fonts] = await Promise.all([getSlateWeeks(sport), getMarkets(sport), shareFonts()]);
  const active = findWeek(weeks, params.season, params.week);
  if (!active) return new Response("No slate", { status: 404 });

  const tier = resolveCheatTier(typeof raw.tier === "string" ? raw.tier : undefined);
  const band = tier ? tierBand(tier) : undefined;
  const marketOptions = cheatSheetMarkets(markets, params.position);
  const market = marketOptions.find((m) => m.key === params.market);
  const windowSize = CHEAT_WINDOWS.includes(params.hitRateWindow) ? params.hitRateWindow : DEFAULT_CHEAT_WINDOW;

  const filters = {
    sport,
    season: active.season,
    week: active.week,
    windowSize,
    positionGroup: params.position,
    side: params.side,
    marketKey: market?.key,
    minHitRate: band?.min,
    belowHitRate: band?.below,
    kickoffCutoff: kickoffCutoff(),
  };
  // One retry: a cold read of the grading view can pass the anonymous 3 s
  // timeout and succeed a moment later (seen 2026-10-12), as the builder found.
  const whole = await getCheatSheet(filters).catch(() => getCheatSheet(filters));
  let rows = whole.rows;
  if (params.games) {
    rows =
      rowsForGames(whole.rows, params.games, whole.truncated) ??
      (await getCheatSheet({ ...filters, gameIds: params.games })).rows;
  }

  const view = shareSheetView({
    rows,
    sportLabel: SHARE_SPORT_LABEL[sport] ?? sport.toUpperCase(),
    week: active.week,
    windowSize,
    tier,
    position: params.position,
    marketLabel: market ? (market.shortLabel ?? market.displayName) : undefined,
    side: params.side,
  });

  return new ImageResponse(<SheetShareImage view={view} site={shareSiteHost(request.nextUrl.host)} />, {
    width: SHARE_WIDTH,
    height: SHARE_HEIGHT,
    fonts: fonts.length > 0 ? fonts : undefined,
    emoji: "twemoji",
    headers: {
      "Cache-Control": "public, max-age=0, s-maxage=300, stale-while-revalidate=600",
      "Content-Disposition": `inline; filename="cheat-sheet-week-${active.week}.png"`,
    },
  });
}
