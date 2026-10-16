import { NextResponse } from "next/server";

import { isSupabaseConfigured } from "@/lib/core/env";
import { slateDayKey, slateDayWindow } from "@/lib/core/slate-days";
import { SPORTS } from "@/lib/core/sport";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/**
 * Today's game count per football sport, for the client's home page (his
 * "Live Engines" strip, which shows only the sports playing today). He reads it
 * at /football/api/today on his own domain, so the request is same-origin.
 *
 * "Today" is the board's slate day: Eastern time, rolling over at 4 a.m., so
 * a late kickoff still counts on the day it is played. Games already finished
 * today count too: the strip says what is on today, not what is left.
 *
 * Cached at the edge for five minutes. His page must never wait on this
 * database; a failed read answers 503 and his strip simply leaves football out.
 */
export async function GET() {
  if (!isSupabaseConfigured()) {
    return NextResponse.json({ error: "not configured" }, { status: 503 });
  }
  const { start, end } = slateDayWindow(new Date());
  const supabase = createServerSupabaseClient();
  const counts = await Promise.all(
    SPORTS.map(async (sport) => {
      const { count, error } = await supabase
        .from("games")
        .select("id", { count: "exact", head: true })
        .eq("sport", sport)
        .gte("start_date", start.toISOString())
        .lt("start_date", end.toISOString());
      return error ? null : ([sport, count ?? 0] as const);
    }),
  );
  if (counts.some((c) => c === null)) {
    return NextResponse.json({ error: "read failed" }, { status: 503 });
  }
  return NextResponse.json(
    {
      day: slateDayKey(start.toISOString()),
      games: Object.fromEntries(counts as (readonly [string, number])[]),
    },
    { headers: { "Cache-Control": "public, s-maxage=300, stale-while-revalidate=600" } },
  );
}
