"use client";

import { useState } from "react";

import { formatAmericanOdds, formatEdge, formatKickoff } from "@/lib/core/format";
import {
  chooseBook,
  DEFAULT_FANTASY_VERSION,
  FANTASY_VERSION_LABEL,
  FANTASY_VERSIONS,
  moneylineEdge,
  pointsEdge,
  SPREAD_GAP,
  TOTAL_GAP,
  teamLineup,
  type FantasyProjection,
  type FantasyVersion,
  type LineupEntry,
  type PointsEdge,
} from "@/lib/core/fantasy-view";
import {
  formatFair,
  modelSpreadLabel,
  spreadSideLabel,
  totalSideLabel,
  type BookOdds,
} from "@/lib/core/game-lines";

/**
 * The client's NFL fantasy game model on the game page (phase F3).
 *
 * TWO VERSIONS, ONE TOGGLE (user, 2026-10-14). "Fixed" by default: the
 * formula with its four faults corrected. "Original" is the formula exactly
 * as his sheet has it. Both are projected and both are graded on the tracker,
 * so the toggle only changes which one is read here.
 *
 * His terms throughout: edges in points against one book's line, a play at
 * his own gaps (spread 4+, total 6+), the win % from his raw 0.23 formula.
 */
export function FantasyPanel({
  projections,
  lineups,
  odds,
  home,
  away,
  homeTeamId,
  awayTeamId,
}: {
  projections: FantasyProjection[];
  lineups: LineupEntry[];
  odds: BookOdds[];
  home: string;
  away: string;
  homeTeamId: number;
  awayTeamId: number;
}) {
  const [version, setVersion] = useState<FantasyVersion>(DEFAULT_FANTASY_VERSION);
  const projection = projections.find((p) => p.version === version) ?? null;

  const spreadQuote = chooseBook(odds, "spreads");
  const totalQuote = chooseBook(odds, "totals");
  const mlQuote = chooseBook(odds, "h2h");

  return (
    <section className="panel flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="section-header">🏈 Fantasy model</h2>
          <p className="text-muted max-w-prose text-xs">
            Each team&rsquo;s five starters&rsquo; CBS fantasy projections plus team numbers:
            red zone, third down, pace, EPA and ratings. Edges are in points against the
            book&rsquo;s line; a play is a spread {SPREAD_GAP}+ or a total {TOTAL_GAP}+ points
            off it.
          </p>
        </div>
        <div
          role="group"
          aria-label="Model version"
          className="border-border-subtle bg-panel-inset flex gap-1 rounded-lg border p-1"
        >
          {FANTASY_VERSIONS.map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={v === version}
              onClick={() => setVersion(v)}
              className={
                "rounded-md px-2.5 py-1 text-[0.6875rem] font-extrabold uppercase tracking-label transition-colors " +
                (v === version ? "cta" : "text-muted hover:text-ink")
              }
            >
              {FANTASY_VERSION_LABEL[v]}
            </button>
          ))}
        </div>
      </div>

      {projection === null ? (
        <p className="text-muted text-xs">
          Not projected yet. A game is projected once both teams&rsquo; starters for the week are
          set, from the Tuesday before it.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <Stat label="Projected score">
              {away} {projection.awayScore.toFixed(1)} · {home} {projection.homeScore.toFixed(1)}
            </Stat>
            <Stat label="Spread">{modelSpreadLabel(projection.homeMargin, home, away)}</Stat>
            <Stat label="Total">{projection.total.toFixed(1)}</Stat>
            <Stat label="Win %">
              {projection.pHomeWin >= 0.5
                ? `${home} ${formatFair(projection.pHomeWin)}`
                : `${away} ${formatFair(1 - projection.pHomeWin)}`}
            </Stat>
          </div>

          <div className="border-border-subtle grid gap-3 border-t pt-3 sm:grid-cols-3">
            <PointsCell
              label="Spread edge"
              edge={pointsEdge(projection, spreadQuote, "spreads")}
              describe={(e) => spreadSideLabel(e.bookLine, e.side, home, away)}
            />
            <PointsCell
              label="Total edge"
              edge={pointsEdge(projection, totalQuote, "totals")}
              describe={(e) => totalSideLabel(e.bookLine, e.side)}
            />
            {(() => {
              const ml = moneylineEdge(projection, mlQuote);
              return (
                <Cell label="Moneyline edge">
                  {ml ? (
                    <>
                      <span
                        className={
                          "font-mono text-sm font-extrabold tabular-nums " +
                          (ml.edge > 0 ? "text-ink" : "text-muted")
                        }
                      >
                        {formatEdge(ml.edge)}
                      </span>
                      <span className="text-dim text-[0.6875rem]">
                        {ml.side === "home" ? home : away} {formatAmericanOdds(ml.price)} ·{" "}
                        {ml.bookName} · model {formatFair(ml.modelProb)}, book{" "}
                        {formatFair(ml.bookProb)}
                      </span>
                    </>
                  ) : (
                    <span className="text-dim text-sm">—</span>
                  )}
                </Cell>
              );
            })()}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Lineup label={away} teamId={awayTeamId} lineups={lineups} />
            <Lineup label={home} teamId={homeTeamId} lineups={lineups} />
          </div>

          <p className="text-dim text-[0.6875rem]">
            Updated {formatKickoff(projection.madeAt)}. Team numbers from games before week{" "}
            {projection.inputsAsOfWeek}. The win % is the model&rsquo;s own formula, not
            adjusted to past results. Starters are picked automatically: the available player
            with the highest CBS projection in each slot.
          </p>
        </>
      )}
    </section>
  );
}

