import { NextResponse, type NextRequest } from "next/server";

import { isSupabaseConfigured } from "@/lib/core/env";
import { resolveSport } from "@/lib/core/sport";
import { getCheckCatalogue } from "@/lib/data/slip-check";

/**
 * The slip checker's catalogue as JSON, fetched by `/slip-check` once per
 * week. A route rather than page props for the same reason as `/builder/pool`:
 * a few thousand priced bets stay out of the HTML and are cached at the edge.
 */
export async function GET(request: NextRequest) {
  if (!isSupabaseConfigured()) {
    return NextResponse.json({ error: "not configured" }, { status: 503 });
  }
  const params = request.nextUrl.searchParams;
  const season = Number.parseInt(params.get("season") ?? "", 10);
  const week = Number.parseInt(params.get("week") ?? "", 10);
  if (!Number.isFinite(season) || !Number.isFinite(week)) {
    return NextResponse.json({ error: "season and week are required" }, { status: 400 });
  }
  const catalogue = await getCheckCatalogue({
    sport: resolveSport(params.get("sport") ?? undefined),
    season,
    week,
    now: new Date(),
  });
  return NextResponse.json(catalogue, {
    headers: { "Cache-Control": "public, s-maxage=120, stale-while-revalidate=600" },
  });
}
