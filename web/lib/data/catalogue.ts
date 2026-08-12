/**
 * The market catalogue and the conference list.
 *
 * Both drive UI controls directly from data: `market_positions` decides which
 * stat selector options a position tab offers, and `conferences.is_displayed`
 * decides which conferences the filter lists. Neither is a hardcoded array,
 * which is what keeps the UI from offering a market the model does not produce
 * and what makes post-realignment changes a row edit (CLAUDE.md §4, §7).
 */

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { DEFAULT_SPORT } from "@/lib/core/sport";
import type { Conference, Market, PositionGroup } from "@/lib/core/types";
import { type DbRow, unwrap } from "@/lib/data/query";
import { cachedRead } from "@/lib/data/cache";

type MarketPositionRow = {
  market_key: string;
  position_group: string;
  sort_order: number;
};

/**
 * Active markets with their positions attached, in display order.
 *
 * One round trip per table rather than a nested select: PostgREST can embed the
 * join, but the shape it returns for a composite-key child table is awkward to
 * type by hand, and two small reads of nine and seventeen rows are not worth
 * the cleverness.
 */
async function readMarkets(): Promise<Market[]> {
  const supabase = createServerSupabaseClient();

  const [markets, positions] = await Promise.all([
    supabase
      .from("markets")
      .select(
        "key, display_name, short_label, emoji, stat_column, is_binary, " +
          "default_line, unit, sort_order",
      )
      .eq("is_active", true)
      .order("sort_order"),
    supabase
      .from("market_positions")
      .select("market_key, position_group, sort_order")
      .order("sort_order"),
  ]);

  const marketRows = unwrap<DbRow[]>(markets, "markets");
  const positionRows = unwrap<MarketPositionRow[]>(
    positions,
    "market_positions",
  );

  const byMarket = new Map<string, PositionGroup[]>();
  for (const row of positionRows) {
    const list = byMarket.get(row.market_key) ?? [];
    list.push(row.position_group as PositionGroup);
    byMarket.set(row.market_key, list);
  }

  return marketRows.map((row) => ({
    key: row.key as string,
    displayName: row.display_name as string,
    shortLabel: row.short_label as string | null,
    emoji: row.emoji as string | null,
    statColumn: row.stat_column as string,
    isBinary: row.is_binary as boolean,
    defaultLine: row.default_line as number | null,
    unit: row.unit as string | null,
    sortOrder: row.sort_order as number,
    positions: byMarket.get(row.key as string) ?? [],
  }));
}

/** Markets offered for one position, in the order the selector should list them. */
export function marketsForPosition(
  markets: Market[],
  position: PositionGroup,
): Market[] {
  return markets.filter((market) => market.positions.includes(position));
}

/**
 * Conferences for the filter control.
 *
 * `is_displayed` is a DISPLAY filter and never a data cut — ingest covers all
 * FBS because a cross-conference game cannot be opponent-adjusted with one side
 * missing (CLAUDE.md §4). The seed marks five as displayed; the Pac-12 is
 * deliberately not among them post-realignment, and this returns whatever the
 * table says rather than assuming a fixed six.
 *
 * Scoped by sport (migration 0035) because conference names are only unique
 * within one: the AFC and the SEC in the same filter would be a nonsense
 * control, and a name shared across two sports would collapse two rows into one
 * option.
 */
async function readConferences(
  { displayedOnly = true }: { displayedOnly?: boolean } = {},
): Promise<Conference[]> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("conferences")
    .select("id, name, abbreviation, is_displayed")
    .eq("sport", DEFAULT_SPORT)
    .order("name");

  if (displayedOnly) query = query.eq("is_displayed", true);

  return unwrap<DbRow[]>(await query, "conferences").map((row) => ({
    id: row.id as number,
    name: row.name as string,
    abbreviation: row.abbreviation as string | null,
    isDisplayed: row.is_displayed as boolean,
  }));
}

/**
 * The market catalogue and the conference list, cached.
 *
 * Both are seed data that a migration changes and nothing else does, and both
 * sit in the board's opening wave alongside the week strip and the config — so
 * caching them removes a whole wait rather than a query. See `lib/data/cache.ts`
 * for why that distinction is the only one that matters here.
 */
export const getMarkets = cachedRead("markets", readMarkets);
export const getConferences = cachedRead("conferences", readConferences);
