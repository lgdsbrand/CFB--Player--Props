import { NextResponse, type NextRequest } from "next/server";

import { CHEAT_WINDOWS, DEFAULT_CHEAT_WINDOW } from "@/lib/core/cheat-sheet";
import { isSupabaseConfigured } from "@/lib/core/env";
import { kickoffCutoff } from "@/lib/core/kickoff";
import { resolveSport } from "@/lib/core/sport";
import { getBuilderPool } from "@/lib/data/builder";

/**
 * The bet builder's candidate pool as JSON, fetched by `/builder` once per
 * week and window. A route rather than page props so ~2,000 candidates stay
 * out of the HTML (see the 5.56 MB `/no-vig` page) and can be cached at the
 * edge: prices change with captures, which run hourly at most.
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
  const requested = Number.parseInt(params.get("window") ?? "", 10);
  const windowSize = CHEAT_WINDOWS.includes(requested) ? requested : DEFAULT_CHEAT_WINDOW;

  const pool = await getBuilderPool({
    sport: resolveSport(params.get("sport") ?? undefined),
    season,
    week,
    windowSize,
    kickoffCutoff: kickoffCutoff(),
  });
  return NextResponse.json(pool, {
    headers: { "Cache-Control": "public, s-maxage=120, stale-while-revalidate=600" },
  });
}
