import { NextResponse } from "next/server";

import { isSupabaseConfigured } from "@/lib/core/env";
import { slateDayKey } from "@/lib/core/slate-days";
import { SPORTS } from "@/lib/core/sport";
import { publicEngines, yesterdayResults } from "@/lib/core/yesterday-results";
import { getTrackerPicks } from "@/lib/data/tracker";

/**
 * Yesterday's graded football picks per sport, for the client's home page
 * ("Yesterday's Results"). He reads it at /football/api/yesterday on his own
 * domain, so the request is same-origin. What it holds and why is in
 * lib/core/yesterday-results.ts.
 *
 * A sport with nothing graded yesterday is absent from `sports`. Cached at the
 * edge for ten minutes (the tracker read under it is cached five); a failed
 * read answers 503 and his ribbon simply leaves football out.
 */
export async function GET() {
  if (!isSupabaseConfigured()) {
    return NextResponse.json({ error: "not configured" }, { status: 503 });
  }
  const now = new Date();
  try {
    const entries = await Promise.all(
      SPORTS.map(async (sport) => {
        const picks = await getTrackerPicks(sport, publicEngines(sport));
        return [sport, yesterdayResults(sport, picks, now)] as const;
      }),
    );
    return NextResponse.json(
      {
        day: slateDayKey(new Date(now.getTime() - 86_400_000).toISOString()),
        sports: Object.fromEntries(entries.filter(([, results]) => results !== null)),
      },
      { headers: { "Cache-Control": "public, s-maxage=600, stale-while-revalidate=1800" } },
    );
  } catch {
    return NextResponse.json({ error: "read failed" }, { status: 503 });
  }
}
