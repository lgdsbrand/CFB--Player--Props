/**
 * The home page's tiles.
 *
 * SPORT-AGNOSTIC CORE (CLAUDE.md §3). Counts in, tiles out; nothing here reads
 * a database or knows what a conference is.
 *
 * WHY THE TILES ARE DATA AND NOT MARKUP. Two of the four are preset views of
 * the board rather than separate pages — "best plays" is the board sorted by
 * confidence, "top edges" is the board on its own edge filter. Building them as
 * pages would duplicate the read layer, the filter logic and the row-cap
 * handling for two questions the board already answers. Defining them here
 * keeps the preset and its label together, and makes the rules below testable.
 *
 * ---------------------------------------------------------------------------
 * A TILE WITH NOTHING BEHIND IT DOES NOT LINK
 * ---------------------------------------------------------------------------
 * This is the third time this defect has been designed into this product:
 * a3206e9 removed controls that led to an empty board, 7d74e9d fixed a Top 25
 * toggle that returned nothing for a fortnight, and the first version of this
 * page linked BEST PLAYS and TOP EDGES straight into empty results.
 *
 * Measured on production, 2026 week 1: 2,848 props, 433 calls, top confidence
 * **0.574**, and **no row has an edge at all** — the only calls on an opening
 * slate are anytime-touchdown probabilities, which need no book line, and no
 * sportsbook has posted an NCAAF prop yet. A tile promising "top edges" over
 * zero rows is worse than no tile, because the reader concludes the DATA is
 * broken rather than that the market has not opened.
 *
 * So `href` is null when the count is zero, and `unavailable` says why in
 * language about the market rather than about the software.
 *
 * ---------------------------------------------------------------------------
 * THE TWO HONESTY RULES
 * ---------------------------------------------------------------------------
 *   1. NOTHING IS LABELLED "+EV". The client asked for a "sharp plays" page and
 *      called it +EV. The model has never been graded against a real closing
 *      line, so the claim is unearned. It ships as TOP EDGES and earns the name
 *      later.
 *
 *   2. MOST CONFIDENT IS NOT BEST VALUE. A 90% call on a line the book has
 *      priced at 90% is worth nothing. The client chose "most confident" for
 *      best plays, which is a reasonable thing to want, but the tile has to say
 *      what it measures rather than implying it found value.
 *
 * BEST PLAYS CARRIES NO CONFIDENCE FLOOR, and that is a correction rather than
 * an omission. A 65% floor was invented here and returned zero on both live
 * weeks. The client's words were "top most confident", which is an ORDERING,
 * not a threshold — the board sorted by confidence puts the strongest calls on
 * page one whatever the slate happens to support.
 */

// Relative, with the extension: these carry VALUES, and Node's test runner does
// not understand the `@/*` alias.
import { BOARD_PATH } from "./board-params.ts";
// `formatCount` and not `toLocaleString()`: bare toLocaleString follows the
// SERVER's locale, which on this machine renders 1200 as "1.200" — read by an
// American audience as one point two. The pinned formatter also keeps server
// and client agreeing, which is a hydration mismatch avoided.
import { formatCount } from "./format.ts";

export type HomeCounts = {
  /** Every projection on the slate, in the displayed conferences. */
  props: number;
  /** Games the index will list. */
  games: number;
  /** Projections carrying a call — the population "best plays" orders. */
  calls: number;
  /** Rows clearing the configured edge threshold. */
  edges: number;
  /**
   * Rows whose only price is the synthetic development book.
   *
   * Drives `pricingNote`. Not cosmetic: while this is the whole priced
   * population, every edge equals confidence minus 50%, so TOP EDGES and BEST
   * PLAYS become near-duplicates and the page has to say so.
   */
  developmentLine: number;
  /** Rows carrying a price from a real sportsbook. */
  bookLine: number;
  /**
   * Entries on the cheat sheet at the default window — props whose recent games
   * have already cleared today's line at 80% or better.
   *
   * ZERO FOR THE WHOLE OF WEEK 1, every season, and that is arithmetic rather
   * than a fault: a hit rate needs games already played and there are none. The
   * tile therefore does not link, and says so in terms of the season rather
   * than of the software.
   */
  cheatSheet: number;
};

export type HomeTile = {
  key: string;
  emoji: string;
  title: string;
  /** What the tile is, in one line. */
  blurb: string;
  /** Null when there is nothing behind it — then `unavailable` explains. */
  href: string | null;
  count: number;
  /** What the number counts. Always shown beside it, never assumed. */
  countLabel: string;
  /** Why this tile is not a link yet. Null whenever `href` is set. */
  unavailable: string | null;
  /** A caveat that must travel with the tile even when it does link. */
  caveat: string | null;
};

