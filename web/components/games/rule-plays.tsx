"use client";

import Link from "next/link";
import { useState } from "react";

import { TeamChip } from "@/components/board/team-chip";
import { formatAmericanOdds, formatKickoff } from "@/lib/core/format";
import {
  isTopTier,
  playLabel,
  playResult,
  reasonLabel,
  RULES_SUMMARY,
  type PlayResult,
  type PlaysScope,
  type RulePlay,
} from "@/lib/core/rule-plays";

/** Rows before "Show all": a Saturday can carry 30+, above the whole slate. */
const FIRST_ROWS = 8;

const MARKET_TAG: Record<RulePlay["market"], string> = {
  spreads: "Spread",
  totals: "Total",
  h2h: "ML",
};

const RESULT_STYLE: Record<PlayResult, string> = {
  win: "border-positive/40 bg-positive/10 text-positive",
  loss: "border-negative/40 bg-negative/10 text-negative",
  push: "border-border-strong text-muted",
  pending: "border-border-subtle text-dim",
};

/**
 * Rule plays above the slate (client, 2026-10-09): the game model against the
 * book on his rules, today's and the week's. Each is frozen when it was made,
 * at the line and price shown, and graded on the tracker's own card.
 *
 * The split and which games have started are both decided on the server
 * (`playsFor`, `hasKickedOff`), so the first paint and the hydrated page agree;
 * the tabs only choose a list.
 */
export function RulePlays({
  today,
  week,
  trackerHref,
  startedIds,
}: {
  today: RulePlay[];
  week: RulePlay[];
  trackerHref: string;
  /** Plays whose game has kicked off: they show a result pill. */
  startedIds: number[];
}) {
  const [scope, setScope] = useState<PlaysScope>(today.length > 0 ? "today" : "week");
  const [expanded, setExpanded] = useState(false);
  const list = scope === "today" ? today : week;
  const shown = expanded ? list : list.slice(0, FIRST_ROWS);

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="section-header">🎯 Rule plays</h2>
          <p className="text-muted text-xs">
            {RULES_SUMMARY}. Ours is the game model&rsquo;s fair line; totals 7+ points off are the
            top tier. Each play is locked when it is made, at the line and price shown, and graded
            on the{" "}
            <Link href={trackerHref} className="text-accent-cyan hover:underline">
              tracker
            </Link>
            .
          </p>
        </div>
        <div className="border-border-subtle bg-panel flex gap-1 rounded-xl border p-1" role="tablist">
          {(["today", "week"] as const).map((s) => (
            <button
              key={s}
              type="button"
              role="tab"
              aria-selected={scope === s}
              onClick={() => {
                setScope(s);
                setExpanded(false);
              }}
              className={
                "rounded-lg px-3 py-1.5 text-xs font-extrabold uppercase tracking-label transition-colors " +
                (scope === s ? "cta" : "text-muted hover:text-ink")
              }
            >
              {s === "today" ? `Today (${today.length})` : `This week (${week.length})`}
            </button>
          ))}
        </div>
      </div>

      {list.length === 0 ? (
        <p className="text-dim text-xs">
          {scope === "today"
            ? "No rule plays on today's games. The week's are one tab over."
            : "No rule plays this week yet. They are made as books post lines, with each model run."}
        </p>
      ) : (
        <ul className="flex flex-col">
          {shown.map((p) => (
            <PlayRow key={p.id} p={p} started={startedIds.includes(p.id)} />
          ))}
        </ul>
      )}
      {list.length > FIRST_ROWS ? (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="border-border-subtle text-muted hover:text-ink self-start rounded-full border px-3 py-1 text-[0.6875rem] font-extrabold uppercase tracking-label transition-colors"
        >
          {expanded ? "Show fewer" : `Show all ${list.length}`}
        </button>
      ) : null}
    </section>
  );
}

function PlayRow({ p, started }: { p: RulePlay; started: boolean }) {
  const result = playResult(p);
  return (
    <li className="border-border-subtle flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t py-2.5 first:border-0">
      <Link href={`/games/${p.gameId}`} className="group flex min-w-0 flex-1 basis-64 items-center gap-2">
        <span className="flex shrink-0 items-center gap-1">
          <TeamChip abbreviation={p.away} color={p.awayColor} altColor={p.awayAltColor} title={p.awaySchool} />
          <span className="text-dim text-xs">{p.neutralSite ? "vs" : "@"}</span>
          <TeamChip abbreviation={p.home} color={p.homeColor} altColor={p.homeAltColor} title={p.homeSchool} />
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="text-ink group-hover:text-accent-cyan text-sm font-extrabold transition-colors">
            {playLabel(p)}{" "}
            <span className="text-muted font-semibold tabular-nums">
              {formatAmericanOdds(p.price)}
              {p.sportsbookName ? ` · ${p.sportsbookName}` : ""}
            </span>
          </span>
          <span className="text-dim truncate text-[0.6875rem]">
            {reasonLabel(p)} · {formatKickoff(p.startDate)}
          </span>
        </span>
      </Link>
      <span className="flex items-center gap-1.5">
        <span className="border-border-strong text-muted rounded px-1.5 py-0.5 text-[0.625rem] font-bold uppercase tracking-label">
          {MARKET_TAG[p.market]}
        </span>
        {isTopTier(p) ? (
          <span className="border-target/40 bg-target/10 text-target rounded px-1.5 py-0.5 text-[0.625rem] font-extrabold uppercase tracking-label">
            🔥 7+
          </span>
        ) : null}
        {started ? (
          <span
            className={`rounded-full border px-2 py-0.5 text-[0.625rem] font-extrabold uppercase tracking-label ${RESULT_STYLE[result]}`}
          >
            {result === "pending" ? "Pending" : result}
          </span>
        ) : null}
      </span>
    </li>
  );
}
