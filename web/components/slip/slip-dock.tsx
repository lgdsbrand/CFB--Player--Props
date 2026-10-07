"use client";

import { useEffect, useState } from "react";

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
import { slip, useSlip } from "@/components/slip/slip-store";

/** What a $10 parlay would return, as the example stake. */
const EXAMPLE_STAKE = 10;
const MORE_BOOKS = 6;
const PARLAY_BOOKS = 5;

type Loaded = Offer[] | "error";

/**
 * The bet slip: a button in the corner once something is on it, and the
 * drawer it opens. Mounted once, in the root layout.
 *
 * Prices are read when the drawer opens (and when a leg is added while it is
 * open), from the latest odds capture, so the drawer says how old each one is.
 * Every link opens ONE bet at the book; nothing is placed from here.
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
      className="panel fixed inset-x-2 bottom-2 z-50 flex max-h-[85vh] flex-col shadow-2xl shadow-black/60 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-[26rem]"
    >
      <div className="border-border-subtle flex items-center gap-3 border-b px-4 py-3">
        <h2 className="section-header">🧾 Bet slip</h2>
        <span className="text-dim text-xs">{`${legs.length} leg${legs.length === 1 ? "" : "s"}`}</span>
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
        {legs.length === 0 ? (
          <p className="text-muted text-xs">
            Your slip is empty. Add props from the board, or prices from a
            game&rsquo;s page.
          </p>
        ) : null}

        {stateMatters ? (
          <label className="text-muted flex items-center gap-2 text-xs">
            <span>Your state, for BetMGM and BetRivers links</span>
            <select
              value={state ?? ""}
              onChange={(event) => slip.setState(event.target.value || null)}
              className="bg-panel-inset border-border-subtle text-ink ml-auto rounded-md border px-2 py-1 text-xs"
            >
              <option value="">Choose</option>
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
            legs={live}
            parlays={parlays}
            loading={!allLoaded}
            sameGame={hasSameGameLegs(live)}
            state={state}
          />
        ) : null}

        <p className="text-dim text-[0.6875rem]">
          Prices are from our latest odds capture, not live. The slip places no
          bets: each link opens that bet on the book&rsquo;s own site. It is kept
          in this browser only.
        </p>
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

  return (
    <div className="panel-inset flex flex-col gap-2 p-3">
      <div className="flex items-start gap-2">
        <div className="flex min-w-0 flex-col">
          <span className="text-ink text-sm font-bold">{legTitle(leg)}</span>
          <span className="text-dim text-[0.6875rem]">
            {`${legMatchup(leg)} · ${formatKickoff(leg.startDate)}`}
          </span>
        </div>
        <span className="ml-auto flex shrink-0 gap-1">
          <button
            type="button"
            onClick={() => slip.flip(key)}
            title="Switch to the other side"
            className="border-border-subtle text-muted hover:text-ink hover:border-border-strong rounded-md border px-1.5 py-0.5 text-xs transition-colors"
          >
            ⇄
          </button>
          <button
            type="button"
            onClick={() => slip.remove(key)}
            aria-label={`Remove ${legTitle(leg)}`}
            className="border-border-subtle text-muted hover:text-negative hover:border-border-strong rounded-md border px-1.5 py-0.5 text-xs transition-colors"
          >
            ×
          </button>
        </span>
      </div>

      {started ? (
        <p className="text-muted text-xs">Kicked off: books have closed this line.</p>
      ) : loaded === undefined ? (
        <p className="text-dim text-xs">Loading prices…</p>
      ) : loaded === "error" ? (
        <p className="text-negative text-xs">Prices could not be loaded. Reopen the slip to retry.</p>
      ) : !best ? (
        <p className="text-muted text-xs">No book is offering this line right now.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="label-caption">Best</span>
            <span className="text-ink text-base font-extrabold tabular-nums">
              {formatAmericanOdds(best.price)}
            </span>
            <span className="text-muted text-xs font-semibold">
              {best.bookName}
              {isExchangeOffer(best) ? <ExchangeTag /> : null}
            </span>
            <OpenLink offer={best} state={state} className="ml-auto" />
          </div>
          {rest.length > 0 ? (
            <details className="text-xs">
              <summary className="text-muted hover:text-ink cursor-pointer transition-colors">
                {`${rest.length} more book${rest.length === 1 ? "" : "s"}`}
              </summary>
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
              </ul>
            </details>
          ) : null}
          <span className="text-dim text-[0.625rem]">{`Price as of ${formatKickoff(best.capturedAt)}`}</span>
        </>
      )}

      {!started && split && split.otherLines.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1 text-[0.6875rem]">
          <span className="text-dim">Other lines:</span>
          {otherLineChoices(split.otherLines).slice(0, 4).map(({ offer, books }) => {
            const moved = { ...leg, line: offer.line } as SlipLeg;
            return (
              <button
                key={String(offer.line)}
                type="button"
                onClick={() => slip.add(moved)}
                title={`Switch this leg to ${legTitle(moved)}`}
                className="border-border-subtle text-muted hover:text-ink hover:border-border-strong rounded-md border px-1.5 py-0.5 tabular-nums transition-colors"
              >
                {`${legTitle(moved)} ${formatAmericanOdds(offer.price)} ${books > 1 ? `· ${books} books` : offer.bookName}`}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function ExchangeTag() {
  return (
    <span className="text-dim font-normal" title="An exchange's price is before its fees">
      {" · exchange, before fees"}
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
        {needsState(offer) && !state ? "Choose your state" : "No link"}
      </span>
    );
  }
  const label = compact
    ? link.isBet
      ? "Bet ↗"
      : "Game ↗"
    : link.isBet
      ? `Bet at ${offer.bookName} ↗`
      : `Game at ${offer.bookName} ↗`;
  return (
    <a
      href={link.href}
      target="_blank"
      rel="noopener noreferrer"
      title={link.isBet ? "Opens this bet on the book's slip" : "Opens the game's page at the book"}
      className={
        (compact
          ? "text-accent-cyan hover:text-ink text-[0.6875rem] font-semibold transition-colors "
          : "cta px-2.5 py-1 ") + className
      }
    >
      {label}
    </a>
  );
}

function ParlayPanel({
  legs,
  parlays,
  loading,
  sameGame,
  state,
}: {
  legs: SlipLeg[];
  parlays: ReturnType<typeof parlayByBook>;
  loading: boolean;
  sameGame: boolean;
  state: string | null;
}) {
  return (
    <div className="bg-panel-inset border-accent-indigo/40 flex flex-col gap-2 rounded-xl border p-3">
      <span className="section-header">{`🎯 Parlay · ${legs.length} legs`}</span>
      {loading ? (
        <p className="text-dim text-xs">Loading prices…</p>
      ) : parlays.length === 0 ? (
        <p className="text-muted text-xs">
          No single book offers every leg at these lines. Switch a leg&rsquo;s
          line, or remove one.
        </p>
      ) : (
        <ul className="flex flex-col">
          {parlays.slice(0, PARLAY_BOOKS).map((parlay, index) => (
            <li key={parlay.bookKey} className="border-border-subtle flex flex-col gap-1 border-t py-2 first:border-0 first:pt-0">
              <div className="flex items-baseline gap-2 tabular-nums">
                <span
                  className={
                    "text-base font-extrabold " + (index === 0 ? "gradient-text" : "text-ink")
                  }
                >
                  {formatAmericanOdds(parlay.american)}
                </span>
                <span className="text-muted text-xs font-semibold">{parlay.bookName}</span>
                <span className="text-dim ml-auto text-[0.6875rem]">
                  {`$${formatMoney(EXAMPLE_STAKE)} returns $${formatMoney(EXAMPLE_STAKE * parlay.decimal)}`}
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
                      {`Leg ${legIndex + 1} ${link.isBet ? "bet" : "game"} ↗`}
                    </a>
                  ) : (
                    <span key={legIndex} className="text-dim text-[0.6875rem]">
                      {`Leg ${legIndex + 1}: ${needsState(offer) && !state ? "choose your state" : "no link"}`}
                    </span>
                  );
                })}
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="text-dim text-[0.6875rem]">
        Combined as if the legs were independent. Each link opens one leg; add
        them all on the book&rsquo;s slip to build the parlay there, where the
        book sets the final price.
        {sameGame
          ? " Some legs share a game: those are correlated, and books price (or refuse) same-game parlays themselves, so this figure is only a guide."
          : ""}
      </p>
    </div>
  );
}
