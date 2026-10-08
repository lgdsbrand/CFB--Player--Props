import Link from "next/link";

import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import { scopedHref, type RawParams } from "@/lib/core/board-params";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatAmericanOdds, formatDateShort } from "@/lib/core/format";
import { formatPoints } from "@/lib/core/game-lines";
import { DEFAULT_SPORT, resolveSport, type Sport } from "@/lib/core/sport";
import {
  byDay,
  cards,
  ENGINE_SINCE,
  formatUnits,
  inPeriod,
  PERIOD_LABEL,
  PERIODS,
  pickLabel,
  record,
  resolvePeriod,
  type Card,
  type CardKey,
  type Engine,
  type Period,
  type Record_,
  type TrackerPick,
} from "@/lib/core/tracker";
import { getTrackerPicks } from "@/lib/data/tracker";

/**
 * The tracker (client, 2026-10-08): the game model's picks, graded, laid out
 * like his NHL "Engine Snapshot" so it reads as part of his site.
 *
 * Every pick is frozen before kickoff (migration 0074's trigger) and appears
 * here only once its game has started (0090's policy). Units are 1-unit
 * stakes at the price the pick was made at.
 *
 * V2 ONLY. The client does not want v1 on the page (2026-10-08). Its picks
 * are still made and graded privately (`grade_game_picks --engine v1`).
 */
const ENGINE: Engine = "v2";

/** Break-even at -110: a win rate below this loses money at standard prices. */
const BREAK_EVEN = 110 / 210;

// Card tints after his tracker: each market its own colour.
const CARD_TINT: Record<CardKey, string> = {
  spreads: "border-accent-indigo/40 from-accent-indigo/15",
  totals: "border-accent-cyan/35 from-accent-cyan/10",
  edge: "border-target/40 from-target/15",
  h2h: "border-positive/35 from-positive/10",
};
const BAR_TINT: Record<CardKey, string> = {
  spreads: "bg-accent-indigo",
  totals: "bg-accent-cyan",
  edge: "bg-target",
  h2h: "bg-positive",
};

