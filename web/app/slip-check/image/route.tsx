import { shareSiteHost } from "@/lib/core/site";
import { ImageResponse } from "next/og";
import type { NextRequest } from "next/server";

import { SlipCheckImage } from "@/components/share/slip-check-image";
import { SHARE_HEIGHT, SHARE_WIDTH } from "@/components/share/share-theme";
import { isSupabaseConfigured } from "@/lib/core/env";
import { checkSlip, decodeSlipKeys, indexCatalogue, parseLegKey } from "@/lib/core/slip-check";
import { resolveSport, SPORT_LABEL } from "@/lib/core/sport";
import { getGame } from "@/lib/data/games";
import { getCheckCatalogue } from "@/lib/data/slip-check";
import { shareFonts } from "@/lib/og/fonts";

/**
 * A checked slip as a PNG. The URL carries only leg KEYS and the book; every
 * number is recomputed here from the week's catalogue, so a hand-edited URL
 * cannot put figures under our name that the model did not produce. Legs that
 * are no longer offered (a game that has kicked off) are left out.
 */
export async function GET(request: NextRequest) {
  if (!isSupabaseConfigured()) return new Response("Not configured", { status: 503 });
  const params = request.nextUrl.searchParams;
  const book = params.get("book") ?? "";
  const keys = decodeSlipKeys(params.get("legs"));
  if (!/^[a-z0-9_]+$/.test(book) || keys.length === 0) {
    return new Response("book and legs are required", { status: 400 });
  }
  // The bet slip drawer does not know the week or the league it is on, so
  // without them they come from the first leg's game.
  let season = Number.parseInt(params.get("season") ?? "", 10);
  let week = Number.parseInt(params.get("week") ?? "", 10);
  let sport = resolveSport(params.get("sport") ?? undefined);
  if (!Number.isFinite(season) || !Number.isFinite(week)) {
    const game = await getGame(parseLegKey(keys[0])!.gameId);
    if (!game) return new Response("Unknown game", { status: 404 });
    ({ season, week, sport } = game);
  }

  const [catalogue, fonts] = await Promise.all([
    // One retry, for a cold read past the anonymous 3 s timeout.
    getCheckCatalogue({ sport, season, week, now: new Date() }).catch(() =>
      getCheckCatalogue({ sport, season, week, now: new Date() }),
    ),
    shareFonts(),
  ]);
  const index = indexCatalogue(catalogue.entries);
  const legs = keys.flatMap((key) => {
    const found = index.get(key);
    return found ? [found.entry.sides[found.index].leg] : [];
  });
  if (legs.length === 0) return new Response("None of these legs is still offered", { status: 404 });
  const bookName = catalogue.books.find((b) => b.key === book)?.name ?? book;

  return new ImageResponse(
    <SlipCheckImage
      check={checkSlip(legs, index, book)}
      bookName={bookName}
      heading={`${SPORT_LABEL[sport].toUpperCase()} · WEEK ${week}`}
      site={shareSiteHost(new URL(request.url).host)}
    />,
    {
      width: SHARE_WIDTH,
      height: SHARE_HEIGHT,
      fonts: fonts.length > 0 ? fonts : undefined,
      emoji: "twemoji",
      headers: {
        "Cache-Control": "public, max-age=0, s-maxage=120, stale-while-revalidate=600",
        "Content-Disposition": 'inline; filename="slip-check.png"',
      },
    },
  );
}
