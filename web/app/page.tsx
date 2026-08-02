import Link from "next/link";

import { SiteHeader } from "@/components/site-header";
import { WeekStrip } from "@/components/week-strip";
import { isSupabaseConfigured } from "@/lib/core/env";
import {
  formatConfidence,
  formatCount,
  formatDateRange,
  formatEdge,
  formatLine,
} from "@/lib/core/format";
import { getBoardCounts, getBoardRows } from "@/lib/data/board";
import { getConferences, getMarkets } from "@/lib/data/catalogue";
import { getAppConfig } from "@/lib/data/config";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";

/**
 * The board page.
 *
 * PHASE 4b BUILDS THE READ LAYER AND THE SHELL, not the cards. What renders
 * below the week strip is a deliberately plain preview proving the reads work
 * against real data end to end — the styled row cards, controls and filters are
 * Phase 4c. Keeping the two apart means the card work starts from a data layer
 * that is already known to be correct.
 */

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function readInt(
  params: Record<string, string | string[] | undefined>,
  key: string,
): number | undefined {
  const raw = params[key];
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (!value) return undefined;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export default async function Home({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  if (!isSupabaseConfigured()) {
    return (
      <>
        <SiteHeader />
        <main className="mx-auto w-full max-w-7xl px-4 py-10 sm:px-6">
          <div className="panel p-6">
            <h1 className="section-header mb-2">Not configured</h1>
            <p className="text-muted text-sm">
              Set <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
              <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in{" "}
              <code className="font-mono">web/.env.local</code>, then reload.
            </p>
          </div>
        </main>
      </>
    );
  }

  const params = await searchParams;
  const [weeks, config, markets, conferences] = await Promise.all([
    getSlateWeeks(),
    getAppConfig(),
    getMarkets(),
    getConferences(),
  ]);

  const active = findWeek(weeks, readInt(params, "season"), readInt(params, "week"));

  if (!active) {
    return (
      <>
        <SiteHeader />
        <main className="mx-auto w-full max-w-7xl px-4 py-10 sm:px-6">
          <div className="panel p-6">
            <h1 className="section-header mb-2">No slate yet</h1>
            <p className="text-muted max-w-prose text-sm">
              No week has model output. Run the projection job to populate the
              board:{" "}
              <code className="font-mono text-xs">
                python -m worker.jobs.run_projections --season 2025 --weeks 10
              </code>
            </p>
          </div>
        </main>
      </>
    );
  }

  const [counts, preview] = await Promise.all([
    getBoardCounts(active.season, active.week, config.edgeThreshold),
    getBoardRows({
      season: active.season,
      week: active.week,
      sort: "edge",
      limit: 10,
    }),
  ]);

  const leansOnly = counts.withCall - counts.withBookLine;

  return (
    <>
      <SiteHeader />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6">
        <div className="flex flex-col gap-1">
          <span className="label-caption">Legends Sports · College Football</span>
          <h1 className="text-2xl font-extrabold tracking-tight">
            Player Props Board
          </h1>
          <p className="text-muted text-sm">
            {active.season} Week {active.week} ·{" "}
            {formatDateRange(active.firstKickoff, active.lastKickoff)} ·{" "}
            {active.games} games
          </p>
        </div>

        <WeekStrip weeks={weeks} active={active} />

        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Projections" value={formatCount(counts.rows)} />
          <Stat
            label="With a call"
            value={formatCount(counts.withCall)}
            note={`${formatCount(leansOnly)} lean-only, no book line`}
          />
          <Stat
            label="Book lines attached"
            value={formatCount(counts.withBookLine)}
            note={
              counts.withBookLine > 0
                ? "development lines — edges are not real"
                : "no books have posted"
            }
          />
          <Stat
            label={`Edges ≥ ${Math.round(config.edgeThreshold * 100)}%`}
            value={formatCount(counts.overThreshold)}
          />
        </section>

        <section className="panel flex flex-col gap-4 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="section-header">Read layer</span>
            <span className="pill bg-positive/15 text-positive">Online</span>
            <span className="text-muted text-xs">
              {markets.length} markets · {conferences.length} displayed
              conferences · hit-rate basis{" "}
              <code className="font-mono">{config.hitRateBasis}</code> · windows
              L{config.hitRateWindows.join(" / L")}
            </span>
          </div>

          <p className="text-muted max-w-prose text-sm">
            Phase 4b delivers the typed read layer and the app shell. The row
            cards, position tabs, stat selector and filters are Phase 4c — the
            table below is a plain preview confirming the queries return real
            data, not the board.
          </p>

          <div className="overflow-x-auto">
            <table className="w-full min-w-208 text-sm">
              <thead>
                <tr className="text-dim text-left text-[0.625rem] uppercase tracking-label">
                  <th className="py-2 pr-3 font-semibold">Player</th>
                  <th className="py-2 pr-3 font-semibold">Pos</th>
                  <th className="py-2 pr-3 font-semibold">Matchup</th>
                  <th className="py-2 pr-3 font-semibold">Market</th>
                  <th className="py-2 pr-3 font-semibold">Line</th>
                  <th className="py-2 pr-3 font-semibold">Call</th>
                  <th className="py-2 pr-3 font-semibold">Conf</th>
                  <th className="py-2 pr-3 font-semibold">Edge</th>
                  <th className="py-2 font-semibold">Opp rank</th>
                </tr>
              </thead>
              <tbody className="divide-border-subtle divide-y">
                {preview.rows.map((row) => (
                  <tr key={row.projectionId}>
                    <td className="py-2 pr-3 font-medium">{row.playerName}</td>
                    <td className="text-muted py-2 pr-3">{row.positionGroup}</td>
                    <td className="text-muted py-2 pr-3 font-mono text-xs">
                      {row.teamAbbreviation} {row.isHome ? "vs" : "@"}{" "}
                      {row.opponentAbbreviation}
                    </td>
                    <td className="text-muted py-2 pr-3">
                      {row.marketEmoji} {row.marketLabel}
                    </td>
                    <td className="py-2 pr-3 font-mono">
                      {row.line === null ? "—" : formatLine(row.line)}
                    </td>
                    <td className="py-2 pr-3">
                      {row.side ? (
                        <span
                          className={
                            row.side === "over"
                              ? "pill bg-positive/15 text-positive"
                              : "pill bg-negative/15 text-negative"
                          }
                        >
                          {row.side}
                        </span>
                      ) : (
                        <span className="text-dim">lean</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 font-bold">
                      {row.confidence === null
                        ? "—"
                        : formatConfidence(row.confidence)}
                    </td>
                    <td className="text-muted py-2 pr-3 font-mono">
                      {formatEdge(row.edge)}
                    </td>
                    <td className="text-muted py-2 font-mono">
                      {row.opponentRankVsPosition ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-dim text-xs">
            Showing {preview.rows.length} of {formatCount(preview.total)}{" "}
            rows, sorted by edge. Opponent rank 1 allows the most to the
            position.
          </p>
        </section>

        <p className="text-dim text-xs">
          <Link href="/health" className="hover:text-accent-cyan underline">
            System health
          </Link>
        </p>
      </main>
    </>
  );
}

function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="panel flex flex-col gap-1 p-4">
      <span className="label-caption">{label}</span>
      <span className="text-xl font-extrabold tracking-tight">{value}</span>
      {note ? <span className="text-dim text-[0.6875rem]">{note}</span> : null}
    </div>
  );
}
