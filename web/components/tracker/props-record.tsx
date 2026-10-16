import Link from "next/link";

import { scopedHref } from "@/lib/core/board-params";
import { formatAmericanOdds, formatDateShort, formatEdge, formatLine } from "@/lib/core/format";
import {
  byPickDay,
  marketBreakdown,
  recordWeeks,
  tierCards,
  totals,
  inScope,
  TIERS,
  type PropPick,
  type PropRecordRow,
  type TierKey,
  type Totals,
  type WeekKey,
} from "@/lib/core/prop-record";
import type { Sport } from "@/lib/core/sport";
import { formatUnits } from "@/lib/core/tracker";

/**
 * The props record: every prop call the board made, locked at kickoff and
 * graded against the box score (lib/core/prop-record.ts). The 5%+ tier leads;
 * every tier is on the page beside it.
 */

// -110 both ways: the win rate a flat bettor needs to break even.
const BREAK_EVEN = 110 / 210;

type Scope = { week: WeekKey | null; tier: TierKey; market: string | null; page: number };

export function PropsRecord({
  sport,
  rows,
  picks,
  hasMore,
  scope,
}: {
  sport: Sport;
  rows: PropRecordRow[];
  picks: PropPick[];
  hasMore: boolean;
  scope: Scope;
}) {
  const weeks = recordWeeks(rows);
  const href = (changes: Partial<Scope>) => {
    const next = { ...scope, page: 0, ...changes };
    return scopedHref("/tracker", { sport }, {
      view: "props",
      week: next.week?.week,
      tier: next.tier === "5" ? undefined : next.tier,
      market: next.market ?? undefined,
      page: next.page > 0 ? next.page + 1 : undefined,
    });
  };
  const tier = TIERS.find((t) => t.key === scope.tier) ?? TIERS[0];
  const headline = totals(inScope(rows, scope.week, scope.tier));
  const markets = marketBreakdown(rows, scope.week, scope.tier);

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-3xl font-extrabold uppercase tracking-tight">
            Props <span className="gradient-text">Snapshot</span>
          </h1>
          <span className="label-caption">
            Every prop call · locked at kickoff · {sport === "nfl" ? "NFL" : "college football"}
          </span>
        </div>
        <nav className="border-border-subtle bg-panel flex flex-wrap gap-1 rounded-xl border p-1" aria-label="Week">
          <Pill href={href({ week: null })} active={scope.week === null} label="All weeks" />
          {weeks.map((w) => (
            <Pill
              key={`${w.season}-${w.week}`}
              href={href({ week: w })}
              active={scope.week?.week === w.week && scope.week.season === w.season}
              label={`Week ${w.week}`}
            />
          ))}
        </nav>
      </div>

      {rows.length === 0 ? (
        <div className="panel p-6">
          <h2 className="section-header mb-2">No results yet</h2>
          <p className="text-muted max-w-prose text-sm">
            Every prop call is locked at kickoff from the 16th of October, at the line and price
            the board showed, and graded the morning after the game.
          </p>
        </div>
      ) : (
        <>
          <section className="panel flex flex-wrap items-center gap-x-8 gap-y-3 p-5">
            <Stat label={tier.label}>
              <RecordLine t={headline} size="lg" />
            </Stat>
            <Stat label="Win rate">
              <span className="text-ink text-2xl font-extrabold tabular-nums">{rate(headline)}</span>
            </Stat>
            <Stat label="Units">
              <span className={"text-2xl font-extrabold tabular-nums " + tone(headline.units)}>
                {formatUnits(headline.units)}
              </span>
            </Stat>
            <Stat label="ROI">
              <span className={"text-2xl font-extrabold tabular-nums " + tone(headline.roi ?? 0)}>
                {headline.roi === null ? "—" : formatEdge(headline.roi)}
              </span>
            </Stat>
            <span className="text-dim ml-auto text-xs">
              {headline.pending > 0 ? `${headline.pending} pending · ` : ""}
              {headline.voids > 0 ? `${headline.voids} void` : ""}
            </span>
          </section>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {tierCards(rows, scope.week).map((card) => (
              <TierCard
                key={card.key}
                label={card.label}
                t={card.totals}
                active={card.key === scope.tier}
                href={href({ tier: card.key, market: null })}
              />
            ))}
          </div>

          {markets.length > 0 ? (
            <section className="panel flex flex-col">
              <h2 className="section-header border-border-subtle border-b px-4 py-3">
                By market · {tier.label}
              </h2>
              {markets.map((m) => (
                <Link
                  key={m.key}
                  href={href({ market: scope.market === m.key ? null : m.key })}
                  scroll={false}
                  aria-current={scope.market === m.key ? "true" : undefined}
                  className={
                    "border-border-subtle flex items-center gap-4 border-t px-4 py-2.5 first-of-type:border-0 hover:bg-white/5 " +
                    (scope.market === m.key ? "bg-blue-600/10" : "")
                  }
                >
                  <span className="text-ink w-28 text-sm font-bold">{m.label}</span>
                  <span className="text-muted text-sm tabular-nums">
                    {m.totals.wins}-{m.totals.losses}
                    {m.totals.pushes > 0 ? `-${m.totals.pushes}` : ""}
                  </span>
                  <span className={"text-sm font-bold tabular-nums " + rateTone(m.totals)}>{rate(m.totals)}</span>
                  <span className={"ml-auto text-sm font-extrabold tabular-nums " + tone(m.totals.units)}>
                    {formatUnits(m.totals.units)}
                  </span>
                </Link>
              ))}
            </section>
          ) : null}

          <section className="panel flex flex-col">
            <div className="border-border-subtle flex flex-wrap items-center gap-2 border-b px-4 py-3">
              <h2 className="section-header">✅ Graded picks</h2>
              <span className="text-dim text-xs">
                {tier.label}
                {scope.market ? ` · ${markets.find((m) => m.key === scope.market)?.label ?? scope.market}` : ""}
              </span>
              {scope.market ? (
                <Link href={href({ market: null })} scroll={false} className="text-accent-cyan ml-auto text-xs hover:underline">
                  Every market
                </Link>
              ) : null}
            </div>
            {picks.length === 0 ? (
              <p className="text-muted px-4 py-5 text-sm">No picks here yet.</p>
            ) : (
              byPickDay(picks).map(({ day, picks: dayPicks }) => (
                <div key={day}>
                  <div className="bg-panel-inset/60 label-caption px-4 py-2">{formatDateShort(`${day}T12:00:00`)}</div>
                  {dayPicks.map((p) => (
                    <PickRow key={p.id} p={p} />
                  ))}
                </div>
              ))
            )}
            {scope.page > 0 || hasMore ? (
              <div className="border-border-subtle flex items-center justify-between border-t px-4 py-3 text-xs">
                {scope.page > 0 ? (
                  <Link href={href({ page: scope.page - 1, week: scope.week, tier: scope.tier, market: scope.market })} className="text-accent-cyan hover:underline">
                    ← Newer
                  </Link>
                ) : (
                  <span />
                )}
                {hasMore ? (
                  <Link href={href({ page: scope.page + 1, week: scope.week, tier: scope.tier, market: scope.market })} className="text-accent-cyan hover:underline">
                    Older →
                  </Link>
                ) : null}
              </div>
            ) : null}
          </section>
        </>
      )}

      <p className="text-dim text-center text-[0.6875rem] uppercase tracking-label">
        Every call the board makes against a real book&apos;s two-way line, locked at kickoff at the
        line and price shown · edge = our probability minus the book&apos;s, vig removed · anytime TD
        not included (books price only one side) · graded on the official box score the morning
        after · void when the box score has no value for the player · units on 1-unit stakes at the
        price taken
      </p>
    </>
  );
}

