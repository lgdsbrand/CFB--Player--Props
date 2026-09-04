/**
 * The no-vig read.
 *
 * `v_no_vig_rows` (migration 0048) does the de-vigging and the cross-book
 * comparison; this applies the page's scope and hands back rows already in the
 * order they will be shown.
 *
 * ALWAYS PINNED TO ONE SEASON AND WEEK. Unfiltered, the view de-duplicates and
 * prices every prop line in the database. Those two predicates lead both window
 * partitions in the view, so the planner pushes them into
 * `player_prop_lines_week_idx` and a week's page reads a week — measured 723 ms
 * on production for all 1,246 quotes of 2026 week 1.
 *
 * FILTER ON THE PLAIN COLUMNS, NOT THE DERIVED ONES. A predicate on a computed
 * column (`fair_price_over`, which is a function call) can cost the planner that
 * pushdown and rebuild the view over every stored line. Nothing here filters on
 * one; if that ever becomes necessary, measure it before shipping it.
 */

import {
  compareNoVigRows,
  type NoVigRow,
  type NoVigSort,
} from "@/lib/core/no-vig";
import { DEFAULT_SPORT, type Sport } from "@/lib/core/sport";
import type { PositionGroup } from "@/lib/core/types";
import {
  type DbRow,
  MAX_ROWS_PER_REQUEST,
  num,
  requireNum,
  unwrap,
  upcomingOnly,
} from "@/lib/data/query";
import { createServerSupabaseClient } from "@/lib/supabase/server";

const COLUMNS =
  "line_id, sport, season, week, game_id, start_date, player_id, player_name, " +
  "position_group, team_id, team_school, team_abbreviation, team_color, " +
  "team_alt_color, is_home, opponent_school, opponent_abbreviation, " +
  "conference_name, conference_is_displayed, market_key, market_label, " +
  "market_emoji, line, sportsbook_key, sportsbook_name, over_price, " +
  "under_price, hold, fair_prob_over, fair_prob_under, fair_price_over, " +
  "fair_price_under, books_at_line, books_on_market, lines_on_market, " +
  "consensus_prob_over, line_prob_over_min, line_prob_over_max, " +
  "consensus_delta_abs, is_best_over, is_best_under, captured_at";

/**
 * Rows to fetch in one request.
 *
 * A full week on production is ~1,250 quotes, which is inside PostgREST's
 * 1,000-row response cap only after the page's own filters. `truncated` is
 * reported rather than hidden: a silently clipped price comparison is a "best
 * price" claim made against books that were never looked at, which is worse
 * than showing nothing.
 */
const PAGE_LIMIT = MAX_ROWS_PER_REQUEST;

export type NoVigPage = {
  rows: NoVigRow[];
  /** True when more quotes matched than were returned. */
  truncated: boolean;
  /** Everything that matched, before the limit. */
  total: number;
};

export type NoVigFilters = {
  season: number;
  week: number;
  sport?: Sport;
  positionGroup?: PositionGroup;
  marketKey?: string;
  /** Only quotes with a rival at the same line — the shoppable subset. */
  shoppableOnly?: boolean;
  /** Hide quotes whose game has kicked off. See `lib/core/kickoff.ts`. */
  kickoffCutoff?: Date;
  sort?: NoVigSort;
};

export async function getNoVigPage(filters: NoVigFilters): Promise<NoVigPage> {
  const supabase = createServerSupabaseClient();
  const sort: NoVigSort = filters.sort ?? "hold";

  let query = supabase
    .from("v_no_vig_rows")
    .select(COLUMNS, { count: "exact" })
    .eq("sport", filters.sport ?? DEFAULT_SPORT)
    .eq("season", filters.season)
    .eq("week", filters.week)
    // The board's scope (CLAUDE.md §7). A price for a player the board will not
    // show is a dead end, and this product has shipped that link three times —
    // see `lib/core/board-scope.ts`.
    .eq("conference_is_displayed", true);

  // A price on a game already played is a result, not a shopping decision.
  if (filters.kickoffCutoff) {
    query = query.or(upcomingOnly(filters.kickoffCutoff));
  }
  if (filters.positionGroup) {
    query = query.eq("position_group", filters.positionGroup);
  }
  if (filters.marketKey) {
    query = query.eq("market_key", filters.marketKey);
  }
  if (filters.shoppableOnly) {
    query = query.gt("books_at_line", 1);
  }

  // THE DATABASE HALF OF `compareNoVigRows`, written inline rather than in a
  // helper: the Supabase builder's type changes with every chained call, so a
  // function taking and returning one either fights the types or erases them.
  // `lib/data/query.ts` makes the same choice for `upcomingOnly`.
  //
  // Every ordering ends on `line_id` so a page is reproducible. Without a
  // unique tiebreak two rows equal on the sort key can swap between requests —
  // the board shipped exactly that once, as a hit rate that changed on reload.
  const ordered =
    sort === "player"
      ? query.order("player_name").order("market_key").order("line").order("line_id")
      : sort === "market"
        ? query.order("market_key").order("hold").order("line_id")
        : sort === "consensus"
          ? // Furthest from its peers first. Single-book quotes have no delta
            // and go LAST rather than first: a null sorted to the top would
            // present "no other book posted this" as the biggest disagreement
            // on the slate — the trap the weekly targets panel hit with
            // unrated defenses.
            query
              .order("consensus_delta_abs", { ascending: false, nullsFirst: false })
              .order("line_id")
          : query.order("hold").order("line_id");

  const result = await ordered.limit(PAGE_LIMIT);

  const rows = unwrap<DbRow[]>(result, "v_no_vig_rows").map(toNoVigRow);
  const total = result.count ?? rows.length;

  // The database decided WHICH rows survived the cap; this decides the order
  // they are read in, and the two must agree or the page shows the right rows
  // in the wrong sequence. The ORDER BY chain above is the same rule.
  rows.sort((a, b) => compareNoVigRows(a, b, sort));

  return { rows, truncated: total > rows.length, total };
}

