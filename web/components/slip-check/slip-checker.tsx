"use client";

import { withBasePath } from "@/lib/core/site";
import { useEffect, useMemo, useState } from "react";

import { ShareButton } from "@/components/share/share-button";
import { SaveSlipButton } from "@/components/slip/save-slip-button";
import { slip, useSlip } from "@/components/slip/slip-store";
import { scopedHref } from "@/lib/core/board-params";
import { formatAmericanOdds, formatEdge, formatKickoff } from "@/lib/core/format";
import {
  bookCounts,
  checkLeg,
  checkSlip,
  encodeSlipKeys,
  indexCatalogue,
  searchCatalogue,
  VERDICT_LABEL,
  type CheckCatalogue,
  type CheckEntry,
  type LegCheck,
  type Verdict,
} from "@/lib/core/slip-check";
import { legKey, legMatchup, legTitle, MAX_LEGS, type SlipLeg } from "@/lib/core/slip";
import type { Sport } from "@/lib/core/sport";

/**
 * The slip checker (client, 2026-10-12). The catalogue is fetched once per
 * week from `/slip-check/catalogue` and searched here; the legs are the
 * ordinary bet slip's, so a leg added on the board is already here and one
 * added here is in the slip drawer. Nothing is placed (CLAUDE.md §10).
 */

type Loaded =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; catalogue: CheckCatalogue };

const BOOK_KEY = "legends.slipcheck.book";
/** Results shown before "Show more": on a phone the slip sits below the list. */
const PAGE = 12;

const pct = (p: number | null) => (p === null ? "—" : `${(p * 100).toFixed(1)}%`);
const price = (p: number | null) => (p === null ? "—" : formatAmericanOdds(p));

const VERDICT_TONE: Record<Verdict, string> = {
  agrees: "border-positive/50 text-positive",
  disagrees: "border-negative/50 text-negative",
  close: "border-border-strong text-muted",
  "no-model": "border-border-subtle text-dim",
  "not-priced": "border-target/50 text-target",
  "not-found": "border-border-subtle text-dim",
};

