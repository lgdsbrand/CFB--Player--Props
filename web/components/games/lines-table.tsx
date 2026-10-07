import Link from "next/link";

import { TeamChip } from "@/components/board/team-chip";
import { formatEdge, formatKickoff, meetsEdgeThreshold } from "@/lib/core/format";
import {
  MISSING_PRIOR_NOTE,
  formatFair,
  formatPoints,
  marketEdge,
  modelSpreadLabel,
  spreadLabel,
  spreadSideLabel,
  totalSideLabel,
  type GameOddsSummary,
  type GameProjection,
  type PricedMarket,
} from "@/lib/core/game-lines";
import type { GameSummary } from "@/lib/core/types";

/**
 * The slate as a table of game lines (CLAUDE.md §11) — the client's layout
 * of 2026-10-06: the book's spread and total, the game model's, and the edge
 * between them, then the win probability.
 *
 * THE BOOK COLUMNS ARE THE LINE THE EDGE WAS PRICED AGAINST, not a consensus.
 * An edge set beside a different number than the one it was computed from
 * would be two claims that disagree on the same row. So the line, its book and
 * the model's probability all come from one `game_projections` row (migration
 * 0082), and the consensus shows only where no listed book priced the game —
 * labelled, and with no edge beside it.
 *
 * Sharp against retail moved to each game's page with the client's change;
 * this table is one opinion per market, not a market survey.
 *
 * Spread labels name a team ("TROY -10.0"); every stored spread is from the
 * home team's side and is flipped only by the label helpers.
 */
export function LinesTable({
  games,
  odds,
  projections,
  edgeThreshold,
}: {
  games: GameSummary[];
  odds: Map<number, GameOddsSummary>;
  projections: Map<number, GameProjection>;
  edgeThreshold: number;
}) {
  const priced = games.filter(
    (game) => odds.has(game.gameId) || projections.get(game.gameId)?.spread,
  ).length;

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">⚡ Game lines</h2>
        <p className="text-muted text-xs">
          Book lines are Pinnacle&rsquo;s, or DraftKings&rsquo; or
          FanDuel&rsquo;s where Pinnacle has none; the book is named under each
          line. Ours are the game model&rsquo;s projected spread and total.{" "}
          <strong className="text-ink">Edge</strong>{" "}
          is the model&rsquo;s
          probability of the side shown minus the book&rsquo;s, with the vig
          removed from the book&rsquo;s two prices; {Math.round(edgeThreshold * 100)}%{" "}
          or more is highlighted. Win % is Pinnacle&rsquo;s moneyline with the
          vig removed. Sharp and retail prices for each game are on its page.
          A game with a team new to FBS shows the book only: the model has no
          previous season for that team.
        </p>
      </div>

      {priced === 0 ? (
        <p className="text-muted text-xs">
          No game on this page has been priced yet. Lines are captured Sunday,
          Tuesday, Thursday and Friday evenings and three times on Saturday.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-240 border-collapse text-sm">
            <thead>
              <tr className="border-border-subtle border-b">
                <Th>Game</Th>
                <Th>Book spread</Th>
                <Th>Our spread</Th>
                <Th>Spread edge</Th>
                <Th>Book total</Th>
                <Th>Our total</Th>
                <Th>Total edge</Th>
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
                  projection={projections.get(game.gameId)}
                  edgeThreshold={edgeThreshold}
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

function LineRow({
  game,
  odds,
  projection,
  edgeThreshold,
}: {
  game: GameSummary;
  odds?: GameOddsSummary;
  projection?: GameProjection;
  edgeThreshold: number;
}) {
  const home = game.homeAbbreviation ?? game.homeSchool;
  const away = game.awayAbbreviation ?? game.awaySchool;
  const spreads = odds?.spreads;
  const totals = odds?.totals;
  const h2h = odds?.h2h;
  const spread = projection?.spread ?? null;
  const total = projection?.total ?? null;
  // A team new to FBS: the book's lines stay, the model's numbers go (see
  // `GameProjection.missingPriorSeason`).
  const model = projection && !projection.missingPriorSeason ? projection : null;
  const noPrior = projection?.missingPriorSeason === true;

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
        {spread ? (
          <Cell value={spreadLabel(spread.line, home, away)} note={spread.bookName} />
        ) : (
          <Cell
            value={spreadLabel(spreads?.consensusLine ?? null, home, away)}
            note="consensus"
          />
        )}
      </td>
      <td className="py-2.5 pr-3">
        {noPrior ? (
          <span className="flex flex-col gap-0.5">
            <span className="text-dim text-xs">—</span>
            <span className="text-dim text-[0.6875rem] whitespace-nowrap">
              {MISSING_PRIOR_NOTE}
            </span>
          </span>
        ) : (
          <Cell
            value={
              model ? modelSpreadLabel(model.periods.full.margin.mean, home, away) : null
            }
          />
        )}
      </td>
      <td className="py-2.5 pr-3">
        <EdgeCell
          market={model ? spread : null}
          label={(side, line) => spreadSideLabel(line, side, home, away)}
          edgeThreshold={edgeThreshold}
        />
      </td>

      <td className="py-2.5 pr-3">
        {total ? (
          <Cell value={formatPoints(total.line)} note={total.bookName} />
        ) : (
          <Cell
            value={
              totals?.consensusLine !== null && totals?.consensusLine !== undefined
                ? formatPoints(totals.consensusLine)
                : null
            }
            note="consensus"
          />
        )}
      </td>
      <td className="py-2.5 pr-3">
        <Cell value={model ? model.periods.full.total.mean.toFixed(1) : null} />
      </td>
      <td className="py-2.5 pr-3">
        <EdgeCell
          market={model ? total : null}
          label={(side, line) => totalSideLabel(line, side)}
          edgeThreshold={edgeThreshold}
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

/**
 * The edge on the side the model prefers, with that side named under it.
 * Highlighted at the edge threshold, as on the props board.
 */
function EdgeCell({
  market,
  label,
  edgeThreshold,
}: {
  market: PricedMarket | null;
  label: (side: "first" | "second", line: number) => string;
  edgeThreshold: number;
}) {
  if (!market) return <span className="text-dim text-xs">—</span>;
  const { side, edge } = marketEdge(market);
  return (
    <span className="flex flex-col gap-0.5">
      <span
        className={
          "font-mono text-sm font-bold whitespace-nowrap tabular-nums " +
          (meetsEdgeThreshold(edge, edgeThreshold) ? "text-target" : "text-muted")
        }
      >
        {formatEdge(edge)}
      </span>
      <span className="text-dim text-[0.6875rem] whitespace-nowrap">
        {label(side, market.line)}
      </span>
    </span>
  );
}

function Cell({
  value,
  note,
}: {
  value: string | null;
  note?: string | null;
}) {
  if (value === null) return <span className="text-dim text-xs">—</span>;
  return (
    <span className="flex flex-col gap-0.5">
      <span className="text-ink text-sm font-extrabold whitespace-nowrap tabular-nums">
        {value}
      </span>
      {note ? (
        <span className="text-dim text-[0.6875rem] whitespace-nowrap">{note}</span>
      ) : null}
    </span>
  );
}
