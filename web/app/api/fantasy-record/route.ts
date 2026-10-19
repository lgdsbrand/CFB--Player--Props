import { NextResponse } from "next/server";

import { isSupabaseConfigured } from "@/lib/core/env";
import { fantasyRecordFeed } from "@/lib/core/fantasy-record";
import { getTrackerPicks } from "@/lib/data/tracker";

/**
 * The fantasy model's record for the client's NFL Records tab
 * (lib/core/fantasy-record.ts). Same-origin for him through his /football
 * rewrite. Picks are listed only once their game has kicked off
 * (`v_tracker_picks`), so nothing here shows a pick still to play.
 *
 * Cached five minutes at the edge: a pick is graded the morning after, so a
 * fresher read buys nothing, and his tab must never wait on this database. A
 * failed read answers 503 and his tab leaves the section out.
 */
export async function GET() {
  if (!isSupabaseConfigured()) {
    return NextResponse.json({ error: "not configured" }, { status: 503 });
  }
  try {
    const picks = await getTrackerPicks("nfl", ["fantasy"]);
    return NextResponse.json(fantasyRecordFeed(picks), {
      headers: { "Cache-Control": "public, s-maxage=300, stale-while-revalidate=600" },
    });
  } catch (err) {
    console.error("fantasy record feed failed", err);
    return NextResponse.json({ error: "read failed" }, { status: 503 });
  }
}
