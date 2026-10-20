import { ComparisonRow } from "@/components/games/team-comparison-panel";
import { MatchupRadar } from "@/components/games/matchup-radar";
import { teamLineup, type LineupEntry } from "@/lib/core/fantasy-view";
import {
  MATCHUP_GROUPS,
  MATCHUP_METRICS,
  injurySummary,
  radarPoints,
  rankMatchup,
  type MatchupInputRow,
  type TeamMatchup,
} from "@/lib/core/matchup-stats";
import type { InjuryRow } from "@/lib/core/starters";

/**
 * The fantasy model's team numbers for an NFL game, as a chart (client,
 * 2026-10-19). A radar of both teams' league ranks for the shape, ranked rows
 * for the values, and each team's starters' CBS points and injury count in its
 * heading. Display only: these are the inputs the projection above already
 * used (`lib/core/matchup-stats.ts`).
 *
 * Starters' points are not ranked: lineups are set team by team as each
 * week's projections post, so mid-week a rank would be among a handful of
 * teams and read as a league rank.
 */
export function MatchupStatsPanel({
  away,
  home,
  awayTeamId,
  homeTeamId,
  rows,
  lineups,
  injuries,
  week,
}: {
  away: string;
  home: string;
  awayTeamId: number;
  homeTeamId: number;
  rows: MatchupInputRow[];
  lineups: LineupEntry[];
  injuries: InjuryRow[];
  week: number;
}) {
  const ranked = rankMatchup(rows, [awayTeamId, homeTeamId]);
  const a = ranked.get(awayTeamId);
  const h = ranked.get(homeTeamId);

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">📊 Matchup stats</h2>
        <p className="text-muted max-w-prose text-xs">
          {/* One string: this compiler drops the space after an expression when
              the text that follows wraps onto the next line ("32NFL teams"). */}
          {`The team numbers the fantasy model used for this game, season to date from games ` +
            `before week ${week}, ranked among ${rows.length > 0 ? rows.length : 32} NFL teams. ` +
            `1st is the best, and for plays per game the most. Not adjusted for opponent, as in ` +
            `the model’s own sheet.`}
        </p>
      </div>

      {!a && !h ? (
        <p className="text-muted text-xs">
          {week <= 1
            ? "No games played yet this season. The numbers start in week 2."
            : "Neither team has numbers entering this week yet."}
        </p>
      ) : (
        <>
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
            <div className="bg-panel-inset rounded-xl p-2">
              <MatchupRadar points={radarPoints(a, h)} away={away} home={home} />
              <p className="text-dim px-2 pb-1 text-center text-[0.6875rem]">
                League rank as a percentile: further out is better on every axis.
              </p>
            </div>

            <div className="flex flex-col gap-1">
              {/* Over the rows, whose sides they name; the radar has its own key. */}
              <div className="grid grid-cols-[1fr_auto_1fr] items-start gap-x-3 pb-1">
                <TeamHead
                  label={away}
                  team={a}
                  teamId={awayTeamId}
                  lineups={lineups}
                  injuries={injuries}
                  align="left"
                />
                <span />
                <TeamHead
                  label={home}
                  team={h}
                  teamId={homeTeamId}
                  lineups={lineups}
                  injuries={injuries}
                  align="right"
                />
              </div>
              {MATCHUP_GROUPS.map((group, i) => (
                <div key={group.key} className="flex flex-col gap-1.5">
                  <h3
                    className={
                      "label-caption border-border-subtle border-b pb-1 text-center " +
                      (i === 0 ? "" : "mt-2")
                    }
                  >
                    {group.label}
                  </h3>
                  {MATCHUP_METRICS.filter((m) => m.group === group.key).map((metric) => (
                    <ComparisonRow
                      key={metric.key}
                      label={metric.label}
                      format={metric.format}
                      away={a?.metrics[metric.key]}
                      home={h?.metrics[metric.key]}
                    />
                  ))}
                </div>
              ))}
            </div>
          </div>

          <p className="text-dim text-[0.6875rem]">
            Offense rating is points a game against an average defense; defense rating is
            points a game it holds an average offense under (higher is better). Both are fitted
            on final scores. SRS is the scoring margin against an average team on a neutral
            field. Red zone TD % is touchdowns per trip inside the 20. Early in a season these
            rest on few games.
          </p>
        </>
      )}
    </section>
  );
}

function TeamHead({
  label,
  team,
  teamId,
  lineups,
  injuries,
  align,
}: {
  label: string;
  team: TeamMatchup | undefined;
  teamId: number;
  lineups: LineupEntry[];
  injuries: InjuryRow[];
  align: "left" | "right";
}) {
  const { slots, total } = teamLineup(lineups, teamId);
  const hurt = injurySummary(injuries.filter((i) => i.teamId === teamId).map((i) => i.status));
  const facts = [
    team ? `from ${team.gamesIncluded} game${team.gamesIncluded === 1 ? "" : "s"}` : "no numbers",
    slots.length > 0 ? `starters ${total.toFixed(1)} CBS pts` : null,
    // "No injuries" only when the week HAS a report: an empty read is not a clean one.
    hurt ?? (injuries.length > 0 ? "no injuries listed" : null),
  ].filter((f): f is string => f !== null);
  return (
    <span className={"flex flex-col " + (align === "right" ? "items-end text-right" : "items-start")}>
      <span className="text-ink text-sm font-extrabold">{label}</span>
      {facts.map((fact) => (
        <span key={fact} className="text-dim text-[0.6875rem]">
          {fact}
        </span>
      ))}
    </span>
  );
}
