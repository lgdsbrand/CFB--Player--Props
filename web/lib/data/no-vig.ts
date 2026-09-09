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

/**
 * Build a filtered no-vig query.
 *
 * ONE definition of "which quotes match", shared by the row read and the count,
 * exactly as `buildBoardQuery` is shared by the board's read and its card scan.
 * The two must agree or the page prints a total the table below it contradicts.
 *
 * `count`/`head` are the split the 500 forced — see `getNoVigPage`.
 */
function buildNoVigQuery(
  select: string,
  filters: NoVigFilters,
  count?: "exact",
  /** Return the count and NO rows. Only meaningful alongside `count`. */
  head?: boolean,
) {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("v_no_vig_rows")
    .select(select, count ? { count, head } : undefined)
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

  return query;
}

/**
 * How many quotes match, without fetching any.
 *
 * `head: true` returns the count and no body, the same trick `getBoardCounts`
 * and `getBoardRowCount` use.
 */
async function getNoVigCount(filters: NoVigFilters): Promise<number> {
  const result = await buildNoVigQuery("line_id", filters, "exact", true);
  if (result.error) {
    throw new Error(`No-vig count failed: ${result.error.message}`);
  }
  return result.count ?? 0;
}

/**
 * THE COUNT IS A SEPARATE STATEMENT, AND THAT IS THE WHOLE FIX FOR A 500.
 *
 * `select(COLUMNS, { count: "exact" })` asks PostgREST for the total AND the
 * ordered page in ONE statement, so the two costs add up against a single
 * `statement_timeout` — measured on production 2026-09-08, the ordered page is
 * 0.9-1.2 s and the exact count is 1.1-1.5 s warm but 3.9 s cold, and together
 * they cancelled: `canceling statement due to statement timeout`, GET
 * /no-vig?sport=nfl 500.
 *
 * College was NOT healthy at the time, only empty: `v_no_vig_rows` held 2,376
 * NFL rows for 2026 week 1 and ZERO college rows for week 2. The page would
 * have 500d for college too the moment week 2's lines were captured.
 *
 * Splitting them changes no SQL and no total — the same predicates run, from
 * `buildNoVigQuery` — but neither statement carries the other's cost, so each
 * finishes inside the timeout. Run in parallel, because nothing here needs the
 * total before the rows: unlike the board, this page cannot ask PostgREST for
 * a range past the end.
 *
 * WHY NOT MAKE THE VIEW FASTER INSTEAD. Measured, with EXPLAIN ANALYZE on
 * production: the planner estimates ONE row out of the windowed CTE (actual
 * 2,496), so whichever reference table lands on top of the join tree is
 * scanned whole and nested-looped against a Materialize of the real rows —
 * 1,789,632 rows discarded on a join filter, 70,630 buffer accesses. Three
 * rewrites were tried and all failed for the same reason: a LATERAL for the
 * conference join was WORSE (214k buffers, 2.4 s), correlated scalar
 * subqueries just moved the bad node from `team_seasons` to `teams o`, and
 * moving every join below the window functions changed nothing (639 ms against
 * a 630 ms baseline). The estimate is what is wrong, and no join shape fixes
 * it. The real fix is an index on `player_prop_lines (season, week, game_id,
 * player_id, market_key, sportsbook_id, captured_at desc)`, which removes the
 * sort under the DISTINCT ON entirely — it is ~5 MB, and production had 9 MB
 * of headroom on 2026-09-08, so it waits for Supabase Pro.
 */
export async function getNoVigPage(filters: NoVigFilters): Promise<NoVigPage> {
  const sort: NoVigSort = filters.sort ?? "hold";
  const query = buildNoVigQuery(COLUMNS, filters);

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

  const [result, total] = await Promise.all([
    ordered.limit(PAGE_LIMIT),
    getNoVigCount(filters),
  ]);

  const rows = unwrap<DbRow[]>(result, "v_no_vig_rows").map(toNoVigRow);

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
 *
 * CUT WITH THE CALLER'S CUTOFF, NOT THE DATABASE'S. The view used to carry an
 * `is_upcoming` boolean of its own, computed as `start_date >= now()`, while the
 * table below these pills cuts at `kickoffCutoff()` — 04:00 ET of the slate day,
 * because a started game deliberately stays on the page until the day rolls over
 * (`lib/core/kickoff.ts`, migration 0052). The two agree only while no game has
 * kicked off. Measured on production for 2026 cfb week 1 at Sat 2026-09-05 20:00
 * ET, the pills saw 84 rec_yards quotes where the table listed 343 — so a market
 * whose games had all started could vanish from the filter while its rows stayed
 * on screen, unselectable, and the pill order was sorted on a different row set
 * than the one it ordered.
 *
 * Migration 0055 replaced that boolean with the kickoff itself, so the cut here
 * is `upcomingOnly` — the same helper, the same predicate string, the same
 * instant the table uses. The rule stays stated once, in `lib/core`.
 */
export async function getNoVigMarkets(
  season: number,
  week: number,
  sport: Sport = DEFAULT_SPORT,
  { kickoffCutoff }: { kickoffCutoff?: Date } = {},
): Promise<NoVigMarket[]> {
  const supabase = createServerSupabaseClient();

  let query = supabase
    .from("v_no_vig_markets")
    .select("market_key, market_label, quotes")
    .eq("sport", sport)
    .eq("season", season)
    .eq("week", week)
    .eq("conference_is_displayed", true);

  if (kickoffCutoff) query = query.or(upcomingOnly(kickoffCutoff));

  const rows = unwrap<DbRow[]>(await query, "v_no_vig_markets");

  // A TRUNCATED AGGREGATE WOULD DROP A MARKET FROM ITS OWN FILTER — the exact
  // failure this function exists to avoid, and the one PostgREST's 1,000-row cap
  // causes silently. The view is now one row per kickoff rather than one per
  // market, so the count is bounded by distinct kickoff times (~120 rows for a
  // college week, measured) and this should never fire; if it does, the fix is
  // to aggregate in the database, not to raise the limit.
  if (rows.length >= MAX_ROWS_PER_REQUEST) {
    throw new Error(
      `v_no_vig_markets returned ${rows.length} rows, at or past the ` +
        `${MAX_ROWS_PER_REQUEST}-row cap: the market filter would be missing markets.`,
    );
  }

  // One row per kickoff now, so the per-market totals are summed here.
  const totals = new Map<string, NoVigMarket>();
  for (const row of rows) {
    const key = row.market_key as string;
    const existing = totals.get(key);
    const quotes = requireNum(row.quotes, "quotes");
    if (existing) {
      existing.quotes += quotes;
    } else {
      totals.set(key, { key, label: row.market_label as string, quotes });
    }
  }

  return [...totals.values()]
    .filter((market) => market.quotes > 0)
    .sort((a, b) => b.quotes - a.quotes || a.key.localeCompare(b.key));
}