function toNoVigRow(row: DbRow): NoVigRow {
  return {
    lineId: row.line_id as number,
    season: row.season as number,
    week: row.week as number,
    gameId: row.game_id as number,
    startDate: (row.start_date as string | null) ?? null,

    playerId: row.player_id as number,
    playerName: row.player_name as string,
    positionGroup: (row.position_group as PositionGroup | null) ?? null,

    teamId: row.team_id as number,
    teamSchool: row.team_school as string,
    teamAbbreviation: row.team_abbreviation as string,
    teamColor: (row.team_color as string | null) ?? null,
    teamAltColor: (row.team_alt_color as string | null) ?? null,
    isHome: row.is_home as boolean,
    opponentSchool: row.opponent_school as string,
    opponentAbbreviation: row.opponent_abbreviation as string,
    conferenceName: (row.conference_name as string | null) ?? null,

    marketKey: row.market_key as string,
    marketLabel: row.market_label as string,
    marketEmoji: (row.market_emoji as string | null) ?? null,

    line: requireNum(row.line, "line"),
    sportsbookKey: row.sportsbook_key as string,
    sportsbookName: row.sportsbook_name as string,
    overPrice: requireNum(row.over_price, "over_price"),
    underPrice: requireNum(row.under_price, "under_price"),

    hold: requireNum(row.hold, "hold"),
    fairProbOver: requireNum(row.fair_prob_over, "fair_prob_over"),
    fairProbUnder: requireNum(row.fair_prob_under, "fair_prob_under"),
    fairPriceOver: num(row.fair_price_over),
    fairPriceUnder: num(row.fair_price_under),

    booksAtLine: row.books_at_line as number,
    booksOnMarket: row.books_on_market as number,
    linesOnMarket: row.lines_on_market as number,
    consensusProbOver: requireNum(row.consensus_prob_over, "consensus_prob_over"),
    lineProbOverMin: requireNum(row.line_prob_over_min, "line_prob_over_min"),
    lineProbOverMax: requireNum(row.line_prob_over_max, "line_prob_over_max"),
    isBestOver: row.is_best_over as boolean,
    isBestUnder: row.is_best_under as boolean,
    capturedAt: row.captured_at as string,
  };
}

export type NoVigMarket = { key: string; label: string; quotes: number };

/**
 * The markets that actually have two-way prices on this slate, for the filter.
 *
 * Read from `v_no_vig_markets`, which is where the aggregate has to live:
 * PostgREST refuses aggregate functions for the anon role, and both
 * alternatives are broken. Counting in the page means fetching first, and a
 * week is ~1,250 quotes against a 1,000-row cap, so the rarest market could
 * fall off the end and be missing from its own filter. One exact `head` count
 * per market means eight requests for one control — which returned a 500 on the
 * free tier under an ordinary page load, the connection-pool ceiling this
 * product has met before.
 *
 * A market with no quotes never appears: offering a filter that returns nothing
 * is the "tile with nothing behind it" trap, and `anytime_td` is permanently in
 * that position — one-way at every book, so it can never be de-vigged.
 */
export async function getNoVigMarkets(
  season: number,
  week: number,
  sport: Sport = DEFAULT_SPORT,
  { upcomingOnly: onlyUpcoming = true }: { upcomingOnly?: boolean } = {},
): Promise<NoVigMarket[]> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("v_no_vig_markets")
    .select("market_key, market_label, quotes")
    .eq("sport", sport)
    .eq("season", season)
    .eq("week", week)
    .eq("conference_is_displayed", true);

  // Matches the table's own cut, so a pill cannot advertise a count the rows
  // below it contradict.
  if (onlyUpcoming) query = query.eq("is_upcoming", true);

  const rows = unwrap<DbRow[]>(await query, "v_no_vig_markets");

  return rows
    .map((row) => ({
      key: row.market_key as string,
      label: row.market_label as string,
      quotes: requireNum(row.quotes, "quotes"),
    }))
    .filter((market) => market.quotes > 0)
    .sort((a, b) => b.quotes - a.quotes || a.key.localeCompare(b.key));
}
