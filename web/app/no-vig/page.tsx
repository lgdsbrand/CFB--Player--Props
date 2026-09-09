import Link from "next/link";

import { NotConfigured } from "@/components/not-configured";
import { PageLink } from "@/components/page-link";
import { NoVigTable } from "@/components/no-vig/no-vig-table";
import { SiteHeader } from "@/components/site-header";
import { WeekStrip } from "@/components/week-strip";
import {
  BOARD_PATH,
  parseBoardParams,
  type RawParams,
  ROWS_PER_PAGE,
} from "@/lib/core/board-params";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount } from "@/lib/core/format";
import { kickoffCutoff } from "@/lib/core/kickoff";
import {
  NO_VIG_SORTS,
  parseNoVigSort,
  summarise,
  type NoVigSort,
} from "@/lib/core/no-vig";
import { POSITION_GROUPS, type PositionGroup } from "@/lib/core/types";
import {
  DEFAULT_SPORT,
  resolveSport,
  SPORT_LABEL,
  type Sport,
} from "@/lib/core/sport";
import { getNoVigMarkets, getNoVigPage } from "@/lib/data/no-vig";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";

/**
 * No-Vig — what the book is charging, with its margin taken out.
 *
 * The client asked for this in place of the arbitrage and PrizePicks features
 * he scratched, and it is the one page here that makes no claim about our
 * model. Every number on it is arithmetic on a posted two-way price: the hold,
 * the fair probability each side implies once that margin is removed, and the
 * fair price that probability corresponds to. It is true whether or not the
 * projections are any good, which is exactly why it is worth having — see
 * `model-is-not-profitable-vs-closing-lines`, and note that nothing on this
 * page reads a projection, a pick, an edge or a confidence.
 *
 * WHAT A READER DOES WITH IT. Two things. Judge a price: a quote at 4.8% hold is
 * a keener market than the same prop at 7.9% elsewhere. And shop a line: where
 * more than one book posts the SAME number, the best price on each side is
 * marked, and how far each book sits from its peers is a column.
 *
 * ANYTIME TOUCHDOWN CANNOT APPEAR HERE and the page says so rather than leaving
 * a market silently missing. Every book prices it Yes-only; a one-sided price
 * carries no information about the other side, so there is no margin to remove
 * and no fair probability to state. That is 3,898 of the week's quotes.
 */

const SORT_LABELS: Record<NoVigSort, string> = {
  hold: "Cheapest",
  consensus: "Out of line",
  player: "Player",
  market: "Market",
};

