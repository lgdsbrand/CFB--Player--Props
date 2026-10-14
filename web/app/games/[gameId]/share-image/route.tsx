import { shareSiteHost } from "@/lib/core/site";
import { ImageResponse } from "next/og";

import { GameShareImage, SHARE_HEIGHT, SHARE_WIDTH } from "@/components/share/game-share-image";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatShareKickoff, shareGameView } from "@/lib/core/share-game";
import { getGameBookOdds, getGameProjections } from "@/lib/data/game-odds";
import { getGame } from "@/lib/data/games";
import { getGameRulePlays } from "@/lib/data/rule-plays";
import { getGameConditions } from "@/lib/data/weather";
import { shareFonts } from "@/lib/og/fonts";

/**
 * One game as a PNG to post (client, 2026-10-12). What it says is decided in
 * `lib/core/share-game.ts`; this reads the same rows the game page reads and
 * renders them.
 *
 * Cached for five minutes at the edge: prices move, but a share button pressed
 * twenty times by one group chat should not be twenty renders.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ gameId: string }> },
) {
  if (!isSupabaseConfigured()) return new Response("Not configured", { status: 503 });

  const gameId = Number.parseInt((await params).gameId, 10);
  if (!Number.isFinite(gameId)) return new Response("Not found", { status: 404 });
  const game = await getGame(gameId);
  if (!game) return new Response("Not found", { status: 404 });

  const [projections, plays, odds, conditions, fonts] = await Promise.all([
    getGameProjections([gameId]),
    getGameRulePlays(gameId),
    getGameBookOdds(gameId),
    getGameConditions(gameId),
    shareFonts(),
  ]);

  const view = shareGameView({
    game,
    kickoff: formatShareKickoff(game.startDate, game.startTimeTbd),
    projection: projections.get(gameId) ?? null,
    plays,
    odds,
    conditions,
  });

  return new ImageResponse(<GameShareImage view={view} site={shareSiteHost(new URL(request.url).host)} />, {
    width: SHARE_WIDTH,
    height: SHARE_HEIGHT,
    fonts: fonts.length > 0 ? fonts : undefined,
    emoji: "twemoji",
    headers: {
      "Cache-Control": "public, max-age=0, s-maxage=300, stale-while-revalidate=600",
      "Content-Disposition": `inline; filename="${view.fileName}"`,
    },
  });
}