export function SlipChecker({
  sport,
  season,
  week,
  sharedKeys,
  savedHref,
}: {
  sport: Sport;
  season: number;
  week: number;
  /** Leg keys from a shared link (`?slip=`), offered for loading. */
  sharedKeys: string[];
  /** The My slips tab. */
  savedHref: string;
}) {
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [chosenBook, setChosenBook] = useState("");
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [sharedDismissed, setSharedDismissed] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const { legs } = useSlip();

  useEffect(() => {
    // After mount, so the server render and the first client render agree.
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setChosenBook(window.localStorage.getItem(BOOK_KEY) ?? "");
    } catch {
      // Private mode: the book is just not remembered.
    }
    const timer = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const url = scopedHref("/slip-check/catalogue", { sport, season, week });
    const read = () =>
      fetch(withBasePath(url)).then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json() as Promise<CheckCatalogue>;
      });
    // One retry: a cold first read can hit the anonymous 3 s timeout.
    read()
      .catch(() => read())
      .then(
        (catalogue) => !cancelled && setLoaded({ status: "ready", catalogue }),
        () => !cancelled && setLoaded({ status: "error" }),
      );
    return () => {
      cancelled = true;
    };
  }, [sport, season, week]);

  const catalogue = loaded.status === "ready" ? loaded.catalogue : null;
  const books = useMemo(() => (catalogue ? bookCounts(catalogue) : []), [catalogue]);
  const book = books.some((b) => b.key === chosenBook) ? chosenBook : (books[0]?.key ?? "");
  const bookName = books.find((b) => b.key === book)?.name ?? "";
  const index = useMemo(() => indexCatalogue(catalogue?.entries ?? []), [catalogue]);
  const results = useMemo(
    () => (catalogue ? searchCatalogue(catalogue.entries, book, query, now, shown + 1) : []),
    [catalogue, book, query, now, shown],
  );
  const check = useMemo(() => checkSlip(legs, index, book), [legs, index, book]);
  const inSlip = new Set(legs.map(legKey));

  const shared = useMemo(
    () => sharedKeys.map((key) => index.get(key)).filter((f) => f !== undefined).map((f) => f.entry.sides[f.index].leg),
    [sharedKeys, index],
  );

  const chooseBook = (key: string) => {
    setChosenBook(key);
    try {
      window.localStorage.setItem(BOOK_KEY, key);
    } catch {
      // Not remembered; still applied.
    }
  };

  const toggle = (leg: SlipLeg) => (inSlip.has(legKey(leg)) ? slip.remove(legKey(leg)) : slip.add(leg));

  const imagePath = scopedHref("/slip-check/image", { sport, season, week }, { book, legs: encodeSlipKeys(legs) });
  const sharePage = scopedHref("/slip-check", { sport, season, week }, { slip: encodeSlipKeys(legs) });

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,28rem)]">
      <section className="panel flex flex-col gap-4 p-4">
        <div className="flex flex-col gap-1">
          <span className="label-caption">Add legs</span>
          <h2 className="text-lg font-extrabold">Current prices</h2>
        </div>

        {loaded.status === "loading" ? (
          <p className="text-muted text-sm">Loading this week&rsquo;s prices…</p>
        ) : loaded.status === "error" ? (
          <p className="text-muted text-sm">The prices could not be loaded. Refresh to try again.</p>
        ) : books.length === 0 ? (
          <p className="text-muted text-sm">No book has priced a game still to play this week yet.</p>
        ) : (
          <>
            <label className="flex flex-col gap-1">
              <span className="label-caption">Sportsbook</span>
              <select
                value={book}
                onChange={(event) => chooseBook(event.target.value)}
                className="bg-panel-inset border-border-subtle text-ink rounded-lg border px-2.5 py-2 text-sm font-bold outline-none"
              >
                {books.map((b) => (
                  <option key={b.key} value={b.key}>
                    {b.name} · {b.count.toLocaleString("en-US")} markets
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1">
              <span className="label-caption">Search player, team or market</span>
              <input
                type="search"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setShown(PAGE);
                }}
                placeholder="e.g. Allar pass yards, Texas Tech spread, moneyline"
                autoComplete="off"
                className="bg-panel-inset border-border-subtle text-ink placeholder:text-dim focus:border-accent-cyan/60 rounded-lg border px-2.5 py-2 text-sm outline-none"
              />
            </label>

            {results.length === 0 ? (
              <p className="text-muted text-xs">Nothing at {bookName} matches that.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {results.slice(0, shown).map((entry) => (
                  <ResultRow
                    key={entry.id}
                    entry={entry}
                    book={book}
                    inSlip={inSlip}
                    full={legs.length >= MAX_LEGS}
                    onToggle={toggle}
                  />
                ))}
              </ul>
            )}
            {results.length > shown ? (
              <button
                type="button"
                onClick={() => setShown((n) => n + PAGE * 2)}
                className="border-border-subtle text-muted hover:text-ink hover:border-border-strong rounded-lg border px-3 py-2 text-xs font-extrabold tracking-[0.12em] uppercase"
              >
                Show more
              </button>
            ) : null}
          </>
        )}
      </section>

      {/* On a wide screen the slip stays in view beside a long list. */}
      <section className="panel flex flex-col gap-4 p-4 lg:sticky lg:top-20 lg:max-h-[calc(100dvh-6rem)] lg:self-start lg:overflow-y-auto">
        <div className="flex items-baseline justify-between gap-2">
          <div className="flex flex-col gap-1">
            <span className="label-caption">Your slip</span>
            <h2 className="text-lg font-extrabold">
              {legs.length === 0 ? "No legs yet" : `${legs.length} leg${legs.length === 1 ? "" : "s"}`}
              {bookName ? <span className="text-muted text-sm font-bold"> · {bookName}</span> : null}
            </h2>
          </div>
          {legs.length > 0 ? (
            <button
              type="button"
              onClick={() => slip.clear()}
              className="text-muted hover:text-ink text-xs font-extrabold tracking-[0.12em] uppercase"
            >
              Clear
            </button>
          ) : null}
        </div>

        {shared.length > 0 && !sharedDismissed ? (
          <div className="border-accent-cyan/40 bg-panel-inset flex flex-col gap-2 rounded-xl border p-3">
            <p className="text-ink text-sm font-bold">
              Someone shared a slip with {shared.length} leg{shared.length === 1 ? "" : "s"}.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                className="cta px-3 py-1.5"
                onClick={() => {
                  slip.replace(shared);
                  setSharedDismissed(true);
                }}
              >
                Load it →
              </button>
              <button
                type="button"
                onClick={() => setSharedDismissed(true)}
                className="text-muted hover:text-ink text-xs font-bold"
              >
                Keep mine
              </button>
            </div>
          </div>
        ) : null}

        {legs.length === 0 ? (
          <p className="text-muted text-sm">
            Add legs from the list, or from the props board and the games pages. Your slip is kept in
            this browser only.
          </p>
        ) : (
          <>
            <Summary check={check} bookName={bookName} />
            <ul className="flex flex-col gap-2">
              {check.legs.map((leg) => (
                <LegRow key={leg.key} check={leg} />
              ))}
            </ul>
            <div className="flex flex-wrap items-center gap-2">
              <SaveSlipButton
                legs={check.legs.map((l) => l.leg)}
                bookKey={book || null}
                bookName={bookName || null}
                prices={check.legs.map((l) => l.price)}
                price={check.legs.length === 1 ? check.legs[0].price : check.bookPrice}
                savedHref={savedHref}
              />
              <ShareButton
                imagePath={imagePath}
                pagePath={sharePage}
                fileName="slip-check.png"
                title={`Slip check · ${legs.length} leg${legs.length === 1 ? "" : "s"}`}
              />
              <button type="button" onClick={() => slip.open()} className="text-muted hover:text-ink text-xs font-bold">
                Every book&rsquo;s price and bet links →
              </button>
            </div>
          </>
        )}

        <p className="text-dim text-[0.6875rem] leading-relaxed">
          Model chance is the model&rsquo;s published probability: props and totals are calibrated
          toward 50% by how often past picks came in, spreads sit near 50% because the model has not
          beaten the spread, and a moneyline&rsquo;s win chance is pulled toward the book&rsquo;s
          price. First-half and first-quarter legs have no model number. Book chance is the
          book&rsquo;s two prices with the vig removed. Legs are combined as independent, which is
          wrong for two legs from one game, so such a slip gets no combined number.
        </p>
      </section>
    </div>
  );
}

