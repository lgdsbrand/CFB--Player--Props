"use client";

import { useEffect, useState } from "react";

import { scopedHref } from "@/lib/core/board-params";
import { formatAmericanOdds, formatKickoff, formatMoney } from "@/lib/core/format";
import { hasKickedOff } from "@/lib/core/kickoff";
import {
  hasSameGameLegs,
  isExchangeOffer,
  legKey,
  legMatchup,
  legTitle,
  needsState,
  offerLink,
  otherLineChoices,
  parlayByBook,
  splitOffers,
  US_STATES,
  type Offer,
  type SlipLeg,
} from "@/lib/core/slip";
import { fetchLegOffers } from "@/components/slip/offers";
import { OPEN_EVENT, slip, useSlip } from "@/components/slip/slip-store";

/** What a $10 parlay would pay, as the example stake. */
const EXAMPLE_STAKE = 10;
const MORE_BOOKS = 6;
const PARLAY_BOOKS = 5;

type Loaded = Offer[] | "error";

/**
 * The bet slip: a button in the corner once something is on it, and the
 * drawer it opens. Mounted once, in the root layout.
 *
 * KEPT TERSE ON PURPOSE (the user, 2026-10-07: "too much text"). Each bet is
 * its title, the best price with one link, and a "More" fold for the other
 * books and lines. A price's capture time is on its hover title.
 *
 * Prices are read when the drawer opens (and when a leg is added while it is
 * open), from the latest odds capture. Every link opens ONE bet at the book;
 * nothing is placed from here.
 */