export default async function NoVig({
  searchParams,
}: {
  searchParams: Promise<RawParams>;
}) {
  if (!isSupabaseConfigured()) {
    return (
      <Shell>
        <NotConfigured />
      </Shell>
    );
  }

  const raw = await searchParams;
  const sport = resolveSport(raw.sport);
  // The board's parser, not a second one. Season, week and position mean the
  // same thing here and are validated the same way.
  const params = parseBoardParams(raw, { edgesOnlyDefault: false });
  const sort = parseNoVigSort(first(raw.sort));
  const market = first(raw.market);
  const shoppableOnly = first(raw.shop) === "1";

  const weeks = await getSlateWeeks(sport);
  const active = findWeek(weeks, params.season, params.week);

  if (!active) {
    return (
      <Shell sport={sport}>
        <Header sport={sport} />
        <div className="panel p-6">
          <h2 className="section-header mb-2">No slate yet</h2>
          <p className="text-muted max-w-prose text-sm">
            No week has model output, so there are no games to price.
          </p>
        </div>
      </Shell>
    );
  }

  const cutoff = kickoffCutoff();

  const [page, markets] = await Promise.all([
    getNoVigPage({
      sport,
      season: active.season,
      week: active.week,
      positionGroup: params.position,
      marketKey: market,
      shoppableOnly,
      kickoffCutoff: cutoff,
      sort,
    }),
    getNoVigMarkets(active.season, active.week, sport, { kickoffCutoff: cutoff }),
  ]);

  // THE SUMMARY DESCRIBES EVERYTHING LOADED, NOT THE VISIBLE PAGE, and that is
  // why the rows below are paged in the render rather than in SQL. Four of the
  // five stats — props, books, median hold, "with a rival quote" — can only be
  // computed from the rows themselves, so a `range()` query would quietly
  // reduce them to describing fifty rows while still sitting under a heading
  // that reads like the slate. `summarise`'s own docstring calls that out.
  const summary = summarise(page.rows);

  // PAGING THE RENDER IS THE FIX FOR PAGE WEIGHT. Measured on the live
  // deployment 2026-09-09: every table row costs ~5.7 KB of HTML, so the 1,000
  // rows this page used to render came to 5.56 MB and took 8-10 s, against the
  // board's 101 rows and 0.58 MB. The database was never the bottleneck — the
  // index in migration 0057 cut real query work and moved page time by nothing.
  //
  // So the fetch is unchanged and only the slice rendered changes. It costs a
  // page view no extra query and no extra database load, and every number on
  // screen means exactly what it meant before. What it does NOT do is reduce
  // the work Postgres does per view; if that becomes the constraint, SQL
  // pagination is the next step and the summary has to move into the database
  // with it.
  const perPage = ROWS_PER_PAGE;
  const totalPages = Math.max(Math.ceil(page.rows.length / perPage), 1);
  const currentPage = Math.min(params.page, totalPages);
  const firstShown = (currentPage - 1) * perPage;
  const visibleRows = page.rows.slice(firstShown, firstShown + perPage);

  const href = (changes: {
    sort?: NoVigSort;
    position?: PositionGroup | null;
    market?: string | null;
    shop?: boolean;
    page?: number;
  }) => {
    const search = new URLSearchParams();
    search.set("season", String(active.season));
    search.set("week", String(active.week));

    const nextSort = changes.sort ?? sort;
    if (nextSort !== "hold") search.set("sort", nextSort);

    const nextPosition =
      changes.position === undefined ? params.position : changes.position;
    if (nextPosition) search.set("position", nextPosition);

    const nextMarket = changes.market === undefined ? market : changes.market;
    if (nextMarket) search.set("market", nextMarket);

    const nextShop = changes.shop ?? shoppableOnly;
    if (nextShop) search.set("shop", "1");

    // ANY CHANGE THAT IS NOT THE PAGER RESETS TO PAGE ONE. Every other caller
    // here narrows or re-sorts, and carrying page 14 across a filter change
    // lands the reader on an empty tail of a shorter list — the stale-deep-link
    // failure the board's own pager guards against by clamping.
    const nextPage = changes.page ?? 1;
    if (nextPage > 1) search.set("page", String(nextPage));

    return `/no-vig?${search.toString()}`;
  };

  return (
    <Shell sport={sport}>
      <Header sport={sport} />

      <WeekStrip weeks={weeks} active={active} basePath="/no-vig" />

      {/*
        THE EXPLANATION SITS ABOVE THE TABLE. Everything below is a grid of
        prices and percentages, and a reader who stops at the first row must
        already know that none of it is a recommendation. The board makes claims
        about what will happen; this page only reports what is being charged.
      */}
      <p className="border-border-subtle bg-panel/60 text-muted rounded-xl border px-3 py-2 text-xs">
        <span className="text-ink font-bold uppercase tracking-label">
          What this shows
        </span>{" "}
        — every two-way price on the slate with the book&rsquo;s margin removed.{" "}
        {/* `{" "}` after the closing tag, not a plain space: a space that
            follows an element OPENING a line is dropped in the build, and only
            the rendered HTML shows it. This shipped as "fairis the price" and
            is the fourth time this trap has bitten in this codebase. */}
        <strong className="text-ink">Hold</strong> is what the book keeps;{" "}
        <strong className="text-ink">fair</strong>{" "}
        is the price the same
        probability would carry at no margin. This is the market&rsquo;s number,
        not ours — for what the model thinks, see the{" "}
        <Link href={BOARD_PATH} className="text-accent-cyan hover:underline">
          board
        </Link>
        .
      </p>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {/* `flex-wrap` like the other two groups. Without it this row could not
            break, and once the pills stopped wrapping their own text the row
            measured 411px inside a 390px viewport — 37px of DOCUMENT overflow,
            on every route, which is how the header bug of 2026-08-12 worked. */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="label-caption">Sort</span>
          {NO_VIG_SORTS.map((option) => (
            <Pill
              key={option}
              href={href({ sort: option })}
              active={option === sort}
              label={SORT_LABELS[option]}
            />
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="label-caption">Market</span>
          <Pill
            href={href({ market: null })}
            active={market === undefined}
            label="All"
          />
          {markets.map((entry) => (
            <Pill
              key={entry.key}
              href={href({ market: entry.key })}
              active={market === entry.key}
              label={entry.label}
            />
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="label-caption">Position</span>
          <Pill
            href={href({ position: null })}
            active={params.position === undefined}
            label="All"
          />
          {POSITION_GROUPS.map((position) => (
            <Pill
              key={position}
              href={href({ position })}
              active={params.position === position}
              label={position}
            />
          ))}
        </div>

        <Pill
          href={href({ shop: !shoppableOnly })}
          active={shoppableOnly}
          label="Shoppable only"
        />
      </div>

      {page.rows.length === 0 ? (
        <EmptyState
          hasMarkets={markets.length > 0}
          shoppableOnly={shoppableOnly}
          filtered={Boolean(market || params.position)}
          clearedHref={href({ market: null, position: null, shop: false })}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <Stat value={formatCount(summary.quotes)} label="Quotes" />
            <Stat value={formatCount(summary.playerMarkets)} label="Props" />
            <Stat value={formatCount(summary.books)} label="Books" />
            <Stat
              value={
                summary.medianHold === null
                  ? "—"
                  : `${(summary.medianHold * 100).toFixed(2)}%`
              }
              label="Median hold"
            />
            <Stat
              value={formatCount(summary.shoppable)}
              label="With a rival quote"
            />
          </div>

          {page.truncated ? (
            <p className="border-negative/40 bg-negative/5 text-muted rounded-xl border px-3 py-2 text-xs">
              <span className="text-negative font-bold uppercase tracking-label">
                Partial slate
              </span>{" "}
              — {formatCount(page.total)} quotes match and the first{" "}
              {formatCount(page.rows.length)} are loaded, paged below. Narrow by
              market or position to reach the rest. A &ldquo;best price&rdquo;
              mark is still trustworthy: it is decided across every book posting
              that same line, not just the rows on screen.
            </p>
          ) : null}

          <NoVigTable rows={visibleRows} />

          {totalPages > 1 ? (
            <nav
              aria-label="No-vig pages"
              className="flex items-center justify-center gap-3 pt-1"
            >
              <PageLink
                href={href({ page: currentPage - 1 })}
                disabled={currentPage <= 1}
              >
                ← Prev
              </PageLink>
              <span className="text-muted text-xs">
                {formatCount(firstShown + 1)}&ndash;
                {formatCount(firstShown + visibleRows.length)} of{" "}
                {formatCount(page.rows.length)}
              </span>
              <PageLink
                href={href({ page: currentPage + 1 })}
                disabled={currentPage >= totalPages}
              >
                Next →
              </PageLink>
            </nav>
          ) : null}
        </>
      )}

      {/*
        Said once, plainly, at the bottom: a market that is simply absent from a
        list reads as a data fault, and anytime touchdown is the biggest market
        we ingest.
      */}
      <p className="text-dim text-xs">
        Anytime touchdown is not listed. Every book prices it yes-only, and a
        one-sided price has no margin to remove — there is nothing to de-vig.
      </p>
    </Shell>
  );
}

function first(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value[0];
  return value;
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="gradient-text text-sm font-extrabold tabular-nums">
        {value}
      </span>
      <span className="label-caption">{label}</span>
    </div>
  );
}

/**
 * Why the page is empty, never a bare "no results".
 *
 * Two of the three causes are structural and no filter change fixes them, so
 * telling the reader to widen their search would be advice that cannot work.
 * The rule this product has learned three times: a surface that leads nowhere
 * has to say why.
 */
function EmptyState({
  hasMarkets,
  shoppableOnly,
  filtered,
  clearedHref,
}: {
  hasMarkets: boolean;
  shoppableOnly: boolean;
  filtered: boolean;
  clearedHref: string;
}) {
  if (!hasMarkets) {
    return (
      <div className="panel p-6">
        <h2 className="section-header mb-2">No two-way prices yet</h2>
        <p className="text-muted max-w-prose text-sm">
          No book has posted a two-sided price on this slate. College books post
          player props late, usually Thursday or Friday for a Saturday game, and
          the odds job picks them up every six hours. Anytime touchdown lines may
          already exist — they are one-sided, so they cannot be de-vigged and are
          not shown here.
        </p>
      </div>
    );
  }

  return (
    <div className="panel p-6">
      <h2 className="section-header mb-2">Nothing matches</h2>
      <p className="text-muted max-w-prose text-sm">
        There are two-way prices on this slate, but none{" "}
        {shoppableOnly ? "with a second book at the same line" : "in this filter"}
        .{" "}
        {filtered || shoppableOnly ? (
          <Link href={clearedHref} className="text-accent-cyan hover:underline">
            Clear the filters
          </Link>
        ) : null}
      </p>
    </div>
  );
}

function Pill({
  href,
  active,
  label,
}: {
  href: string;
  active: boolean;
  label: string;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "true" : undefined}
      // `whitespace-nowrap` because one label here is two words: at 390px
      // "Out of line" wrapped to three lines and the pill became a tall
      // lozenge among round ones. The group already wraps to a new ROW, so
      // holding each pill on one line costs nothing.
      className={
        "whitespace-nowrap rounded-full border px-2.5 py-1 text-[0.6875rem] font-bold uppercase tracking-label transition-colors " +
        (active
          ? "border-accent-cyan/40 bg-accent-cyan/10 text-accent-cyan"
          : "border-border-subtle bg-panel text-muted hover:border-border-strong")
      }
    >
      {label}
    </Link>
  );
}

function Header({ sport }: { sport: Sport }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="label-caption">Legends Sports · {SPORT_LABEL[sport]}</span>
      <h1 className="text-2xl font-extrabold tracking-tight">No-Vig Prices</h1>
    </div>
  );
}

function Shell({
  children,
  sport = DEFAULT_SPORT,
}: {
  children: React.ReactNode;
  sport?: Sport;
}) {
  return (
    <>
      <SiteHeader activeHref="/no-vig" sport={sport} />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-6 sm:px-6">
        {children}
      </main>
    </>
  );
}
