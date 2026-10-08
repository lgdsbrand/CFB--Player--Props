"use client";

import { useEffect, useMemo, useState } from "react";

import { GamePicker } from "@/components/game-picker";
import { slip } from "@/components/slip/slip-store";
import {
  buildSlip,
  eligible,
  legHitRate,
  nextShuffleSeed,
  type BuilderCandidate,
  type BuilderSettings,
} from "@/lib/core/builder";
import { scopedHref } from "@/lib/core/board-params";
import { minDecidedFor } from "@/lib/core/cheat-sheet";
import {
  formatAmericanOdds,
  formatConfidence,
  formatEdge,
  formatKickoff,
} from "@/lib/core/format";
import { hasKickedOff } from "@/lib/core/kickoff";
import { legKey, legMatchup, legTitle } from "@/lib/core/slip";
import type { Sport } from "@/lib/core/sport";
import type { BuilderPool } from "@/lib/data/builder";

/**
 * The bet builder (client, 2026-10-08): settings in, a slip out, instantly.
 *
 * The pool is fetched once per week and window from `/builder/pool`; every
 * setting re-runs `buildSlip` in the browser. "Use this slip" replaces the
 * bet slip's legs and opens it, where each book's price and link are read as
 * for any slip. Nothing is placed from here (CLAUDE.md §10).
 */

type Settings = Omit<BuilderSettings, "book" | "minDecided" | "edgeThreshold" | "gameIds"> & {
  /** "" lets the builder use the book with the most matching picks. */
  book: string;
  windowSize: number;
};

const DEFAULTS: Settings = {
  book: "",
  targetOdds: 300,
  legs: "auto",
  minProb: 0.55,
  minHitRate: null,
  minPrice: -300,
  maxPrice: 300,
  markets: [],
  side: null,
  edgesOnly: false,
  sameGame: false,
  windowSize: 5,
};

const STORAGE_KEY = "legends.builder.v1";
const TARGETS: (number | null)[] = [null, 100, 200, 300, 500, 1000];
const LEG_CHOICES: (number | "auto")[] = ["auto", 2, 3, 4, 5, 6];
// Since the per-market calibration (2026-10-08) almost no prop sits above
// 60%: on week 6, 31 of ~800 priced props reached 55%. Steps sized to that.
const CONFIDENCE: number[] = [0.5, 0.52, 0.55, 0.58, 0.6];
const HIT_RATES: (number | null)[] = [null, 0.6, 0.8, 1];
const MIN_PRICES = [-1000, -500, -300, -200, -150, -110];
const MAX_PRICES = [100, 150, 200, 300, 500, 1000];

function loadSettings(): Settings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const saved = JSON.parse(raw) as Partial<Settings>;
    // Markets and book differ by sport and week, so they are not kept.
    return { ...DEFAULTS, ...saved, markets: [], book: "" };
  } catch {
    return DEFAULTS;
  }
}

function saveSettings(settings: Settings) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Private mode: the settings just are not remembered.
  }
}

type Loaded =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; pool: BuilderPool; candidates: BuilderCandidate[] };