function PointsCell({
  label,
  edge,
  describe,
}: {
  label: string;
  edge: PointsEdge | null;
  describe: (edge: PointsEdge) => string;
}) {
  return (
    <Cell label={label}>
      {edge ? (
        <>
          <span className="flex items-baseline gap-2">
            <span
              className={
                "font-mono text-sm font-extrabold tabular-nums " +
                (edge.isPlay ? "text-target" : "text-muted")
              }
            >
              {edge.gap.toFixed(1)} pts
            </span>
            {edge.isPlay ? (
              <span className="pill border-target/40 bg-target/10 text-target border">Play</span>
            ) : null}
          </span>
          <span className="text-dim text-[0.6875rem]">
            {describe(edge)} {formatAmericanOdds(edge.price)} · {edge.bookName}
          </span>
        </>
      ) : (
        <span className="text-dim text-sm">—</span>
      )}
    </Cell>
  );
}

function Lineup({
  label,
  teamId,
  lineups,
}: {
  label: string;
  teamId: number;
  lineups: LineupEntry[];
}) {
  const { slots, total } = teamLineup(lineups, teamId);
  const skipped = slots.filter((e) => e.skipped);
  return (
    <div className="bg-panel-inset flex flex-col gap-2 rounded-xl p-3">
      <div className="flex items-baseline justify-between">
        <span className="label-caption">{label} starters</span>
        <span className="text-ink text-sm font-extrabold tabular-nums">{total.toFixed(1)} pts</span>
      </div>
      {slots.length === 0 ? (
        <p className="text-muted text-xs">Not set yet.</p>
      ) : (
        <table className="w-full border-collapse text-sm">
          <tbody>
            {slots.map((e) => (
              <tr key={e.slot} className="border-border-subtle/60 border-b last:border-0">
                <td className="label-caption w-12 py-1.5 pr-2">{e.slot}</td>
                <td className="text-ink py-1.5 pr-2">
                  {e.playerName ?? <span className="text-muted">None available</span>}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  <span className="text-ink font-bold">{e.fantasyPoints.toFixed(1)}</span>
                  {e.pointsSource === "default" ? (
                    <span className="text-dim" title="Not on CBS's list: the default for the position">
                      *
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {skipped.length > 0 ? (
        <p className="text-dim text-[0.6875rem]">
          Passed over: {skipped.map((e) => e.skipped).join("; ")}
        </p>
      ) : null}
      {slots.some((e) => e.pointsSource === "default") ? (
        <p className="text-dim text-[0.6875rem]">* Not on CBS&rsquo;s list: the position default.</p>
      ) : null}
    </div>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="label-caption">{label}</span>
      <span className="text-ink text-sm font-extrabold whitespace-nowrap tabular-nums">
        {children}
      </span>
    </span>
  );
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="label-caption">{label}</span>
      {children}
    </span>
  );
}
