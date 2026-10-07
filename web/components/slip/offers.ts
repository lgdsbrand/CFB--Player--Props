"use client";

import type { MarketRole } from "@/lib/core/game-lines";
import type { Offer, SlipLeg } from "@/lib/core/slip";
import { getBrowserSupabaseClient } from "@/lib/supabase/client";

const COLUMNS =
  "line, price, link, event_link, captured_at, " +
  "book:sportsbooks!sportsbook_id(key, display_name, market_role)";

/**
 * Every book's current offer on one leg's side, at any line (migration 0089).
 *
 * ONE QUERY PER LEG, from the browser. Each is a primary-key prefix (game,
 * then player and market, or period and market) and returns a few dozen rows
 * at most, so a dozen legs stay far from the 1,000-row cap and the anon
 * timeout, which one query for all of them would not.
 */
export async function fetchLegOffers(leg: SlipLeg): Promise<Offer[]> {
  const supabase = getBrowserSupabaseClient();
  const query =
    leg.kind === "prop"
      ? supabase
          .from("prop_offers")
          .select(COLUMNS)
          .eq("game_id", leg.gameId)
          .eq("player_id", leg.playerId)
          .eq("market_key", leg.marketKey)
          .eq("side", leg.side)
      : supabase
          .from("game_offers")
          .select(COLUMNS)
          .eq("game_id", leg.gameId)
          .eq("period", leg.period)
          .eq("market", leg.market)
          .eq("side", leg.side);
  const { data, error } = await query;
  if (error) throw new Error(error.message);
  const offers = ((data ?? []) as Record<string, unknown>[]).map(toOffer);
  return offers.length > 0 || leg.kind !== "prop" ? offers : latestPropPrices(leg);
}

/**
 * A prop's newest captured prices WITHOUT links, for a game no capture has
 * asked for links yet. Props are bought when first posted and again in the
 * hour before kickoff, so a game first captured before links were asked for
 * (2026-10-07) has prices here and offers only from its closing capture.
 */
async function latestPropPrices(leg: Extract<SlipLeg, { kind: "prop" }>): Promise<Offer[]> {
  const { data, error } = await getBrowserSupabaseClient()
    .from("v_latest_prop_lines")
    .select("sportsbook_key, sportsbook_name, line, over_price, under_price, captured_at")
    .eq("game_id", leg.gameId)
    .eq("player_id", leg.playerId)
    .eq("market_key", leg.marketKey);
  if (error) throw new Error(error.message);
  return ((data ?? []) as Record<string, unknown>[]).flatMap((row) => {
    const price = row[leg.side === "over" ? "over_price" : "under_price"];
    if (price === null || price === undefined) return [];
    return [
      {
        bookKey: row.sportsbook_key as string,
        bookName: row.sportsbook_name as string,
        role: "retail" as const,
        line: Number(row.line),
        price: Number(price),
        link: null,
        eventLink: null,
        capturedAt: row.captured_at as string,
      },
    ];
  });
}

function toOffer(row: Record<string, unknown>): Offer {
  const book = row.book as { key: string; display_name: string; market_role: MarketRole };
  return {
    bookKey: book.key,
    bookName: book.display_name,
    role: book.market_role,
    line: row.line === null ? null : Number(row.line),
    price: Number(row.price),
    link: (row.link as string | null) ?? null,
    eventLink: (row.event_link as string | null) ?? null,
    capturedAt: row.captured_at as string,
  };
}