function rate(t: Totals): string {
  return t.winRate === null ? "—" : `${(t.winRate * 100).toFixed(1)}%`;
}

function rateTone(t: Totals): string {
  return t.winRate === null ? "text-dim" : t.winRate >= BREAK_EVEN ? "text-positive" : "text-negative";
}

function tone(units: number): string {
  return units > 0 ? "text-positive" : units < 0 ? "text-negative" : "text-muted";
}

function RecordLine({ t, size }: { t: Totals; size: "lg" | "md" }) {
  return (
    <span className={`${size === "lg" ? "text-2xl" : "text-lg"} font-extrabold tabular-nums`}>
      <span className="text-positive">{t.wins}W</span>
      <span className="text-dim"> · </span>
      <span className="text-negative">{t.losses}L</span>
      {t.pushes > 0 ? (
        <>
          <span className="text-dim"> · </span>
          <span className="text-muted">{t.pushes}P</span>
        </>
      ) : null}
    </span>
  );
}

function TierCard({ label, t, active, href }: { label: string; t: Totals; active: boolean; href: string }) {
  return (
    <Link
      href={href}
      scroll={false}
      aria-current={active ? "true" : undefined}
      className={
        "border-border-subtle bg-panel flex flex-col gap-2 rounded-[14px] border p-4 transition-shadow hover:ring-1 hover:ring-white/15 " +
        (active ? "ring-2 ring-white/40 hover:ring-2 hover:ring-white/40" : "")
      }
    >
      <span className="label-caption">{label}</span>
      <span className={"text-2xl font-extrabold tabular-nums " + rateTone(t)}>{rate(t)}</span>
      <RecordLine t={t} size="md" />
      <span className={"text-sm font-bold tabular-nums " + tone(t.units)}>{formatUnits(t.units)}</span>
    </Link>
  );
}

function Pill({ href, active, label }: { href: string; active: boolean; label: string }) {
  return (
    <Link
      href={href}
      scroll={false}
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

const RESULT_STYLE: Record<PropPick["result"], string> = {
  win: "border-positive/40 bg-positive/10 text-positive",
  loss: "border-negative/40 bg-negative/10 text-negative",
  push: "border-border-strong text-muted",
  void: "border-border-strong text-dim",
  pending: "border-border-subtle text-dim",
};

function PickRow({ p }: { p: PropPick }) {
  return (
    <div className="border-border-subtle flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-4 py-2.5">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="text-ink text-sm font-bold">
          {p.playerName}{" "}
          <span className="text-muted font-semibold">
            {p.side.toUpperCase()} {formatLine(p.line)} {p.marketLabel}
          </span>
        </span>
        <span className="text-dim text-xs">
          {p.team ?? ""}
          {p.opponent ? ` vs ${p.opponent}` : ""} · {formatAmericanOdds(p.price)}
          {p.sportsbookName ? ` ${p.sportsbookName}` : ""} · edge {formatEdge(p.edge)}
          {p.actual !== null ? ` · actual ${formatLine(p.actual).replace(/\.0$/, "")}` : ""}
        </span>
      </div>
      <span
        className={`rounded-lg border px-2 py-0.5 text-[0.625rem] font-black uppercase tracking-label ${RESULT_STYLE[p.result]}`}
      >
        {p.result}
      </span>
      <span className={"w-16 text-right text-sm font-extrabold tabular-nums " + tone(p.units ?? 0)}>
        {p.units === null || p.result === "pending" ? "—" : formatUnits(p.units)}
      </span>
    </div>
  );
}