export function BetBuilder({
  sport,
  season,
  week,
  edgeThreshold,
}: {
  sport: Sport;
  season: number;
  week: number;
  edgeThreshold: number;
}) {
  const [settings, setSettings] = useState<Settings>(DEFAULTS);
  const [restored, setRestored] = useState(false);
  const [games, setGames] = useState<number[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [seed, setSeed] = useState(0);
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [used, setUsed] = useState(false);

  useEffect(() => {
    // After mount, so the server render and the first client render agree.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSettings(loadSettings());
    setRestored(true);
  }, []);

  useEffect(() => {
    if (restored) saveSettings(settings);
  }, [settings, restored]);

  useEffect(() => {
    let cancelled = false;
    const url = scopedHref("/builder/pool", { sport, season, week }, { window: settings.windowSize });
    const read = () =>
      fetch(url).then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json() as Promise<BuilderPool>;
      });
    // One retry: a cold first read can hit the database's 3 s anonymous
    // timeout and succeed a second later (seen live 2026-10-08).
    read()
      .catch(() => read())
      .then(
        (pool) => {
          if (cancelled) return;
          // Started games leave here, not in render: the pool may be cached
          // for a couple of minutes past a kickoff.
          const candidates = pool.candidates.filter((c) => !hasKickedOff(c.leg.startDate));
          setLoaded({ status: "ready", pool, candidates });
        },
        () => !cancelled && setLoaded({ status: "error" }),
      );
    return () => {
      cancelled = true;
    };
  }, [sport, season, week, settings.windowSize]);

  const update = (patch: Partial<Settings>) => {
    setSettings((was) => ({ ...was, ...patch }));
    setSeed(0);
    setUsed(false);
  };

  const ready = loaded.status === "ready" ? loaded : null;
  const candidates = useMemo(
    () => (ready ? ready.candidates.filter((c) => !excluded.includes(legKey(c.leg))) : []),
    [ready, excluded],
  );

  const base = useMemo(
    () => ({
      ...settings,
      minDecided: minDecidedFor(settings.windowSize),
      edgeThreshold,
      gameIds: games,
    }),
    [settings, edgeThreshold, games],
  );

  // How many picks pass at each book: the book list's numbers and the default.
  const books = useMemo(() => {
    if (!ready) return [];
    return Object.entries(ready.pool.bookNames)
      .map(([key, name]) => ({
        key,
        name,
        count: eligible(candidates, { ...base, book: key }).length,
      }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }, [ready, candidates, base]);

  const book = settings.book || books[0]?.key || "";
  const full: BuilderSettings = useMemo(() => ({ ...base, book }), [base, book]);
  const matching = useMemo(() => eligible(candidates, full), [candidates, full]);
  const built = useMemo(() => buildSlip(candidates, full, seed), [candidates, full, seed]);

  // Matching picks per game at this book, for the game list.
  const gameCounts = useMemo(() => {
    const withoutGames = eligible(candidates, { ...full, gameIds: [] });
    const counts: Record<number, number> = {};
    for (const c of withoutGames) counts[c.leg.gameId] = (counts[c.leg.gameId] ?? 0) + 1;
    return counts;
  }, [candidates, full]);

  const bookName = books.find((b) => b.key === book)?.name ?? book;

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
      <section className="panel flex flex-col gap-4 p-4">
        <h2 className="section-header">⚙️ Settings</h2>

        <Group label="End on odds of">
          {TARGETS.map((target) => (
            <Pill
              key={String(target)}
              active={settings.targetOdds === target}
              onClick={() => update({ targetOdds: target })}
              label={target === null ? "Any" : formatAmericanOdds(target)}
            />
          ))}
          <input
            type="number"
            inputMode="numeric"
            aria-label="Custom target odds"
            placeholder="Custom"
            value={settings.targetOdds !== null && !TARGETS.includes(settings.targetOdds) ? settings.targetOdds : ""}
            onChange={(event) => {
              const value = Number.parseInt(event.target.value, 10);
              if (Number.isFinite(value) && Math.abs(value) >= 100) update({ targetOdds: value });
            }}
            className="bg-panel-inset border-border-subtle text-ink placeholder:text-dim w-24 rounded-full border px-3 py-1 text-xs outline-none"
          />
        </Group>

        <Group label="Picks">
          {LEG_CHOICES.map((legs) => (
            <Pill
              key={String(legs)}
              active={settings.legs === legs}
              onClick={() => update({ legs })}
              label={legs === "auto" ? "Auto" : String(legs)}
            />
          ))}
        </Group>

        <Group label="Model confidence per pick">
          {CONFIDENCE.map((prob) => (
            <Pill
              key={prob}
              active={settings.minProb === prob}
              onClick={() => update({ minProb: prob })}
              label={prob === 0.5 ? "Any" : `${formatConfidence(prob)}+`}
            />
          ))}
        </Group>

        <Group label="Hit rate per pick">
          {HIT_RATES.map((rate) => (
            <Pill
              key={String(rate)}
              active={settings.minHitRate === rate}
              onClick={() => update({ minHitRate: rate })}
              label={rate === null ? "Any" : rate === 1 ? "100%" : `${formatConfidence(rate)}+`}
            />
          ))}
          <span className="text-dim px-1 text-xs">over</span>
          {[5, 10].map((size) => (
            <Pill
              key={size}
              active={settings.windowSize === size}
              onClick={() => {
                setLoaded({ status: "loading" });
                update({ windowSize: size });
              }}
              label={`L${size}`}
            />
          ))}
        </Group>

        <Group label="Odds per pick">
          <Select
            label="Shortest"
            value={settings.minPrice}
            options={MIN_PRICES}
            onChange={(minPrice) => update({ minPrice })}
          />
          <span className="text-dim text-xs">to</span>
          <Select
            label="Longest"
            value={settings.maxPrice}
            options={MAX_PRICES}
            onChange={(maxPrice) => update({ maxPrice })}
          />
        </Group>

        <Group label="Side">
          {([null, "over", "under"] as const).map((side) => (
            <Pill
              key={String(side)}
              active={settings.side === side}
              onClick={() => update({ side })}
              label={side === null ? "Both" : side === "over" ? "Over" : "Under"}
            />
          ))}
        </Group>

        {ready ? (
          <Group label="Bet types">
            <Pill
              active={settings.markets.length === 0}
              onClick={() => update({ markets: [] })}
              label="All"
            />
            {ready.pool.markets.map((market) => {
              const on = settings.markets.includes(market.key);
              return (
                <Pill
                  key={market.key}
                  active={on}
                  onClick={() =>
                    update({
                      markets: on
                        ? settings.markets.filter((key) => key !== market.key)
                        : [...settings.markets, market.key],
                    })
                  }
                  label={market.label}
                />
              );
            })}
          </Group>
        ) : null}

        <div className="relative flex flex-wrap items-end gap-x-4 gap-y-3">
          <label className="flex flex-col gap-1">
            <span className="label-caption">Sportsbook</span>
            <select
              value={settings.book}
              onChange={(event) => update({ book: event.target.value })}
              className="bg-panel-inset border-border-subtle text-ink rounded-lg border px-2.5 py-1.5 text-sm outline-none"
            >
              <option value="">{books[0] ? `Most picks (${books[0].name})` : "Most picks"}</option>
              {books.map((b) => (
                <option key={b.key} value={b.key}>
                  {`${b.name} (${b.count})`}
                </option>
              ))}
            </select>
          </label>

          {ready ? (
            <div className="flex flex-col gap-1">
              <span className="label-caption">Games</span>
              <GamePicker
                busy={false}
                games={ready.pool.games.filter((g) => !hasKickedOff(g.startDate))}
                value={games.join(",")}
                counts={gameCounts}
                onToggle={(id) => {
                  setGames((was) =>
                    was.includes(id) ? was.filter((g) => g !== id) : [...was, id].sort((a, b) => a - b),
                  );
                  setSeed(0);
                }}
                onClear={() => setGames([])}
              />
            </div>
          ) : null}
        </div>

        <div className="flex flex-wrap gap-x-5 gap-y-2">
          <Toggle
            checked={settings.edgesOnly}
            onChange={(edgesOnly) => update({ edgesOnly })}
            label={`Edges only (${Math.round(edgeThreshold * 100)}%+)`}
          />
          <Toggle
            checked={settings.sameGame}
            onChange={(sameGame) => update({ sameGame })}
            label="Allow 2+ picks from one game"
          />
        </div>

        <p className="text-dim text-xs">
          Picks are always on the model&rsquo;s side: its call on a prop, &ldquo;Yes&rdquo; on
          anytime TD, the side with the edge on a spread or total.
        </p>
      </section>

      {/* First on a phone, so the slip is the first thing seen; beside the
          settings, and sticky, from lg up. */}
      <section className="panel order-first flex flex-col gap-3 p-4 lg:order-none lg:sticky lg:top-20 lg:self-start">
        <div className="flex items-baseline gap-3">
          <h2 className="section-header">🎯 Your slip</h2>
          {ready ? (
            <span className="text-dim ml-auto text-xs tabular-nums">
              {`${matching.length} pick${matching.length === 1 ? "" : "s"} match`}
            </span>
          ) : null}
        </div>

        {loaded.status === "loading" ? (
          <p className="text-muted text-sm">Loading this week&rsquo;s picks…</p>
        ) : loaded.status === "error" ? (
          <p className="text-negative text-sm">Couldn&rsquo;t load the picks. Reload to try again.</p>
        ) : !built ? (
          <p className="text-muted text-sm">
            {matching.length === 0
              ? "No pick matches these settings at this book. Loosen the confidence, hit rate or odds range, or try another book."
              : `Only ${matching.length} pick${matching.length === 1 ? "" : "s"} match, not enough for this many legs.`}
          </p>
        ) : (
          <>
            <div className="panel-inset flex flex-wrap items-end gap-x-6 gap-y-2 rounded-xl p-3">
              <Stat label={`Odds at ${bookName}`}>
                <span className="gradient-text text-3xl font-extrabold tabular-nums">
                  {formatAmericanOdds(built.american)}
                </span>
              </Stat>
              <Stat label="Model: all hit">
                <span className="text-ink text-xl font-extrabold tabular-nums">
                  {formatConfidence(built.allHitProb)}
                </span>
              </Stat>
              <Stat label="Picks">
                <span className="text-ink text-xl font-extrabold tabular-nums">{built.picks.length}</span>
              </Stat>
            </div>
            {!built.onTarget && settings.targetOdds !== null ? (
              <p className="text-target text-xs">
                {`Closest to ${formatAmericanOdds(settings.targetOdds)} these settings allow.`}
              </p>
            ) : null}
            {built.sameGame ? (
              <p className="text-target text-xs">
                Two picks share a game, so the real price may differ.
              </p>
            ) : null}

            <ul className="flex flex-col">
              {built.picks.map(({ candidate, price }) => {
                const rate = legHitRate(candidate, minDecidedFor(settings.windowSize));
                return (
                  <li
                    key={legKey(candidate.leg)}
                    className="border-border-subtle flex items-start gap-3 border-t py-2.5"
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="text-ink text-sm font-bold">{legTitle(candidate.leg)}</span>
                      <span className="text-dim text-xs">
                        {`${legMatchup(candidate.leg)} · ${formatKickoff(candidate.leg.startDate)}`}
                      </span>
                      <span className="text-muted text-xs tabular-nums">
                        {`Model ${formatConfidence(candidate.prob)}`}
                        {rate !== null && candidate.hits !== null && candidate.decided !== null
                          ? ` · ${candidate.hits}-${candidate.decided - candidate.hits} L${settings.windowSize}`
                          : ""}
                        {candidate.edge !== null ? ` · edge ${formatEdge(candidate.edge)}` : ""}
                      </span>
                    </div>
                    <span className="text-ink text-sm font-extrabold tabular-nums">
                      {formatAmericanOdds(price)}
                    </span>
                    <button
                      type="button"
                      aria-label={`Swap out ${legTitle(candidate.leg)}`}
                      title="Swap this pick out"
                      onClick={() => {
                        setExcluded((was) => [...was, legKey(candidate.leg)]);
                        setUsed(false);
                      }}
                      className="text-dim hover:text-negative text-lg leading-none transition-colors"
                    >
                      ×
                    </button>
                  </li>
                );
              })}
            </ul>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  slip.replace(built.picks.map((p) => p.candidate.leg));
                  slip.open();
                  setUsed(true);
                }}
                className="cta px-4 py-2.5"
              >
                {used ? "In your slip ✓" : "Use this slip →"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setSeed(nextShuffleSeed(candidates, full, seed));
                  setUsed(false);
                }}
                className="border-border-subtle text-muted hover:text-ink hover:border-border-strong rounded-full border px-4 py-2 text-xs font-bold uppercase tracking-label transition-colors"
              >
                🔀 Shuffle
              </button>
              {excluded.length > 0 ? (
                <button
                  type="button"
                  onClick={() => setExcluded([])}
                  className="text-dim hover:text-muted ml-auto text-xs font-semibold"
                >
                  {`Bring back ${excluded.length} swapped`}
                </button>
              ) : null}
            </div>
            <p className="text-dim text-xs">
              The &ldquo;all hit&rdquo; chance multiplies the model&rsquo;s probabilities. The slip
              shows every book&rsquo;s price and links to each bet.
            </p>
          </>
        )}
      </section>
    </div>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="label-caption">{label}</span>
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
    </div>
  );
}

function Pill({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        "rounded-full border px-2.5 py-1 text-[0.6875rem] font-bold uppercase tracking-label transition-colors " +
        (active
          ? "border-accent-cyan/40 bg-accent-cyan/10 text-accent-cyan"
          : "border-border-subtle bg-panel text-muted hover:border-border-strong")
      }
    >
      {label}
    </button>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: number;
  options: number[];
  onChange: (value: number) => void;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(event) => onChange(Number(event.target.value))}
      className="bg-panel-inset border-border-subtle text-ink rounded-lg border px-2.5 py-1.5 text-sm tabular-nums outline-none"
    >
      {options.map((option) => (
        <option key={option} value={option}>
          {formatAmericanOdds(option)}
        </option>
      ))}
    </select>
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <label className="text-muted flex cursor-pointer items-center gap-2 text-xs font-semibold">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="accent-accent-cyan"
      />
      {label}
    </label>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="label-caption">{label}</span>
      {children}
    </div>
  );
}
