import { TeamChip } from "@/components/board/team-chip";
import {
  defenseStatsFor,
  perGame,
  rankBasis,
  matchupBand,
  matchupSoftness,
  type DefenseStat,
} from "@/lib/core/defense-view";
import { formatCount } from "@/lib/core/format";
import type { DefenseGameRow, PositionGroup } from "@/lib/core/types";

/**
 * What this week's opponent has allowed to this player's position, game by game.
 *
 * A HEADLINE FEATURE, NOT A NICE-TO-HAVE (CLAUDE.md §7). It is also the one
 * place the core signal (§5) becomes visible: the position split is computed
 * from play-by-play because no provider serves it, and this is where a reader
 * gets to check it against games they watched.
 *
 * TWO NUMBERS THAT MUST NOT BE CONFUSED. The table is RAW — actual yards
 * conceded, opponent-unadjusted, which is what a reader can verify. The RANK is
 * opponent-ADJUSTED, because a unit that happened to face three weak offences
 * looks elite on raw numbers, and the adjusted figure is the one the model
 * consumes. Both are labelled; neither is presented as the other.
 */
export function DefenseDetail({
  opponentSchool,
  opponentAbbreviation,
  position,
  rows,
  rank,
  rankedDefenses,
  gamesRated,
  highlight,
  asOfWeek,
}: {
  opponentSchool: string;
  opponentAbbreviation: string | null;
  position: PositionGroup;
  rows: DefenseGameRow[];
  rank: number | null;
  rankedDefenses: number;
  gamesRated: number | null;
  /** The column matching the market on screen, emphasised in the table. */
  highlight: DefenseStat | null;
  asOfWeek: number;
}) {
  const stats = defenseStatsFor(position);
  const fraction = rank !== null ? matchupSoftness(rank, rankedDefenses) : null;
  const band = rank !== null ? matchupBand(rank, rankedDefenses) : null;
  const basis = rankBasis(position);

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="section-header flex items-center gap-2">
          <span aria-hidden>🎯</span>
          Defense detail
        </h2>
        <span className="text-muted flex items-center gap-1.5 text-[0.6875rem]">
          <TeamChip
            abbreviation={opponentAbbreviation}
            color={null}
            altColor={null}
            title={opponentSchool}
          />
          vs {position}
        </span>
      </header>

      <div className="panel-inset flex flex-col gap-2 p-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="label-caption">
            Opponent-adjusted rank vs {position}
          </span>
          <span className="text-muted text-[0.625rem]">
            entering week {asOfWeek}
            {gamesRated !== null ? ` · ${gamesRated} games rated` : ""}
          </span>
        </div>

        {/* What the rank is built from. Without this a reader cannot tell a
            rushing rank from a receiving one, which is how a QB rank on the
            wrong column went unnoticed for two phases. */}
        <p className="text-dim text-[0.625rem]">Ranked on {basis.label}.</p>

        {rank !== null ? (
          <>
            <div className="flex items-baseline gap-2">
              <span className="gradient-text text-2xl font-extrabold leading-none tabular-nums">
                {rank}
              </span>
              <span className="text-muted text-xs">
                of {formatCount(rankedDefenses)}
              </span>
              <span
                className={
                  "pill " +
                  (band?.tone === "positive"
                    ? "bg-positive/15 text-positive"
                    : band?.tone === "negative"
                      ? "bg-negative/15 text-negative"
                      : "bg-panel text-muted")
                }
              >
                {band?.label ?? "unranked"}
              </span>
            </div>

            {/* Tough on the left, because rank 1 is the best defense. */}
            {fraction !== null ? (
              <div className="flex flex-col gap-1">
                <div className="bg-panel relative h-1.5 w-full overflow-hidden rounded-full">
                  <span
                    aria-hidden
                    className="bg-ink absolute inset-y-0 w-0.5"
                    style={{ left: `${fraction * 100}%` }}
                  />
                </div>
                <div className="text-dim flex justify-between text-[0.625rem]">
                  <span>1 · allows the least</span>
                  <span>{rankedDefenses} · allows the most</span>
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <p className="text-muted text-xs">
            {rankedDefenses === 0
              ? `No defense in the league carries a rating entering week ${asOfWeek}. ` +
                "Ratings are built from games played this season, so nothing on " +
                "this slate is matchup-adjusted — the projection is this " +
                "player's own form and priors."
              : "No adjusted rating at this cutoff — usually too few games " +
                "behind the week, or a non-FBS opponent."}
          </p>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="text-dim text-xs">
          No games logged for this defense against {position} before week{" "}
          {asOfWeek}.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[26rem] border-collapse text-left text-xs">
            <caption className="text-dim mb-1 text-left text-[0.625rem]">
              Raw totals allowed, opponent-unadjusted — what actually happened.
            </caption>
            <thead>
              <tr className="text-dim [&>th]:px-2 [&>th]:py-1.5 [&>th]:font-semibold [&>th]:uppercase [&>th]:tracking-label [&>th]:text-[0.625rem]">
                <th>Wk</th>
                <th>Offense</th>
                {stats.map((stat) => (
                  <th
                    key={stat.key}
                    className={
                      "text-right " +
                      (stat.key === highlight?.key ? "text-accent-cyan" : "")
                    }
                  >
                    {stat.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.splitId}
                  className="border-border-subtle border-t [&>td]:px-2 [&>td]:py-1.5"
                >
                  <td className="text-muted tabular-nums">{row.week}</td>
                  <td>
                    <span className="text-dim mr-1">
                      {row.neutralSite ? "N" : row.defenseIsHome ? "vs" : "@"}
                    </span>
                    {row.offenseAbbreviation ?? row.offenseSchool}
                  </td>
                  {stats.map((stat) => (
                    <td
                      key={stat.key}
                      className={
                        "text-right tabular-nums " +
                        (stat.key === highlight?.key
                          ? "text-ink font-bold"
                          : "text-muted")
                      }
                    >
                      {stat.value(row) ?? "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-border-strong border-t [&>td]:px-2 [&>td]:py-1.5">
                <td colSpan={2} className="label-caption">
                  Per game
                </td>
                {stats.map((stat) => {
                  const mean = perGame(rows, stat);
                  return (
                    <td
                      key={stat.key}
                      className={
                        "text-right font-bold tabular-nums " +
                        (stat.key === highlight?.key
                          ? "text-accent-cyan"
                          : "text-muted")
                      }
                    >
                      {mean === null ? "—" : mean.toFixed(1)}
                    </td>
                  );
                })}
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {position === "QB" ? (
        <p className="text-dim max-w-prose text-[0.625rem]">
          Quarterback splits are <strong>rushing only</strong>, and so is the
          rank above. The position split exists to break a defense apart by who
          it concedes to, and since the quarterback is the only passer,
          &ldquo;pass yards allowed to QBs&rdquo; would just be team pass
          defense — the single number the split is meant to disaggregate. A soft
          rank here means soft against a <em>running</em> quarterback; it makes
          no claim about pass yards, completions or attempts.
        </p>
      ) : null}
    </section>
  );
}
