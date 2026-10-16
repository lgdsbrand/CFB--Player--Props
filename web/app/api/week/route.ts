import { NextResponse, type NextRequest } from "next/server";

import { isSupabaseConfigured } from "@/lib/core/env";
import { resolveSport } from "@/lib/core/sport";
import { feedGame, type TeamKeys } from "@/lib/core/week-feed";
import { getLiveScores } from "@/lib/data/live";
import { findWeek, getSlateGames, getSlateWeeks } from "@/lib/data/slate";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/**
 * The current week's games for the strip at the top of the client's CFB and
 * NFL pages (lib/core/week-feed.ts). Same week as /games opens on, kickoff
 * order, live scores while games are on. Same-origin for him through his
 * /football rewrite. Cached a minute at the edge so live scores stay close.
 */
export async function GET(request: NextRequest) {
  if (!isSupabaseConfigured()) {
    return NextResponse.json({ error: "not configured" }, { status: 503 });
  }
  const sport = resolveSport(request.nextUrl.searchParams.get("sport") ?? undefined);
  try {
    const active = findWeek(await getSlateWeeks(sport), undefined, undefined);
    if (!active) {
      return NextResponse.json({ sport, season: null, week: null, games: [] });
    }
    const games = await getSlateGames(active.season, active.week, sport);
    const teamIds = [...new Set(games.flatMap((g) => [g.homeTeamId, g.awayTeamId]))];
    const supabase = createServerSupabaseClient();
    const [{ data: teamRows, error }, live] = await Promise.all([
      supabase.from("teams").select("id, cfbd_id, abbreviation").in("id", teamIds),
      getLiveScores(games.map((g) => g.gameId)),
    ]);
    if (error) throw new Error(error.message);
    const teams: TeamKeys = new Map(
      (teamRows ?? []).map((t) => [
        t.id as number,
        { cfbdId: (t.cfbd_id as number | null) ?? null, abbreviation: (t.abbreviation as string | null) ?? null },
      ]),
    );
    const liveById = new Map(live.map((s) => [s.gameId, s]));
    return NextResponse.json(
      {
        sport,
        season: active.season,
        week: active.week,
        games: games.map((g) => feedGame(sport, g, liveById.get(g.gameId), teams)),
      },
      { headers: { "Cache-Control": "public, s-maxage=60, stale-while-revalidate=300" } },
    );
  } catch (err) {
    console.error("week feed failed", err);
    return NextResponse.json({ error: "read failed" }, { status: 503 });
  }
}
