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
import { DEFAULT_SPORT, type Sport } from "@/lib/core/sport";
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
async function readMarkets(sport: Sport = DEFAULT_SPORT): Promise<Market[]> {
  const supabase = createServerSupabaseClient();

  const [markets, positions] = await Promise.all([
    supabase
      .from("markets")
      .select(
        "key, display_name, short_label, emoji, stat_column, is_binary, " +
          "default_line, unit, sort_order, ladder_step, sport, " +
          "parent_market_key, publishes_call",
      )
      .eq("is_active", true)
      // A NULL sport means every sport, which is what nine of the twelve rows
      // carry: pass_yards is the same market in both leagues. Only a market
      // that genuinely exists in one league names it, and the first-quarter
      // three do because no college book anywhere posts a quarter market.
      //
      // WITHOUT THIS PREDICATE THE COLLEGE BOARD LISTS Q1 PASS YDS, which is
      // the sport-blind read this repo has now shipped six times. The filter
      // goes in with the column rather than after it.
      .or(`sport.is.null,sport.eq.${sport}`)
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

  const built = marketRows.map((row) => ({
    key: row.key as string,
    displayName: row.display_name as string,
    shortLabel: row.short_label as string | null,
    emoji: row.emoji as string | null,
    statColumn: row.stat_column as string,
    isBinary: row.is_binary as boolean,
    defaultLine: row.default_line as number | null,
    unit: row.unit as string | null,
    sortOrder: row.sort_order as number,
    // `numeric` arrives from PostgREST as a string, so this is coerced rather
    // than cast — a cast would typecheck and then do string arithmetic when the
    // stepper added it to a line.
    ladderStep:
      row.ladder_step === null || row.ladder_step === undefined
        ? null
        : Number(row.ladder_step),
    positions: byMarket.get(row.key as string) ?? [],
    parentMarketKey: (row.parent_market_key as string | null) ?? null,
    publishesCall: row.publishes_call as boolean,
  }));

  return inheritParentPositions(built);
}

/**
 * Give every derived market the positions of the market it is derived from.
 *
 * A first-quarter market applies to exactly the positions its parent does —
 * anything a tight end gets for the full game it gets for the first quarter,
 * and nothing else — so it carries no `market_positions` rows of its own and
 * would otherwise appear in no stat selector at all.
 *
 * THIS MIRRORS `projections.first_quarter_catalogue` ON THE WORKER SIDE, which
 * derives the same rows the same way for the same reason: listing the mapping
 * twice would let the Q1 tabs a tight end sees drift from the full-game ones
 * the model actually produces for him.
 *
 * ONE HOP, NOT A WALK. `markets_parent_is_not_self` and migration 0066's guard
 * between them refuse a self-reference and a parent that is itself derived, so
 * a chain cannot form and this cannot loop. A parent missing from the list —
 * possible if it were ever deactivated on its own — leaves the child with no
 * positions, which reads as a market offered to nobody rather than as one
 * silently offered to everybody.
 */
function inheritParentPositions(markets: Market[]): Market[] {
  const byKey = new Map(markets.map((market) => [market.key, market]));

  return markets.map((market) => {
    if (market.parentMarketKey === null) return market;
    const parent = byKey.get(market.parentMarketKey);
    if (!parent || market.positions.length > 0) return market;
    return { ...market, positions: parent.positions };
  });
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
  sport: Sport = DEFAULT_SPORT,
  { displayedOnly = true }: { displayedOnly?: boolean } = {},
): Promise<Conference[]> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("conferences")
    .select("id, name, abbreviation, is_displayed")
    .eq("sport", sport)
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
 *
 * BOTH TAKE A SPORT, AND THAT IS WHAT KEYS THE CACHE. `cachedRead` serialises
 * the arguments into the key, so this is handled by passing one — but only for
 * a caller that actually passes it. `getMarkets` is the fifth read to join the
 * list `lib/core/sport.ts` keeps of reads that must be sport-keyed; a caller
 * that omits the argument gets the college catalogue for the length of the TTL,
 * which on the NFL board means no first-quarter markets and no error.
 */
export const getMarkets = cachedRead("markets", readMarkets);
export const getConferences = cachedRead("conferences", readConferences);
