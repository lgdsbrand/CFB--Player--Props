import type { PositionMatchup, SideMatchup } from "@/lib/core/game-view";

/**
 * What each defense in this game concedes, by position.
 *
 * RANK 1 IS THE BEST DEFENSE, so a HIGH number is the soft matchup. That
 * inversion has caused a real bug in this codebase before — the board's
 * opponent-rank filter selected the exact opposite set of games until it was
 * renamed — so the colour, the word beside the number and the caption all say
 * it, rather than relying on the reader carrying the convention in.
 *
 * BOTH SIDES ALWAYS. The weekly-targets panel shows only the soft half of a
 * matchup, because its question is "whose players should I look at". This page
 * asks what is going on in the game, and a tough defense is half the answer.
 */
export function MatchupGrid({ matchups }: { matchups: PositionMatchup[] }) {
  const anyRated = matchups.some(
    (m) => m.homeDefense.rank !== null || m.awayDefense.rank !== null,
  );

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">🎯 Position matchups</h2>
        <p className="text-muted text-xs">
          What each defense allows to each position, opponent-adjusted and ranked
          nationally. <strong className="text-ink">Rank 1 is the best</strong>{" "}
          defense, so a higher number is the softer matchup.
        </p>
      </div>

      {anyRated ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-md border-collapse text-sm">
            <thead>
              <tr className="border-border-subtle border-b">
                <th scope="col" className="label-caption py-2 pr-3 text-left">
                  Pos
                </th>
                <th scope="col" className="label-caption py-2 pr-3 text-left">
                  {matchups[0]?.awayDefense.defenseAbbreviation ?? "Away"} defense
                </th>
                <th scope="col" className="label-caption py-2 text-left">
                  {matchups[0]?.homeDefense.defenseAbbreviation ?? "Home"} defense
                </th>
                {/* No "measures" column: the unit is already printed under
                    every figure ("56.5 rush yds/g"), and a column repeating it
                    four times was spending a quarter of the table restating
                    what the cells say. */}
              </tr>
            </thead>
            <tbody>
              {matchups.map((matchup) => (
                <tr
                  key={matchup.position}
                  className="border-border-subtle/60 border-b last:border-0"
                >
                  <th
                    scope="row"
                    className="text-ink py-2.5 pr-3 text-left text-xs font-extrabold"
                  >
                    {matchup.position}
                  </th>
                  <td className="py-2.5 pr-3">
                    <RankCell
                      side={matchup.awayDefense}
                      unit={matchup.basis.short}
                      rankedDefenses={matchup.rankedDefenses}
                    />
                  </td>
                  <td className="py-2.5">
                    <RankCell
                      side={matchup.homeDefense}
                      unit={matchup.basis.short}
                      rankedDefenses={matchup.rankedDefenses}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-muted text-xs">
          Neither defense is rated yet. A rating needs at least one completed game
          behind the week&rsquo;s cutoff, so an opening-weekend game has none by
          construction — this is not missing data.
        </p>
      )}

      {/* The QB caveat is a real limit of the position-split engine, not a
          disclaimer: a quarterback is the only player who throws, so "pass
          yards allowed to QBs" would just be team pass defense. Any surface
          showing a QB rank has to say it. */}
      <p className="text-dim text-[0.6875rem]">
        {matchups.find((m) => m.position === "QB")?.basis.caveat}
      </p>
    </section>
  );
}

function RankCell({
  side,
  unit,
  rankedDefenses,
}: {
  side: SideMatchup;
  unit: string;
  rankedDefenses: number;
}) {
  if (side.rank === null) {
    return <span className="text-dim text-xs">Not rated</span>;
  }

  // Thirds of the national field. Named rather than shaded on a gradient so the
  // word carries the meaning for anyone who cannot separate the two hues.
  const soft = rankedDefenses > 0 && side.rank > (rankedDefenses * 2) / 3;
  const tough = rankedDefenses > 0 && side.rank <= rankedDefenses / 3;

  return (
    <span className="flex flex-col gap-0.5">
      <span className="flex items-baseline gap-1.5">
        <span
          className={
            "text-sm font-extrabold tabular-nums " +
            (soft ? "text-positive" : tough ? "text-negative" : "text-ink")
          }
        >
          {side.rank}
        </span>
        <span className="text-dim text-[0.6875rem] tabular-nums">
          of {rankedDefenses || "—"}
        </span>
        {soft ? (
          <span className="label-caption text-positive">Soft</span>
        ) : tough ? (
          <span className="label-caption text-negative">Tough</span>
        ) : null}
      </span>
      <span className="text-dim text-[0.6875rem] tabular-nums">
        {side.value !== null ? `${side.value.toFixed(1)} ${unit.toLowerCase()}` : "—"}
        {side.gamesRated > 0 ? ` · ${side.gamesRated}g` : ""}
      </span>
    </span>
  );
}
