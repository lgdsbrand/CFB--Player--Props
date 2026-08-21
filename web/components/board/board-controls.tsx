import Link from "next/link";

import { FilterFields } from "@/components/board/filter-fields";
import {
  boardHref,
  type BoardParams,
  type BoardView,
} from "@/lib/core/board-params";
import { formatCount } from "@/lib/core/format";
import { POSITION_GROUPS, type Conference, type Market } from "@/lib/core/types";
import type { GameSummary } from "@/lib/core/types";

/**
 * The board's controls (CLAUDE.md §7).
 *
 * EVERY CONTROL APPLIES ON CHANGE. It did not always: the pill groups were
 * links that navigated at once while the fields below sat behind an Apply
 * button, so half the controls acted immediately and half waited, with nothing
 * on screen explaining which was which. One behaviour now, two mechanisms —
 * pills stay server-rendered links, the fields moved into `FilterFields`, and
 * both write the same URL parameters.
 *
 * FILTER STATE STAYS IN THE URL. The filters are database predicates: a week
 * exceeds PostgREST's row cap, so filtering in the browser would be wrong and
 * not merely slow. Keeping them in the URL also means a filtered board is an
 * address the client can be sent, and it survives a hard refresh.
 */
export function BoardControls({
  params,
  markets,
  conferences,
  games,
  hitRateWindows,
  resultCount,
  resultNoun,
  view,
}: {
  params: BoardParams;
  markets: Market[];
  conferences: Conference[];
  games: GameSummary[];
  hitRateWindows: number[];
  resultCount: number;
  /**
   * What `resultCount` counts. The two layouts page different things — cards
   * page players, the table pages props — so the label has to follow the layout
   * or it states a number under the wrong noun.
   */
  resultNoun: "player" | "prop";
  /** Already resolved by `resolveBoardView`, never the raw optional param. */
  view: BoardView;
}) {
  // The stat selector offers only markets the selected position actually has —
  // driven by `market_positions`, so the UI cannot offer a market the model
  // does not produce for that position.
  const availableMarkets = params.position
    ? markets.filter((market) => market.positions.includes(params.position!))
    : markets;

  // A market that does not apply to the newly chosen position would filter to
  // nothing, so switching position drops it.
  const marketFor = (position?: (typeof POSITION_GROUPS)[number]) => {
    if (!params.market) return undefined;
    if (!position) return params.market;
    const market = markets.find((m) => m.key === params.market);
    return market?.positions.includes(position) ? params.market : undefined;
  };

  return (
    <div className="flex flex-col gap-3">
      {/*
        POSITION AND MARKET EACH TAKE A ROW rather than sharing one. They shared
        a wrapping row, which at phone width put MARKET's nine pills onto three
        lines and pushed everything below the fold; giving each the full width is
        what lets MARKET scroll in one line instead (see `PillGroup`).
      */}
      <div className="flex flex-col gap-2">
        <PillGroup label="Position" size="md">
          <PillLink
            href={boardHref(params, { position: undefined, market: marketFor(undefined) })}
            active={params.position === undefined}
            size="md"
          >
            All
          </PillLink>
          {POSITION_GROUPS.map((position) => (
            <PillLink
              key={position}
              href={boardHref(params, { position, market: marketFor(position) })}
              active={params.position === position}
              size="md"
            >
              {position}
            </PillLink>
          ))}
        </PillGroup>

        <PillGroup label="Market">
          <PillLink
            href={boardHref(params, { market: undefined })}
            active={params.market === undefined}
          >
            All
          </PillLink>
          {availableMarkets.map((market) => (
            <PillLink
              key={market.key}
              href={boardHref(params, { market: market.key })}
              active={params.market === market.key}
            >
              {market.shortLabel ?? market.displayName}
            </PillLink>
          ))}
        </PillGroup>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <PillGroup label="Sort">
          <PillLink
            href={boardHref(params, { sort: "edge" })}
            active={params.sort === "edge"}
          >
            Edge
          </PillLink>
          <PillLink
            href={boardHref(params, { sort: "confidence" })}
            active={params.sort === "confidence"}
          >
            Confidence
          </PillLink>
          <PillLink
            href={boardHref(params, { sort: "opponent_rank" })}
            active={params.sort === "opponent_rank"}
          >
            Opp rank
          </PillLink>
        </PillGroup>

        <PillGroup label="Hit rate">
          {hitRateWindows.map((window) => (
            <PillLink
              key={window}
              href={boardHref(params, { hitRateWindow: window })}
              active={params.hitRateWindow === window}
            >
              L{window}
            </PillLink>
          ))}
        </PillGroup>

        {/*
          THE DEFAULT FOLLOWS THE MARKET FILTER — see `resolveBoardView`. These
          pills always write an explicit value, so once a reader picks a layout
          it stops moving when they change market. `view` arrives resolved, so
          whichever pill is lit is genuinely what is on screen rather than what
          the URL happens to say.
        */}
        <PillGroup label="View">
          <PillLink
            href={boardHref(params, { view: "table" })}
            active={view === "table"}
          >
            Table
          </PillLink>
          <PillLink
            href={boardHref(params, { view: "cards" })}
            active={view === "cards"}
          >
            Cards
          </PillLink>
        </PillGroup>

        <Link
          href={boardHref(params, { rankedOnly: !params.rankedOnly })}
          className={
            "rounded-full border px-3 py-1 text-[0.625rem] font-bold uppercase tracking-label transition-colors " +
            (params.rankedOnly
              ? "border-accent-cyan/50 bg-accent-cyan/15 text-accent-cyan"
              : "border-border-subtle text-muted hover:text-ink")
          }
          title="Only players whose own team is in the AP Top 25 entering this week"
        >
          Top 25
        </Link>

        <Link
          href={boardHref(params, { edgesOnly: !params.edgesOnly })}
          className={
            "rounded-full border px-3 py-1 text-[0.625rem] font-bold uppercase tracking-label transition-colors " +
            (params.edgesOnly
              ? "border-target/50 bg-target/15 text-target"
              : "border-border-subtle text-muted hover:text-ink")
          }
        >
          Edges only
        </Link>

        {/* THE NOUN IS NOT DECORATION. In card view this counts card keys — one
            per player — while a "row" in the read layer is one player-MARKET, of
            which a player has up to six. Calling them rows once put a number
            here that contradicted the home page's prop counts by a factor of
            three, with no way for a reader to tell which was wrong. Table view
            genuinely pages props, so it says props; the caller decides, because
            only the caller knows which query produced the number. */}
        <span className="text-dim ml-auto text-[0.6875rem]">
          {formatCount(resultCount)} {resultNoun}
          {resultCount === 1 ? "" : "s"}
        </span>
      </div>

      <FilterFields params={params} conferences={conferences} games={games} />
    </div>
  );
}