export function homeTiles(
  counts: HomeCounts,
  { season, week }: { season: number; week: number },
): HomeTile[] {
  const scope = `season=${season}&week=${week}`;

  /** One rule for every tile: no rows, no link, and a reason instead. */
  const tile = (
    t: Omit<HomeTile, "href" | "unavailable"> & {
      href: string;
      emptyReason: string;
    },
  ): HomeTile => ({
    key: t.key,
    emoji: t.emoji,
    title: t.title,
    blurb: t.blurb,
    count: t.count,
    countLabel: t.countLabel,
    caveat: t.caveat,
    href: t.count > 0 ? t.href : null,
    unavailable: t.count > 0 ? null : t.emptyReason,
  });

  return [
    tile({
      key: "props",
      emoji: "📊",
      title: "Analyze Props",
      blurb:
        "Every player and market on the slate, with the over/under call and the confidence behind it.",
      href: `${BOARD_PATH}?${scope}`,
      count: counts.props,
      countLabel: "props this week",
      caveat: null,
      emptyReason:
        "No projections for this week yet. They are published once the schedule and rosters are in.",
    }),
    tile({
      key: "games",
      emoji: "🏟️",
      title: "Analyze Games",
      blurb:
        "The slate game first: the book's spread and total, the position matchups, and every prop underneath.",
      href: `/games?${scope}`,
      count: counts.games,
      countLabel: "games this week",
      caveat: null,
      emptyReason: "No games on this week's slate.",
    }),
    tile({
      key: "best",
      emoji: "⚡",
      title: "Best Plays",
      blurb:
        "Every call the model has made this week, strongest first.",
      // A PRESET, not a preset FILTER on the full board. The client asked for
      // "all the plays, just a table view" without the filter apparatus — see
      // `BoardPreset` in `board-params.ts`.
      href: `${BOARD_PATH}?${scope}&preset=best`,
      count: counts.calls,
      countLabel: "calls to rank",
      // Rule 2, on the tile rather than the destination: the tile is what makes
      // the promise.
      caveat:
        "Most confident is not the same as best value — a call the book has priced correctly is worth nothing.",
      emptyReason:
        "The model has not made a call on this slate yet. Calls need a line to price against, or a market like anytime touchdown that carries its own.",
    }),
    tile({
      key: "edges",
      emoji: "🎯",
      title: "Top Edges",
      blurb:
        "Where the model most disagrees with the book, measured against the de-vigged price.",
      href: `${BOARD_PATH}?${scope}&preset=edges`,
      count: counts.edges,
      countLabel: "clearing the threshold",
      caveat: null,
      // Deliberately about the MARKET, not about us. Nothing is broken; the
      // books have not opened these props yet.
      emptyReason:
        "An edge needs a price to measure against, and no sportsbook has posted an NCAAF player prop for this slate. College books usually post on Thursday or Friday for Saturday games.",
    }),
    tile({
      key: "cheat",
      emoji: "🔥",
      title: "Cheat Sheets",
      blurb:
        "Players whose recent games have already cleared the line showing today — the 100% and 80%-and-up lists.",
      href: `/cheat-sheets?${scope}`,
      count: counts.cheatSheet,
      countLabel: "at 80% or better",
      // The same rule as BEST PLAYS, one step further. That tile has to say
      // confidence is not value; this one has to say history is not a forecast,
      // because a percentage beside a player's name is the exact shape of a
      // tout sheet and will be read as a prediction unless it is contradicted.
      caveat:
        "A streak is what has already happened against today's line, not a forecast and not an edge.",
      // Covers both structural causes in one sentence, because the tile cannot
      // tell them apart without paying for a second count: before kickoff there
      // are no games to grade, and before the books post there is no line to
      // grade them against.
      emptyReason:
        "A hit rate needs games already played and a line to grade them against. This fills in once the season is under way — a first entry needs four decided games behind it.",
    }),
  ];
}

/**
 * What to say about the state of the pricing, or null when there is nothing to
 * explain.
 *
 * While every priced row carries the synthetic development line, its de-vigged
 * probability is exactly 0.500, so an edge is confidence minus 50% and TOP
 * EDGES is BEST PLAYS in a different order. Two tiles quietly showing the same
 * rows, one of them implying the market has been beaten, is the worst version
 * of this page.
 */
export function pricingNote(counts: HomeCounts): string | null {
  if (counts.developmentLine === 0) return null;

  if (counts.bookLine === 0) {
    return (
      "No sportsbook has posted an NCAAF player prop for this slate yet. Every " +
      "priced row here carries a placeholder line at even money, which de-vigs " +
      "to exactly 50% — so an edge is currently just confidence minus 50%, and " +
      "Top Edges is Best Plays in a different order. Both fill in properly as " +
      "books post, usually Thursday or Friday for Saturday games."
    );
  }

  return (
    `${formatCount(counts.developmentLine)} of the priced rows still carry ` +
    "a placeholder line at even money rather than a book's. Treat those calls " +
    "as real and their edges as provisional."
  );
}
