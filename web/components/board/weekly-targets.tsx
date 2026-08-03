import Link from "next/link";

import { TeamChip } from "@/components/board/team-chip";
import { boardHref, type BoardParams } from "@/lib/core/board-params";
import { matchupBand } from "@/lib/core/defense-view";
import type { PositionTargets, TargetRow } from "@/lib/core/targets";
import type { TeamInfo } from "@/lib/data/teams";

/**
 * Weekly targets — for each position, the defenses giving up the most to it
 * this week (CLAUDE.md §7).
 *
 * READ IT AS "LOOK AT THESE OFFENSES". Each row is a matchup: a soft defense
 * and the offense facing it. The offense is the actionable half — it is whose
 * skill players a reader goes and looks at — so it leads the row and carries
 * the link through to the board, filtered to that game and position.
 *
 * THE LINK NARROWS TO THE GAME, NOT THE TEAM, and that is deliberate. Both
 * sides' players come through, so a row pointing at Arizona's quarterback lands
 * on a board holding Colorado's too. A team filter would be sharper, but the
 * control set in CLAUDE.md §7 is game / position / market / conference / search
 * — adding a team predicate reachable only from here would be a filter with no
 * control, invisible to anyone who lands on the URL and cannot see why the
 * board is short. The card header carries the team chip, so which side is which
 * is legible on arrival.
 *
 * WHAT THE NUMBERS ARE, STATED RATHER THAN IMPLIED. The rank is national and
 * opponent-adjusted, so "134 of 136" means third-softest in the country, not
 * third on this slate. The per-game figure beside it is the adjusted figure the
 * rank was built from, which is why it will not match a raw stat page — the
 * whole point of the adjustment is that a unit which faced three weak offences
 * should not read as elite (CLAUDE.md §5). Each column names its own basis
 * because they differ: rushing for QB and RB, receiving for WR and TE.
 */
export function WeeklyTargets({
  targets,
  teams,
  params,
  conferenceLabel,
  asOfWeek,
}: {
  targets: PositionTargets[];
  teams: Map<number, TeamInfo>;
  params: BoardParams;
  /** Set when a conference filter is narrowing the list, so it can be said. */
  conferenceLabel: string | null;
  asOfWeek: number;
}) {
  const anyRows = targets.some((entry) => entry.rows.length > 0);

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="section-header flex items-center gap-2">
          <span aria-hidden>🎯</span>
          Weekly targets
        </h2>
        <span className="text-muted text-[0.6875rem]">
          Softest matchups on this week&rsquo;s slate, entering week {asOfWeek}
          {conferenceLabel ? ` · ${conferenceLabel} offenses only` : ""}
        </span>
      </header>

      {anyRows ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {targets.map((entry) => (
            <PositionColumn
              key={entry.position}
              entry={entry}
              teams={teams}
              params={params}
            />
          ))}
        </div>
      ) : (
        // Name the likely cause, and name the RIGHT one: with a filter on, the
        // filter is the explanation, and pointing at week 1 instead would send
        // a reader looking for a data problem that is not there.
        <p className="text-muted max-w-prose text-xs">
          No defense on this slate carries an opponent-adjusted rating entering
          week {asOfWeek}
          {conferenceLabel ? ` against a ${conferenceLabel} offense` : ""}.{" "}
          {conferenceLabel
            ? "No game this week fits that filter — clear it to see the full slate."
            : "Ratings need at least one completed game behind the cutoff, so week 1 has none by construction."}
        </p>
      )}

      <p className="text-dim max-w-prose text-[0.625rem]">
        Rank is national and opponent-adjusted — 1 is the best of all rated
        defenses, not just those playing. The per-game figure is the adjusted
        one the rank was built from, so it will not match a raw stat line.
      </p>
    </section>
  );
}

function PositionColumn({
  entry,
  teams,
  params,
}: {
  entry: PositionTargets;
  teams: Map<number, TeamInfo>;
  params: BoardParams;
}) {
  return (
    <div className="panel-inset flex flex-col gap-2 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-ink text-xs font-extrabold tracking-tight">
          vs {entry.position}
        </span>
        <span className="label-caption">{entry.basis.short}</span>
      </div>

      {entry.rows.length === 0 ? (
        <p className="text-dim text-[0.6875rem]">
          {entry.unrated > 0
            ? `No rating yet for any of the ${entry.unrated} defenses on this slate.`
            : "No matchups on this slate."}
        </p>
      ) : (
        <ol className="flex flex-col gap-1.5">
          {entry.rows.map((row) => (
            <TargetLine
              key={`${row.gameId}-${row.defenseTeamId}`}
              row={row}
              entry={entry}
              teams={teams}
              params={params}
            />
          ))}
        </ol>
      )}

      {/* The QB caveat travels with the QB column, where the number is. A
          footnote at the bottom of the panel would be read after the fact, if
          at all. */}
      {entry.basis.caveat ? (
        <p className="text-dim text-[0.625rem] leading-snug">
          {entry.basis.caveat}
        </p>
      ) : null}

      {entry.rows.length > 0 && entry.unrated > 0 ? (
        <p className="text-dim text-[0.625rem]">
          {entry.unrated} of {entry.onSlate} matchups set aside as unrated.
        </p>
      ) : null}
    </div>
  );
}

function TargetLine({
  row,
  entry,
  teams,
  params,
}: {
  row: TargetRow;
  entry: PositionTargets;
  teams: Map<number, TeamInfo>;
  params: BoardParams;
}) {
  const defense = teams.get(row.defenseTeamId);
  const offense = teams.get(row.offenseTeamId);
  const band = matchupBand(row.rank, entry.rankedDefenses);

  return (
    <li>
      <Link
        href={boardHref(params, {
          game: row.gameId,
          position: entry.position,
          page: 1,
        })}
        className="hover:border-border-strong hover:bg-panel flex items-center gap-2 rounded-lg border border-transparent px-1.5 py-1 transition-colors"
        title={
          `${offense?.school ?? "Offense"} vs ${defense?.school ?? "defense"} — ` +
          `rank ${row.rank} of ${entry.rankedDefenses} on ${entry.basis.label}, ` +
          `${row.gamesRated} games rated`
        }
      >
        <span className="text-dim w-6 shrink-0 text-right text-[0.625rem] font-bold tabular-nums">
          {row.rank}
        </span>

        {/* The offense leads: it is the half a reader acts on. */}
        <TeamChip
          abbreviation={offense?.abbreviation ?? null}
          color={offense?.color ?? null}
          altColor={offense?.altColor ?? null}
          title={offense?.school}
        />

        {/* Read from the OFFENSE, which is the chip on the left — the reverse
            of the defense-detail log, which reads from the defense. Same symbol
            set: N for neutral, where neither side is home. */}
        <span className="text-dim shrink-0 text-[0.625rem]">
          {row.neutralSite ? "N" : row.defenseIsHome ? "@" : "vs"}
        </span>

        <span className="text-muted min-w-0 flex-1 truncate text-[0.6875rem]">
          {defense?.abbreviation ?? defense?.school ?? "—"}
        </span>

        <span
          className={
            "shrink-0 text-[0.6875rem] font-bold tabular-nums " +
            // Soft matchups are the point of this panel, so they are the
            // ones worth highlighting.
            (band?.key === "soft" ? "text-positive" : "text-muted")
          }
        >
          {row.value === null ? "—" : row.value.toFixed(1)}
        </span>
      </Link>
    </li>
  );
}