/**
 * Two sizes, and the difference is a claim about which control matters.
 *
 * POSITION IS THE PRIMARY FILTER and was rendering at the same 10px as every
 * secondary pill on the board. `md` is 12px with a wider tap target; `sm` stays
 * the house 10px used by MARKET, SORT and HIT RATE. Nothing else on the board
 * takes `md`, so the step up reads as hierarchy rather than as a type scale
 * that drifted.
 */
type PillSize = "sm" | "md";

const PILL_TEXT: Record<PillSize, string> = {
  sm: "px-2.5 py-1 text-[0.625rem]",
  md: "px-3 py-1.5 text-xs",
};

/** Caption box height, matched to one pill row so the label centres on it. */
const CAPTION_HEIGHT: Record<PillSize, string> = {
  sm: "h-5.75",
  md: "h-7",
};

function PillGroup({
  label,
  children,
  size = "sm",
}: {
  label: string;
  children: React.ReactNode;
  size?: PillSize;
}) {
  return (
    // `items-start`, not `items-center`. The caption gets its own box the height
    // of one pill row so it centres on that row rather than on the group's full
    // height. It mattered more when these groups wrapped to three rows and the
    // caption landed 25px adrift beside the middle one; it still matters,
    // because the scroller below can be taller than its content's line box.
    // `scripts/measure.mjs` reports the drift if the type scale ever moves this.
    <div className="flex items-start gap-1.5">
      <span
        className={`label-caption mt-0.5 flex shrink-0 items-center ${CAPTION_HEIGHT[size]}`}
      >
        {label}
      </span>
      {/*
        ONE LINE THAT SCROLLS, NOT A WRAPPING BLOCK. `min-w-0` is what makes it
        work: a flex child defaults to `min-width: auto` and will not shrink
        below its content, so without it the row would simply overflow the page
        and take the body's horizontal scrollbar with it instead of scrolling
        inside its own border.

        NOT `scroll-fade-x`, which the week strip uses. That mask applies whether
        or not the content overflows, so POSITION — five pills that fit at every
        width — would render its first and last pill permanently half-faded,
        plus the rounded border behind them. The border is the affordance here.
      */}
      <div className="border-border-subtle bg-panel no-scrollbar flex min-w-0 flex-nowrap gap-0.5 overflow-x-auto rounded-full border p-0.5">
        {children}
      </div>
    </div>
  );
}

function PillLink({
  href,
  active,
  children,
  size = "sm",
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
  size?: PillSize;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "true" : undefined}
      className={
        // `shrink-0`: inside a nowrap scroller a flex child would otherwise be
        // compressed to fit, so "PASS YDS" would render squeezed rather than
        // scrolling out of view.
        `shrink-0 whitespace-nowrap rounded-full font-bold uppercase tracking-label transition-colors ${PILL_TEXT[size]} ` +
        (active
          ? "bg-accent-cyan/15 text-accent-cyan"
          : "text-muted hover:text-ink")
      }
    >
      {children}
    </Link>
  );
}

