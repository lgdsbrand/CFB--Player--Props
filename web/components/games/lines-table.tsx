import Link from "next/link";

import { TeamChip } from "@/components/board/team-chip";
import { formatKickoff } from "@/lib/core/format";
import {
  formatFair,
  lineMove,
  sharpRetailGap,
  spreadLabel,
  spreadMoveToward,
  totalMoveLabel,
  formatPoints,
  type GameOddsSummary,
} from "@/lib/core/game-lines";
import type { GameSummary } from "@/lib/core/types";

/**
 * The slate as a table of game lines (CLAUDE.md §11, G1) — what the client
 * asked for: a table of games, each one clickable.
 *
 * EVERY NUMBER HERE IS THE MARKET'S. The game model's own projection is a
 * later column (G4). Until then the page says so, rather than letting a
 * Pinnacle probability read as ours.
 *
 * Spread columns name the favourite ("UGA -7.0"); the stored line is from the
 * home team's perspective and is flipped only by `spreadLabel`.
 */
export function LinesTable({
  games,
  odds,
}: {
  games: GameSummary[];
  odds: Map<number, GameOddsSummary>;
}) {
  const priced = games.filter((game) => odds.has(game.gameId)).length;

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">⚡ Game lines</h2>
        <p className="text-muted text-xs">
          Consensus is the median across every book we capture. Sharp is
          Pinnacle; retail is the median of the US books (DraftKings, FanDuel,
          BetMGM, BetRivers, Caesars, Fanatics). Win % is Pinnacle&rsquo;s
          moneyline with the vig removed.{" "}
          <strong className="text-ink">These are market numbers</strong>
          {/* Explicit: a space opening a line after an element is dropped. */}
          {" "}— the game model&rsquo;s projections join this table once it
          has been backtested.
        </p>
      </div>

      {priced === 0 ? (
        <p className="text-muted text-xs">
          No game on this page has been priced yet. Lines are captured Sunday,
          Tuesday, Thursday and Friday evenings and three times on Saturday.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[52rem] border-collapse text-sm">
            <thead>
              <tr className="border-border-subtle border-b">
                <Th>Game</Th>
                <Th>Spread · consensus</Th>
                <Th>Sharp</Th>
                <Th>Retail</Th>
                <Th>Total</Th>
                <Th>Sharp total</Th>
                <Th>Win %</Th>
                <Th right>Books</Th>
              </tr>
            </thead>
            <tbody>
              {games.map((game) => (
                <LineRow
                  key={game.gameId}
                  game={game}
                  odds={odds.get(game.gameId)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      scope="col"
      className={
        "label-caption py-2 pr-3 whitespace-nowrap " + (right ? "text-right" : "text-left")
      }
    >
      {children}
    </th>
  );
}

function LineRow({ game, odds }: { game: GameSummary; odds?: GameOddsSummary }) {
  const home = game.homeAbbreviation ?? game.homeSchool;
  const away = game.awayAbbreviation ?? game.awaySchool;
  const spreads = odds?.spreads;
  const totals = odds?.totals;
  const h2h = odds?.h2h;

  const spreadMove = spreadMoveToward(
    lineMove(spreads?.consensusLine ?? null, spreads?.consensusFirstLine ?? null),
    home,
    away,
  );
  const totalMove = totalMoveLabel(
    lineMove(totals?.consensusLine ?? null, totals?.consensusFirstLine ?? null),
  );
  const gap = sharpRetailGap(spreads);

  // The favourite by Pinnacle's no-vig moneyline; consensus when Pinnacle is
  // absent, and labelled as such so the column never pretends to be sharp.
  const fair = h2h?.sharpFair ?? h2h?.consensusFair ?? null;
  const fairIsSharp = h2h?.sharpFair !== null && h2h?.sharpFair !== undefined;
  const favourite =
    fair === null ? null : fair >= 0.5 ? { team: home, p: fair } : { team: away, p: 1 - fair };

  return (
    <tr className="border-border-subtle/60 hover:bg-panel-inset/40 border-b last:border-0">
      <td className="py-2.5 pr-3">
        <Link
          href={`/games/${game.gameId}`}
          className="group flex flex-col gap-1"
        >
          <span className="flex items-center gap-1.5">
            <TeamChip
              abbreviation={game.awayAbbreviation}
              color={game.awayColor}
              altColor={game.awayAltColor}
              title={game.awaySchool}
            />
            <span className="text-dim text-xs">{game.neutralSite ? "vs" : "@"}</span>
            <TeamChip
              abbreviation={game.homeAbbreviation}
              color={game.homeColor}
              altColor={game.homeAltColor}
              title={game.homeSchool}
            />
            <span className="text-ink group-hover:text-accent-cyan truncate text-xs font-bold transition-colors">
              {game.awaySchool} {game.neutralSite ? "vs" : "@"} {game.homeSchool}
            </span>
          </span>
          <span className="text-dim text-[0.6875rem]">{formatKickoff(game.startDate)}</span>
        </Link>
      </td>

      <td className="py-2.5 pr-3">
        <Cell
          value={spreadLabel(spreads?.consensusLine ?? null, home, away)}
          note={spreadMove ? `moved ${spreadMove.points} to ${spreadMove.team}` : null}
        />
      </td>
      <td className="py-2.5 pr-3">
        <Cell value={spreadLabel(spreads?.sharpLine ?? null, home, away)} />
      </td>
      <td className="py-2.5 pr-3">
        <Cell
          value={spreadLabel(spreads?.retailLine ?? null, home, away)}
          // The side where retail hands out MORE points than Pinnacle: home
          // when retail's home line sits above the sharp one, away otherwise.
          note={
            gap === null
              ? null
              : `+${formatPoints(gap)} on ${gap > 0 ? home : away} vs sharp`
          }
          highlight={gap !== null && Math.abs(gap) >= 1}
        />
      </td>
      <td className="py-2.5 pr-3">
        <Cell
          value={
            totals?.consensusLine !== null && totals?.consensusLine !== undefined
              ? formatPoints(totals.consensusLine)
              : null
          }
          note={totalMove ? `${totalMove}` : null}
        />
      </td>
      <td className="py-2.5 pr-3">
        <Cell
          value={
            totals?.sharpLine !== null && totals?.sharpLine !== undefined
              ? formatPoints(totals.sharpLine)
              : null
          }
        />
      </td>
      <td className="py-2.5 pr-3">
        <Cell
          value={favourite ? `${favourite.team} ${formatFair(favourite.p)}` : null}
          note={favourite && !fairIsSharp ? "consensus" : null}
        />
      </td>
      <td className="text-muted py-2.5 text-right text-xs tabular-nums">
        {spreads?.books ?? h2h?.books ?? totals?.books ?? "—"}
      </td>
    </tr>
  );
}

function Cell({
  value,
  note,
  highlight,
}: {
  value: string | null;
  note?: string | null;
  highlight?: boolean;
}) {
  if (value === null) return <span className="text-dim text-xs">—</span>;
  return (
    <span className="flex flex-col gap-0.5">
      <span
        className={
          "text-sm font-extrabold whitespace-nowrap tabular-nums " +
          (highlight ? "text-target" : "text-ink")
        }
      >
        {value}
      </span>
      {note ? (
        <span className="text-dim text-[0.6875rem] whitespace-nowrap">{note}</span>
      ) : null}
    </span>
  );
}
