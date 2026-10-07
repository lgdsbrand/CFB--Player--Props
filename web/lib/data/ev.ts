import { createServerSupabaseClient } from "@/lib/supabase/server";
import { type DbRow, unwrap } from "@/lib/data/query";
import type { EvSide, EvWager } from "@/lib/core/ev";
import type { MarketRole, OddsMarket } from "@/lib/core/game-lines";

/**
 * Full-game +EV wagers for these games, at or above `minEv` (migration 0087).
 *
 * Small by construction: the capture stores positive EV only, a few hundred
 * rows across a whole slate and a handful above 2%. No sport filter is
 * needed: the caller passes game ids, which belong to one sport.
 *
 * The caller decides which games are still to kick off. A game's rows are
 * left as its last capture before kickoff saw them, so a started game's list
 * is history, not an offer.
 */
export async function getEvWagers(gameIds: number[], minEv = 0): Promise<EvWager[]> {
  if (gameIds.length === 0) return [];
  const supabase = createServerSupabaseClient();
  const rows = unwrap<DbRow[]>(
    await supabase
      .from("game_ev_wagers")
      .select(
        "game_id, market, side, line, price, fair_prob, ev, captured_at, " +
          "book:sportsbooks!sportsbook_id(key, display_name, market_role)",
      )
      .eq("period", "full")
      .in("game_id", gameIds)
      .gte("ev", minEv)
      .order("ev", { ascending: false })
      .limit(1000),
    "game_ev_wagers",
  );
  return rows.map((r) => {
    const book = r.book as { key: string; display_name: string; market_role: MarketRole } | null;
    return {
      gameId: r.game_id as number,
      market: r.market as OddsMarket,
      side: r.side as EvSide,
      line: r.line === null ? null : Number(r.line),
      price: r.price as number,
      fairProb: Number(r.fair_prob),
      ev: Number(r.ev),
      bookKey: book?.key ?? "",
      bookName: book?.display_name ?? "",
      role: book?.market_role ?? "other",
      capturedAt: r.captured_at as string,
    };
  });
}
