import Link from "next/link";

import { TeamChip } from "@/components/board/team-chip";
import type { TeamProps } from "@/lib/core/game-view";
import {
  formatConfidence,
  formatEdge,
  formatLine,
  meetsEdgeThreshold,
} from "@/lib/core/format";
import type { BoardRow } from "@/lib/core/types";

/**
 * One team's props in this game, as a table.
 *
 * A TABLE RATHER THAN THE BOARD'S CARDS, deliberately. The board's job is to
 * present one player-market at a time in the client's card language; this
 * page's job is to let someone read a whole game at once, and thirty stacked
 * cards is a scroll, not a read. The card language stays where it belongs — a
 * player name here is a link into the player detail, which carries the game
 * log, the hit-rate chart and the defense detail.
 *
 * A PLAYER'S MARKETS STAY TOGETHER, ordered by depth chart then by his
 * strongest call (`groupPropsByTeam`). Sorting the whole table by confidence
 * would read as a leaderboard and scatter one quarterback's five markets
 * through it.
 *
 * The OVER/UNDER call and the confidence are the headline (CLAUDE.md §1); the
 * projected median is a caption on the line, never the claim.
 */
export function PropsTable({
  team,
  colors,
  edgeThreshold,
}: {
  team: TeamProps<BoardRow>;
  colors: { color: string | null; altColor: string | null };
  edgeThreshold: number;
}) {
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <TeamChip
          abbreviation={team.abbreviation}
          color={colors.color}
          altColor={colors.altColor}
        />
        <h2 className="section-header min-w-0 flex-1 truncate">{team.school}</h2>
        <span className="label-caption shrink-0">
          {team.isHome ? "Home" : "Away"}
        </span>
      </div>

      {team.players.length === 0 ? (
        <p className="text-muted text-xs">
          No player on this roster clears the usage threshold for a projection
          this week. That is a real answer rather than missing data — the model
          only projects players with enough recent involvement to say anything
          about.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-lg border-collapse text-sm">
            <thead>
              <tr className="border-border-subtle border-b">
                <th scope="col" className="label-caption py-2 pr-3 text-left">
                  Player
                </th>
                <th scope="col" className="label-caption py-2 pr-3 text-left">
                  Market
                </th>
                <th scope="col" className="label-caption py-2 pr-3 text-right">
                  Line
                </th>
                {/* SECONDARY, AND LAST IN READING ORDER AFTER THE CALL — the
                    over/under call and the confidence are the headline
                    (CLAUDE.md §1). It earns its column because through most of
                    a live week most markets have no line, and without it those
                    rows are four em-dashes: the projection is the only thing
                    the model has to say about them yet. */}
                <th scope="col" className="label-caption py-2 pr-3 text-right">
                  Proj
                </th>
                <th scope="col" className="label-caption py-2 pr-3 text-left">
                  Call
                </th>
                <th scope="col" className="label-caption py-2 pr-3 text-right">
                  Conf
                </th>
                <th scope="col" className="label-caption py-2 text-right">
                  Edge
                </th>
              </tr>
            </thead>
            <tbody>
              {team.players.map((player) =>
                player.props.map((row, index) => (
                  <tr
                    key={row.projectionId}
                    className={
                      "border-border-subtle/60 border-b last:border-0 " +
                      // A hairline above each new player, so the grouping is
                      // visible without indenting or repeating the name.
                      (index === 0 ? "border-t-border-subtle border-t" : "")
                    }
                  >
                    <td className="py-2 pr-3">
                      {index === 0 ? (
                        <Link
                          href={`/player/${player.playerId}`}
                          className="text-ink hover:text-accent-cyan font-bold transition-colors"
                        >
                          {player.playerName}
                          <span className="text-dim ml-1.5 text-[0.6875rem] font-semibold">
                            {player.positionGroup ?? ""}
                          </span>
                        </Link>
                      ) : null}
                    </td>
                    <td className="text-muted py-2 pr-3 text-xs font-semibold uppercase tracking-label">
                      {row.marketEmoji ? (
                        <span aria-hidden className="mr-1">
                          {row.marketEmoji}
                        </span>
                      ) : null}
                      {row.marketLabel ?? row.marketName}
                    </td>
                    <td className="text-ink py-2 pr-3 text-right tabular-nums">
                      {row.line !== null ? (
                        formatLine(row.line)
                      ) : (
                        <span className="text-dim">—</span>
                      )}
                    </td>
                    <td className="text-muted py-2 pr-3 text-right tabular-nums">
                      {/* A binary market's projection is a probability, not a
                          count, and the probability is already the Conf column.
                          Printing "0.2" here would invite it to be read as a
                          line. */}
                      {row.isBinary || row.projectedMedian === null ? (
                        <span className="text-dim">—</span>
                      ) : (
                        row.projectedMedian.toFixed(1)
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      <Call row={row} />
                    </td>
                    <td className="py-2 pr-3 text-right">
                      {row.displayConfidence !== null ? (
                        <span className="text-ink font-bold tabular-nums">
                          {formatConfidence(row.displayConfidence)}
                        </span>
                      ) : (
                        <span className="text-dim">—</span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {row.edge !== null ? (
                        <span
                          className={
                            "font-bold tabular-nums " +
                            (meetsEdgeThreshold(row.edge, edgeThreshold)
                              ? "text-positive"
                              : "text-muted")
                          }
                        >
                          {formatEdge(row.edge)}
                        </span>
                      ) : (
                        <span className="text-dim">—</span>
                      )}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/**
 * The OVER/UNDER pill, or the honest absence of one.
 *
 * A binary market (anytime TD) has no side — the probability IS the claim — so
 * it says "to score" rather than borrowing the over/under language and implying
 * a line that does not exist.
 */
function Call({ row }: { row: BoardRow }) {
  if (row.isBinary) {
    return <span className="pill bg-accent-indigo/15 text-accent-indigo">To score</span>;
  }
  if (!row.hasCall || !row.side) {
    return <span className="text-dim text-xs">Lean · no line</span>;
  }
  return (
    <span
      className={
        "pill " +
        (row.side === "over"
          ? "bg-positive/15 text-positive"
          : "bg-negative/15 text-negative")
      }
    >
      {row.side}
    </span>
  );
}
