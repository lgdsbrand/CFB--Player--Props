import Link from "next/link";

import {
  boardHref,
  hiddenFields,
  type BoardParams,
} from "@/lib/core/board-params";
import { formatKickoff } from "@/lib/core/format";
import { POSITION_GROUPS, type Conference, type Market } from "@/lib/core/types";
import type { SlateGame } from "@/lib/data/slate";

/**
 * The board's controls (CLAUDE.md §7).
 *
 * NO CLIENT JAVASCRIPT. Pill groups are links and the rest is a GET form, so
 * every control writes the same URL parameters and the board stays a
 * server-rendered document. That is not minimalism for its own sake: the filters
 * are database predicates (a week exceeds PostgREST's row cap, so filtering in
 * the browser would be wrong, not just slow), and keeping them in the URL means
 * a filtered board is an address the client can be sent.
 *
 * The form needs an explicit Apply because submitting on change would require
 * script. The pill groups — the controls people actually reach for — are single
 * clicks.
 */
export function BoardControls({
  params,
  markets,
  conferences,
  games,
  hitRateWindows,
  resultCount,
}: {
  params: BoardParams;
  markets: Market[];
  conferences: Conference[];
  games: SlateGame[];
  hitRateWindows: number[];
  resultCount: number;
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
      <div className="flex flex-wrap items-center gap-2">
        <PillGroup label="Position">
          <PillLink
            href={boardHref(params, { position: undefined, market: marketFor(undefined) })}
            active={params.position === undefined}
          >
            All
          </PillLink>
          {POSITION_GROUPS.map((position) => (
            <PillLink
              key={position}
              href={boardHref(params, { position, market: marketFor(position) })}
              active={params.position === position}
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

        <span className="text-dim ml-auto text-[0.6875rem]">
          {resultCount.toLocaleString("en-US")} rows
        </span>
      </div>

      <form
        method="GET"
        action="/"
        className="border-border-subtle flex flex-wrap items-end gap-2 border-t pt-3"
      >
        {hiddenFields(params, ["q", "game", "conference", "conf", "rank", "page"]).map(
          (field) => (
            <input
              key={field.name}
              type="hidden"
              name={field.name}
              value={field.value}
            />
          ),
        )}

        <Field label="Search player" className="min-w-44 flex-1">
          <input
            type="search"
            name="q"
            defaultValue={params.search ?? ""}
            placeholder="Name…"
            className="bg-panel-inset border-border-subtle text-ink placeholder:text-dim focus:border-accent-cyan/60 w-full rounded-lg border px-2.5 py-1.5 text-sm outline-none"
          />
        </Field>

        <Field label="Game">
          <Select name="game" defaultValue={params.game?.toString() ?? ""}>
            <option value="">All games</option>
            {games.map((game) => (
              <option key={game.gameId} value={game.gameId}>
                {game.awayAbbreviation} @ {game.homeAbbreviation} ·{" "}
                {formatKickoff(game.startDate)}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Conference">
          <Select name="conference" defaultValue={params.conference ?? ""}>
            <option value="">All displayed</option>
            {conferences.map((conference) => (
              <option key={conference.id} value={conference.name}>
                {conference.abbreviation ?? conference.name}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Min confidence">
          <Select name="conf" defaultValue={params.minConfidence?.toString() ?? ""}>
            <option value="">Any</option>
            <option value="0.55">55%</option>
            <option value="0.6">60%</option>
            <option value="0.65">65%</option>
            <option value="0.7">70%</option>
            <option value="0.8">80%</option>
          </Select>
        </Field>

        {/*
          Rank 1 is the BEST defense, so the soft matchups this filter exists
          to find are the HIGH ranks. Stated as "≥" rather than dressed up as
          "top N softest": the number in the control is the number on the card,
          and a filter whose label disagrees with the value beside it is how a
          reader stops trusting both.
        */}
        <Field label="Opp rank ≥">
          <Select name="rank" defaultValue={params.minOpponentRank?.toString() ?? ""}>
            <option value="">Any</option>
            <option value="90">90+ (soft)</option>
            <option value="110">110+</option>
            <option value="125">125+ (softest)</option>
          </Select>
        </Field>

        <button
          type="submit"
          className="cta px-4 py-1.5"
        >
          Apply →
        </button>

        <Link
          href={boardHref(
            { ...params, sort: "edge", edgesOnly: false, hitRateWindow: 5, page: 1 },
            {
              position: undefined,
              market: undefined,
              game: undefined,
              conference: undefined,
              search: undefined,
              minConfidence: undefined,
              minOpponentRank: undefined,
            },
          )}
          className="text-dim hover:text-muted px-2 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-label"
        >
          Reset
        </Link>
      </form>
    </div>
  );
}

function PillGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="label-caption">{label}</span>
      <div className="border-border-subtle bg-panel flex flex-wrap gap-0.5 rounded-full border p-0.5">
        {children}
      </div>
    </div>
  );
}

function PillLink({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "true" : undefined}
      className={
        "rounded-full px-2.5 py-1 text-[0.625rem] font-bold uppercase tracking-label transition-colors " +
        (active
          ? "bg-accent-cyan/15 text-accent-cyan"
          : "text-muted hover:text-ink")
      }
    >
      {children}
    </Link>
  );
}

function Field({
  label,
  className = "",
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={"flex flex-col gap-1 " + className}>
      <span className="label-caption">{label}</span>
      {children}
    </label>
  );
}

function Select({
  name,
  defaultValue,
  children,
}: {
  name: string;
  defaultValue: string;
  children: React.ReactNode;
}) {
  return (
    <select
      name={name}
      defaultValue={defaultValue}
      className="bg-panel-inset border-border-subtle text-ink focus:border-accent-cyan/60 rounded-lg border px-2.5 py-1.5 text-sm outline-none"
    >
      {children}
    </select>
  );
}