export function SlipDock() {
  const { legs, state } = useSlip();
  const [open, setOpen] = useState(false);
  const [offers, setOffers] = useState<Record<string, Loaded>>({});
  const keys = legs.map(legKey).join("|");

  useEffect(() => {
    if (!open || legs.length === 0) return;
    let cancelled = false;
    for (const leg of legs) {
      const key = legKey(leg);
      fetchLegOffers(leg).then(
        (rows) => !cancelled && setOffers((was) => ({ ...was, [key]: rows })),
        () => !cancelled && setOffers((was) => ({ ...was, [key]: "error" })),
      );
    }
    return () => {
      cancelled = true;
    };
    // `keys` stands for `legs`: the same bets in a new array are the same slip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, keys]);

  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (legs.length === 0 && !open) return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="cta fixed right-4 bottom-4 z-40 px-4 py-3 shadow-lg shadow-black/40"
      >
        {`🧾 Bet slip · ${legs.length}`}
      </button>
    );
  }

  const live = legs.filter((leg) => !hasKickedOff(leg.startDate));
  const atLine = live.map((leg) => {
    const loaded = offers[legKey(leg)];
    return Array.isArray(loaded) ? splitOffers(leg, loaded).atLine : null;
  });
  const allLoaded = atLine.every((list) => list !== null);
  const parlays = allLoaded ? parlayByBook(atLine as Offer[][]) : [];
  const stateMatters = Object.values(offers).some(
    (loaded) => Array.isArray(loaded) && loaded.some(needsState),
  );

  return (
    <div
      role="dialog"
      aria-label="Bet slip"
      className="panel fixed inset-x-2 bottom-2 z-50 flex max-h-[85vh] flex-col shadow-2xl shadow-black/60 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-[24rem]"
    >
      <div className="border-border-subtle flex items-center gap-3 border-b px-4 py-3">
        <h2 className="section-header">{`🧾 Bet slip (${legs.length})`}</h2>
        {/* The builder for the league being viewed. The dock lives in the root
            layout and has no sport of its own, so it reads the address. */}
        <a
          href="/builder"
          onClick={(event) => {
            const sport = new URLSearchParams(window.location.search).get("sport");
            if (sport === "nfl") event.currentTarget.href = scopedHref("/builder", { sport });
          }}
          className="text-accent-cyan hover:text-ink text-xs font-semibold transition-colors"
        >
          ⚡ Build one
        </a>
        {legs.length > 0 ? (
          <button
            type="button"
            onClick={() => slip.clear()}
            className="text-muted hover:text-negative ml-auto text-xs font-semibold transition-colors"
          >
            Clear
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Close bet slip"
          className={
            "text-muted hover:text-ink text-lg leading-none transition-colors " +
            (legs.length > 0 ? "" : "ml-auto")
          }
        >
          ×
        </button>
      </div>

      <div className="flex flex-col gap-3 overflow-y-auto px-4 py-3">
        {legs.length === 0 ? <p className="text-muted text-xs">Your slip is empty.</p> : null}

        {stateMatters ? (
          <label className="text-muted flex items-center gap-2 text-xs">
            <span title="BetMGM and BetRivers links need it">State</span>
            <select
              value={state ?? ""}
              onChange={(event) => slip.setState(event.target.value || null)}
              className="bg-panel-inset border-border-subtle text-ink ml-auto rounded-md border px-2 py-1 text-xs"
            >
              <option value="">Pick state</option>
              {US_STATES.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {legs.map((leg) => (
          <LegCard key={legKey(leg)} leg={leg} loaded={offers[legKey(leg)]} state={state} />
        ))}

        {live.length >= 2 ? (
          <ParlayPanel
            parlays={parlays}
            loading={!allLoaded}
            sameGame={hasSameGameLegs(live)}
            state={state}
          />
        ) : null}
      </div>
    </div>
  );
}

function LegCard({
  leg,
  loaded,
  state,
}: {
  leg: SlipLeg;
  loaded: Loaded | undefined;
  state: string | null;
}) {
  const key = legKey(leg);
  const started = hasKickedOff(leg.startDate);
  const split = Array.isArray(loaded) ? splitOffers(leg, loaded) : null;
  const [best, ...rest] = split?.atLine ?? [];
  const lines = split ? otherLineChoices(split.otherLines).slice(0, 4) : [];

  return (
    <div className="panel-inset flex flex-col gap-1.5 p-3">
      <div className="flex items-start gap-2">
        <div className="flex min-w-0 flex-col">
          <span className="text-ink text-sm font-bold">{legTitle(leg)}</span>
          <span className="text-dim text-[0.6875rem]">
            {`${legMatchup(leg)} · ${formatKickoff(leg.startDate)}`}
          </span>
        </div>
        <button
          type="button"
          onClick={() => slip.remove(key)}
          aria-label={`Remove ${legTitle(leg)}`}
          className="text-muted hover:text-negative ml-auto text-lg leading-none transition-colors"
        >
          ×
        </button>
      </div>

      {started ? (
        <p className="text-muted text-xs">Started</p>
      ) : loaded === undefined ? (
        <p className="text-dim text-xs">Loading…</p>
      ) : loaded === "error" ? (
        <p className="text-negative text-xs">Couldn&rsquo;t load prices</p>
      ) : !best ? (
        <p className="text-muted text-xs">Not offered right now</p>
      ) : (
        <div className="flex items-center gap-2">
          <span
            className="text-ink text-base font-extrabold tabular-nums"
            title={`Price as of ${formatKickoff(best.capturedAt)}`}
          >
            {formatAmericanOdds(best.price)}
          </span>
          <span className="text-muted text-xs font-semibold">
            {best.bookName}
            {isExchangeOffer(best) ? <ExchangeTag /> : null}
          </span>
          <OpenLink offer={best} state={state} className="ml-auto" />
        </div>
      )}

      {!started && (rest.length > 0 || lines.length > 0) ? (
        <details className="text-xs">
          <summary className="text-muted hover:text-ink cursor-pointer transition-colors">More</summary>
          <ul className="mt-1 flex flex-col">
            {rest.slice(0, MORE_BOOKS).map((offer) => (
              <li
                key={offer.bookKey}
                className="border-border-subtle flex items-center gap-2 border-t py-1 tabular-nums"
              >
                <span className="text-ink w-12 font-bold">{formatAmericanOdds(offer.price)}</span>
                <span className="text-muted">
                  {offer.bookName}
                  {isExchangeOffer(offer) ? <ExchangeTag /> : null}
                </span>
                <OpenLink offer={offer} state={state} className="ml-auto" compact />
              </li>
            ))}
            {lines.map(({ offer }) => {
              const moved = { ...leg, line: offer.line } as SlipLeg;
              return (
                <li
                  key={`line-${offer.line}`}
                  className="border-border-subtle flex items-center gap-2 border-t py-1 tabular-nums"
                >
                  <span className="text-ink w-12 font-bold">{formatAmericanOdds(offer.price)}</span>
                  <span className="text-muted">{legTitle(moved)}</span>
                  <button
                    type="button"
                    onClick={() => slip.add(moved)}
                    className="text-accent-cyan hover:text-ink ml-auto text-[0.6875rem] font-semibold transition-colors"
                  >
                    Use this line
                  </button>
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function ExchangeTag() {
  return (
    <span className="text-dim font-normal" title="Exchange price, before its fees">
      {" (exchange)"}
    </span>
  );
}

function OpenLink({
  offer,
  state,
  className = "",
  compact = false,
}: {
  offer: Offer;
  state: string | null;
  className?: string;
  compact?: boolean;
}) {
  const link = offerLink(offer, state);
  if (!link) {
    return (
      <span className={"text-dim text-[0.6875rem] " + className}>
        {needsState(offer) && !state ? "Pick state" : "No link"}
      </span>
    );
  }
  return (
    <a
      href={link.href}
      target="_blank"
      rel="noopener noreferrer"
      title={link.isBet ? "Opens this bet at the book" : "Opens the game at the book"}
      className={
        (compact
          ? "text-accent-cyan hover:text-ink text-[0.6875rem] font-semibold transition-colors "
          : "cta px-2.5 py-1 ") + className
      }
    >
      {link.isBet ? "Bet ↗" : "Open ↗"}
    </a>
  );
}

function ParlayPanel({
  parlays,
  loading,
  sameGame,
  state,
}: {
  parlays: ReturnType<typeof parlayByBook>;
  loading: boolean;
  sameGame: boolean;
  state: string | null;
}) {
  return (
    <div className="bg-panel-inset border-accent-indigo/40 flex flex-col gap-2 rounded-xl border p-3">
      <span className="section-header">🎯 Parlay</span>
      {loading ? (
        <p className="text-dim text-xs">Loading…</p>
      ) : parlays.length === 0 ? (
        <p className="text-muted text-xs">No book has every leg.</p>
      ) : (
        <ul className="flex flex-col">
          {parlays.slice(0, PARLAY_BOOKS).map((parlay, index) => (
            <li
              key={parlay.bookKey}
              className="border-border-subtle flex flex-col gap-1 border-t py-2 first:border-0 first:pt-0"
            >
              <div className="flex items-baseline gap-2 tabular-nums">
                <span className={"text-base font-extrabold " + (index === 0 ? "gradient-text" : "text-ink")}>
                  {formatAmericanOdds(parlay.american)}
                </span>
                <span className="text-muted text-xs font-semibold">{parlay.bookName}</span>
                <span className="text-dim ml-auto text-[0.6875rem]">
                  {`$${EXAMPLE_STAKE} pays $${formatMoney(EXAMPLE_STAKE * parlay.decimal)}`}
                </span>
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                {parlay.offers.map((offer, legIndex) => {
                  const link = offerLink(offer, state);
                  return link ? (
                    <a
                      key={legIndex}
                      href={link.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent-cyan hover:text-ink text-[0.6875rem] font-semibold transition-colors"
                    >
                      {`Leg ${legIndex + 1} ↗`}
                    </a>
                  ) : null;
                })}
              </div>
            </li>
          ))}
        </ul>
      )}
      {sameGame ? (
        <p className="text-dim text-[0.6875rem]">Same-game legs: the book sets the final price.</p>
      ) : null}
    </div>
  );
}
