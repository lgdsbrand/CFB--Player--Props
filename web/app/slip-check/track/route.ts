import { NextResponse, type NextRequest } from "next/server";

import { isSupabaseConfigured } from "@/lib/core/env";
import { decodeSlipKeys, parseLegKey, type LegRef } from "@/lib/core/slip-check";
import { TRACK_BATCH } from "@/lib/core/slip-track";
import { getTrackFacts } from "@/lib/data/slip-track";

/**
 * The scores and box-score stats for saved slips' legs, as JSON
 * (`lib/core/slip-track.ts` turns them into statuses in the browser). The
 * request carries leg keys only; nothing about the reader is sent or kept.
 */
export async function GET(request: NextRequest) {
  if (!isSupabaseConfigured()) {
    return NextResponse.json({ error: "not configured" }, { status: 503 });
  }
  const keys = decodeSlipKeys(request.nextUrl.searchParams.get("legs"), TRACK_BATCH);
  if (keys.length === 0) return NextResponse.json({ error: "legs are required" }, { status: 400 });
  const refs = keys.map(parseLegKey).filter((r): r is LegRef => r !== null);
  const facts = await getTrackFacts(refs, new Date());
  // The scoreboard is rewritten every two minutes; a minute at the edge is plenty fresh.
  return NextResponse.json(facts, {
    headers: { "Cache-Control": "public, s-maxage=60, stale-while-revalidate=120" },
  });
}