function ResultRow({
  entry,
  book,
  inSlip,
  full,
  onToggle,
}: {
  entry: CheckEntry;
  book: string;
  inSlip: Set<string>;
  full: boolean;
  onToggle: (leg: SlipLeg) => void;
}) {
  return (
    <li className="bg-panel-inset border-border-subtle flex flex-col gap-2 rounded-xl border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="text-ink text-sm font-extrabold">
          {entry.kind === "prop" ? `${entry.title} · ${entry.market}` : `${entry.matchup} · ${entry.market}`}
        </span>
        <span className="text-dim text-[0.6875rem]">
          {entry.kind === "prop" ? `${entry.matchup} · ` : ""}
          {formatKickoff(entry.startDate)}
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {entry.sides.map((side, i) => {
          const offered = side.prices[book];
          if (offered === undefined) return null;
          const key = legKey(side.leg);
          const added = inSlip.has(key);
          const fair = checkLeg(side.leg, { entry, index: i }, book).fairPrice;
          return (
            <button
              key={key}
              type="button"
              disabled={!added && full}
              onClick={() => onToggle(side.leg)}
              aria-pressed={added}
              className={
                "flex min-w-36 flex-1 items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-colors " +
                (added
                  ? "border-accent-cyan/60 bg-accent-cyan/10"
                  : "border-border-subtle hover:border-border-strong disabled:opacity-40")
              }
            >
              <span className="flex flex-col">
                <span className="text-ink text-sm font-bold">{side.label}</span>
                <span className="text-dim text-[0.6875rem] tabular-nums">
                  {fair === null ? "No model number" : `Fair ${formatAmericanOdds(fair)}`}
                </span>
              </span>
              <span className="flex flex-col items-end">
                <span className="text-ink font-mono text-sm font-extrabold tabular-nums">
                  {formatAmericanOdds(offered)}
                </span>
                <span className={"text-[0.625rem] font-extrabold tracking-[0.12em] uppercase " + (added ? "text-accent-cyan" : "text-muted")}>
                  {added ? "✓ Added" : "Add"}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </li>
  );
}

function Summary({ check, bookName }: { check: ReturnType<typeof checkSlip>; bookName: string }) {
  const single = check.legs.length === 1;
  const note =
    check.blocked === "same-game"
      ? "Two legs are from the same game. They are correlated, so multiplying their chances would mislead; each leg is checked on its own below."
      : check.blocked === "no-model"
        ? "A leg has no model number, so there is no combined chance. The others are checked below."
        : null;
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        <Tile label={single ? "Model chance" : "Model: all hit"} value={pct(check.modelProb)} accent />
        <Tile label="Book chance (no vig)" value={pct(check.bookProb)} />
        <Tile label="Fair price" value={price(check.fairPrice)} />
        <Tile
          label={`${bookName || "Book"} price`}
          value={single ? price(check.legs[0].price) : price(check.bookPrice)}
        />
      </div>
      {check.value !== null ? (
        <p className="text-muted text-xs">
          Value at {bookName}&rsquo;s price:{" "}
          <span className={"font-mono font-extrabold " + (check.value >= 0 ? "text-positive" : "text-negative")}>
            {formatEdge(check.value)}
          </span>{" "}
          per unit staked, if the model&rsquo;s chance is right.
        </p>
      ) : null}
      {note ? <p className="text-target text-xs">{note}</p> : null}
    </div>
  );
}

function Tile({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="bg-panel-inset border-border-subtle flex flex-col gap-0.5 rounded-xl border p-3">
      <span className="label-caption">{label}</span>
      <span
        className={
          "text-xl font-extrabold tabular-nums " +
          (accent ? "from-accent-cyan to-accent-indigo bg-gradient-to-r bg-clip-text text-transparent" : "text-ink")
        }
      >
        {value}
      </span>
    </div>
  );
}

function LegRow({ check }: { check: LegCheck }) {
  return (
    <li className="bg-panel-inset border-border-subtle flex flex-col gap-1.5 rounded-xl border p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <span className="text-ink text-sm font-extrabold">{legTitle(check.leg)}</span>
          <span className="text-dim text-[0.6875rem]">{legMatchup(check.leg)}</span>
        </div>
        <button
          type="button"
          onClick={() => slip.remove(check.key)}
          aria-label={`Remove ${legTitle(check.leg)}`}
          className="text-muted hover:text-ink shrink-0 text-sm font-bold"
        >
          ✕
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={"pill border " + VERDICT_TONE[check.verdict]}>{VERDICT_LABEL[check.verdict]}</span>
        <span className="text-muted text-xs tabular-nums">
          Model {pct(check.modelProb)} · Book {pct(check.bookProb)} · Price {price(check.price)} · Fair{" "}
          {price(check.fairPrice)}
          {check.edge !== null ? ` · Edge ${formatEdge(check.edge)}` : ""}
        </span>
      </div>
    </li>
  );
}
