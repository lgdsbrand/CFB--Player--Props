import Link from "next/link";

import { playerHref } from "@/lib/core/player-params";
import type { Sport } from "@/lib/core/sport";
import {
  injuryReport,
  injuryTone,
  teamStarters,
  type DepthRow,
  type InjuryRow,
} from "@/lib/core/starters";

/**
 * Both teams' starters and injury reports (client, 2026-10-06, after the
 * Bettor Odds card). NFL only today: ESPN's depth charts via nflverse and
 * Sleeper's injury designations (migration 0086). Both are stored per week as
 * they stood going into the game, so a finished game shows its own week.
 *
 * The page renders this only when there are rows, so a college game, which
 * has no source for either, shows nothing rather than an empty panel.
 */
export function StartersPanel({
  sport,
  season,
  week,
  away,
  home,
  depth,
  injuries,
}: {
  sport: Sport;
  season: number;
  week: number;
  away: { teamId: number; label: string };
  home: { teamId: number; label: string };
  depth: DepthRow[];
  injuries: InjuryRow[];
}) {
  const asOf = depth.reduce<string | null>(
    (latest, d) => (latest === null || d.sourceAsOf > latest ? d.sourceAsOf : latest),
    null,
  );
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">🩺 Starters &amp; injuries</h2>
        <p className="text-muted text-xs">
          Depth charts are ESPN&rsquo;s via nflverse
          {asOf ? `, as of ${AS_OF.format(new Date(asOf))} ET` : ""}; injury
          designations are Sleeper&rsquo;s. Both update daily until kickoff and
          then stay as they stood going into the game.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {[away, home].map((side) => (
          <TeamCard
            key={side.teamId}
            label={side.label}
            starters={teamStarters(depth, injuries, side.teamId)}
            report={injuryReport(injuries, side.teamId)}
            link={(playerId) => playerHref({ playerId, sport, season, week })}
          />
        ))}
      </div>
    </section>
  );
}

const AS_OF = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/New_York",
});

function TeamCard({
  label,
  starters,
  report,
  link,
}: {
  label: string;
  starters: ReturnType<typeof teamStarters>;
  report: ReturnType<typeof injuryReport>;
  link: (playerId: number) => string;
}) {
  return (
    <div className="bg-panel-inset flex flex-col gap-3 rounded-xl p-3">
      <span className="label-caption">{label}</span>

      {starters.length === 0 ? (
        <p className="text-dim text-xs">No depth chart for this week.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {starters.map((s, i) => (
            <li key={`${s.label}-${i}`} className="flex items-center gap-2 text-sm">
              <span className="label-caption w-9 shrink-0">{s.label}</span>
              {s.playerId !== null ? (
                <Link
                  href={link(s.playerId)}
                  className="text-ink hover:text-accent-cyan min-w-0 truncate font-bold transition-colors"
                >
                  {s.playerName}
                </Link>
              ) : (
                <span className="text-ink min-w-0 truncate font-bold">{s.playerName}</span>
              )}
              {s.injury ? <StatusPill status={s.injury.status} /> : null}
            </li>
          ))}
        </ul>
      )}

      <div className="border-border-subtle flex flex-col gap-1.5 border-t pt-2">
        <span className="label-caption">Injury report</span>
        {report.game.length === 0 ? (
          <p className="text-dim text-xs">No one listed for this game.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {report.game.map((i) => (
              <li key={`${i.playerName}-${i.status}`} className="flex items-center gap-2 text-xs">
                <span className="text-ink min-w-0 truncate font-semibold">{i.playerName}</span>
                <span className="text-dim shrink-0">
                  {[i.position, i.bodyPart].filter(Boolean).join(" · ")}
                </span>
                <StatusPill status={i.status} />
              </li>
            ))}
          </ul>
        )}
        {report.longTerm.length > 0 ? (
          <p className="text-dim text-[0.6875rem] leading-snug">
            Longer term ({[...new Set(report.longTerm.map((i) => i.status))].join(", ")}):{" "}
            {report.longTerm.map((i) => i.playerName).join(", ")}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const tone = injuryTone(status);
  const colour =
    tone === "out"
      ? "bg-negative/15 text-negative"
      : tone === "doubt"
        ? "bg-target/15 text-target"
        : "bg-panel text-muted";
  return <span className={`pill ml-auto shrink-0 ${colour}`}>{status}</span>;
}