export default async function TrackerPage({
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
  const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  const engine = ENGINE;
  const period = resolvePeriod(first(raw.period));

  if (sport !== "cfb") {
    return (
      <Shell sport={sport}>
        <Title engine={engine} />
        <div className="panel p-6">
          <p className="text-muted text-sm">
            The game model covers college football.{" "}
            <Link href={scopedHref("/tracker", { sport: "cfb" })} className="text-accent-cyan hover:underline">
              Open the college tracker
            </Link>
            .
          </p>
        </div>
      </Shell>
    );
  }

  const picks = await getTrackerPicks(sport, engine);
  const shown = inPeriod(picks, period);
  const overall = record(shown);
  const href = (changes: { period?: Period }) => {
    const nextPeriod = changes.period ?? period;
    return scopedHref("/tracker", { sport }, {
      period: nextPeriod === "all" ? undefined : nextPeriod,
    });
  };

  return (
    <Shell sport={sport}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <Title engine={engine} />
        <div className="flex flex-col items-end gap-2">
          <Tabs>
            {PERIODS.map((p) => (
              <Tab key={p} href={href({ period: p })} active={p === period} label={PERIOD_LABEL[p]} />
            ))}
          </Tabs>
        </div>
      </div>

      <section className="panel flex flex-wrap items-center gap-x-8 gap-y-3 p-5">
        <Stat label="Overall record">
          <RecordLine r={overall} size="lg" />
        </Stat>
        <Stat label="Win rate">
          <span className="text-ink text-2xl font-extrabold tabular-nums">{rate(overall)}</span>
        </Stat>
        <Stat label="Total units">
          <span className={"text-2xl font-extrabold tabular-nums " + unitsTone(overall.units)}>
            {formatUnits(overall.units)}
          </span>
        </Stat>
        <span className="border-positive/40 bg-positive/10 text-positive ml-auto rounded-md border px-2 py-1 text-[0.6875rem] font-extrabold uppercase tracking-label">
          {engine.toUpperCase()} online
        </span>
      </section>

      {picks.length === 0 ? (
        <div className="panel p-6">
          <h2 className="section-header mb-2">No results yet</h2>
          <p className="text-muted max-w-prose text-sm">
            Engine {engine.toUpperCase()} started {ENGINE_SINCE[engine]}. Its picks appear here as
            their games kick off, and are graded the morning after.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {cards(shown, engine).map((card) => (
            <MarketCard key={card.key} card={card} />
          ))}
        </div>
      )}

      {shown.length > 0 ? (
        <section className="panel flex flex-col">
          <h2 className="section-header border-border-subtle border-b px-4 py-3">✅ Graded picks</h2>
          {/* One fold per day, newest open: all time is hundreds of picks. */}
          {byDay(shown).map(({ day, picks: dayPicks }, index) => (
            <details key={day} open={index === 0} className="group border-border-subtle border-t first:border-0">
              <summary className="bg-panel-inset/60 label-caption flex cursor-pointer list-none items-center gap-2 px-4 py-2 [&::-webkit-details-marker]:hidden">
                <span>
                  {formatDateShort(`${day}T12:00:00`)} · {dayRecord(dayPicks)}
                </span>
                <span className="text-dim ml-auto group-open:rotate-180" aria-hidden>
                  ▾
                </span>
              </summary>
              {dayPicks.map((p) => (
                <PickRow key={p.id} p={p} />
              ))}
            </details>
          ))}
        </section>
      ) : null}

      <p className="text-dim text-center text-[0.6875rem] uppercase tracking-label">
        Picks locked before kickoff · shown once the game starts · graded the morning after · units on
        1-unit stakes at the price taken · Engine {engine.toUpperCase()} since {ENGINE_SINCE[engine]}
      </p>
    </Shell>
  );
}

function rate(r: Record_): string {
  return r.winRate === null ? "—" : `${(r.winRate * 100).toFixed(1)}%`;
}

function unitsTone(units: number): string {
  return units > 0 ? "text-positive" : units < 0 ? "text-negative" : "text-muted";
}

function dayRecord(picks: TrackerPick[]): string {
  const r = record(picks);
  const pending = r.pending > 0 ? ` · ${r.pending} pending` : "";
  return `${r.wins}-${r.losses}${r.pushes ? `-${r.pushes}` : ""} · ${formatUnits(r.units)}${pending}`;
}

function RecordLine({ r, size }: { r: Record_; size: "lg" | "md" }) {
  const text = size === "lg" ? "text-2xl" : "text-lg";
  return (
    <span className={`${text} font-extrabold tabular-nums`}>
      <span className="text-positive">{r.wins}W</span>
      <span className="text-dim"> · </span>
      <span className="text-negative">{r.losses}L</span>
      {r.pushes > 0 ? (
        <>
          <span className="text-dim"> · </span>
          <span className="text-muted">{r.pushes}P</span>
        </>
      ) : null}
    </span>
  );
}

function MarketCard({ card }: { card: Card }) {
  const r = card.record;
  const decided = r.wins + r.losses;
  return (
    <section
      className={`flex flex-col gap-3 rounded-[14px] border bg-gradient-to-br to-transparent p-5 ${CARD_TINT[card.key]}`}
    >
      <div className="flex items-start gap-3">
        <span className="text-2xl" aria-hidden>
          {card.emoji}
        </span>
        <div className="flex flex-col gap-1">
          <h3 className="text-ink text-sm font-extrabold uppercase tracking-label">{card.title}</h3>
          <span className="border-border-strong text-muted w-fit rounded px-1.5 py-0.5 text-[0.625rem] font-bold uppercase tracking-label">
            {card.tag}
          </span>
        </div>
        <span
          className={
            "ml-auto text-3xl font-extrabold tabular-nums " +
            (r.winRate === null ? "text-dim" : r.winRate >= BREAK_EVEN ? "text-positive" : "text-negative")
          }
        >
          {rate(r)}
        </span>
      </div>
      <RecordLine r={r} size="md" />
      <div className="bg-panel-inset h-2 overflow-hidden rounded-full" aria-hidden>
        <div
          className={`h-full rounded-full ${BAR_TINT[card.key]}`}
          style={{ width: `${decided > 0 ? Math.round((r.winRate ?? 0) * 100) : 0}%` }}
        />
      </div>
      <div className="border-border-subtle flex items-end justify-between border-t pt-2">
        <span className="text-dim text-xs">{card.rule}</span>
        <div className="flex flex-col items-end">
          <span className="label-caption">Units</span>
          <span className={"text-sm font-extrabold tabular-nums " + unitsTone(r.units)}>
            {formatUnits(r.units)}
          </span>
        </div>
      </div>
    </section>
  );
}

const RESULT_STYLE: Record<TrackerPick["result"], string> = {
  win: "border-positive/40 bg-positive/10 text-positive",
  loss: "border-negative/40 bg-negative/10 text-negative",
  push: "border-border-strong text-muted",
  pending: "border-border-subtle text-dim",
};

function PickRow({ p }: { p: TrackerPick }) {
  const final =
    p.homePoints !== null && p.awayPoints !== null ? `${p.away} ${p.awayPoints} · ${p.home} ${p.homePoints}` : null;
  return (
    <div className="border-border-subtle flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-4 py-2.5">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="text-ink text-sm font-bold">
          {pickLabel(p)}{" "}
          <span className="text-muted font-semibold tabular-nums">
            {formatAmericanOdds(p.price)}
            {p.sportsbookName ? ` · ${p.sportsbookName}` : ""}
          </span>
        </span>
        <span className="text-dim text-xs">
          {`${p.away} @ ${p.home}`}
          {final ? ` · Final ${final}` : ""}
          {p.market === "totals" && final
            ? ` (${formatPoints((p.homePoints ?? 0) + (p.awayPoints ?? 0))} total)`
            : ""}
        </span>
      </div>
      <span
        className={`rounded-full border px-2 py-0.5 text-[0.625rem] font-extrabold uppercase tracking-label ${RESULT_STYLE[p.result]}`}
      >
        {p.result === "pending" ? "Pending" : p.result}
      </span>
      <span className={"w-16 text-right text-sm font-extrabold tabular-nums " + unitsTone(p.units ?? 0)}>
        {p.units === null ? "—" : formatUnits(p.units)}
      </span>
    </div>
  );
}

function Title({ engine }: { engine: Engine }) {
  return (
    <div className="flex flex-col gap-1">
      <h1 className="text-3xl font-extrabold uppercase tracking-tight">
        Engine <span className="gradient-text">Snapshot</span>
      </h1>
      <span className="label-caption">Performance history · {engine.toUpperCase()} · college game model</span>
    </div>
  );
}

function Tabs({ children }: { children: React.ReactNode }) {
  return (
    <nav className="border-border-subtle bg-panel flex flex-wrap gap-1 rounded-xl border p-1">{children}</nav>
  );
}

function Tab({ href, active, label }: { href: string; active: boolean; label: string }) {
  return (
    <Link
      href={href}
      aria-current={active ? "true" : undefined}
      className={
        "rounded-lg px-3 py-1.5 text-xs font-extrabold uppercase tracking-label transition-colors " +
        (active ? "cta" : "text-muted hover:text-ink")
      }
    >
      {label}
    </Link>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="label-caption">{label}</span>
      {children}
    </div>
  );
}

function Shell({ children, sport = DEFAULT_SPORT }: { children: React.ReactNode; sport?: Sport }) {
  return (
    <>
      <SiteHeader activeHref="/tracker" sport={sport} />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6">{children}</main>
    </>
  );
}
