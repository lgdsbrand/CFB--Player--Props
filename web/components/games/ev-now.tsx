import Link from "next/link";

import { formatAmericanOdds, formatEdge } from "@/lib/core/format";
import { EV_HIGHLIGHT, fairAmerican, splitWagers, wagerLabel, type EvWager } from "@/lib/core/ev";
import { legFromWager } from "@/lib/core/slip";
import type { GameSummary } from "@/lib/core/types";
import { AddToSlip } from "@/components/slip/add-to-slip";

/** Per list; the rest are counted, and each game's page lists them all. */
const PER_LIST = 10;

/**
 * "+EV now" above the slate's Lines table (client, 2026-10-06): every book
 * price at least `EV_HIGHLIGHT` above Pinnacle's fair price on the games on
 * screen, sportsbooks first and exchanges apart (before fees). Price-based,
 * migration 0087.
 *
 * It is usually short, and that is the finding rather than a fault: measured
 * on 2026-10-03, sportsbooks cleared 2% seven times across a whole Saturday.
 * The empty state says so plainly instead of hiding the list.
 */
export function EvNow({ wagers, games }: { wagers: EvWager[]; games: GameSummary[] }) {
  const byId = new Map(games.map((g) => [g.gameId, g]));
  const { books, exchanges } = splitWagers(wagers.filter((w) => byId.has(w.gameId)));
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">💰 +EV now</h2>
        <p className="text-muted text-xs">
          Book prices {Math.round(EV_HIGHLIGHT * 100)}% or more above
          Pinnacle&rsquo;s fair price (margin removed, power method) at the same
          line, on the games below, from the latest odds capture. Exchange
          prices are before their fees. Each game&rsquo;s page lists the smaller
          ones too. Click a price to add it to your bet slip.
        </p>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <List
          title="Sportsbooks"
          empty="No sportsbook is that far above the fair price on these games right now."
          wagers={books}
          byId={byId}
        />
        <List
          title="Exchanges · before fees"
          empty="No exchange is that far above the fair price right now."
          wagers={exchanges}
          byId={byId}
        />
      </div>
    </section>
  );
}

function List({
  title,
  empty,
  wagers,
  byId,
}: {
  title: string;
  empty: string;
  wagers: EvWager[];
  byId: Map<number, GameSummary>;
}) {
  return (
    <div className="bg-panel-inset flex flex-col gap-2 rounded-xl p-3">
      <span className="label-caption">{title}</span>
      {wagers.length === 0 ? (
        <p className="text-dim text-xs">{empty}</p>
      ) : (
        <ul className="flex flex-col">
          {wagers.slice(0, PER_LIST).map((w) => {
            const game = byId.get(w.gameId)!;
            const home = game.homeAbbreviation ?? game.homeSchool;
            const away = game.awayAbbreviation ?? game.awaySchool;
            return (
              <li
                key={`${w.gameId}-${w.bookKey}-${w.market}-${w.side}`}
                className="border-border-subtle flex flex-col gap-0.5 border-t py-1.5 text-xs first:border-0 tabular-nums sm:flex-row sm:items-baseline sm:gap-2"
              >
                {/* Two lines on a phone: the bet, then its price. One row from sm up. */}
                <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                  <Link
                    href={`/games/${w.gameId}`}
                    className="text-ink hover:text-accent-cyan font-bold transition-colors"
                  >
                    {wagerLabel(w, home, away)}
                  </Link>
                  <span className="text-dim">{`${away} @ ${home}`}</span>
                  <span className="text-muted">{w.bookName}</span>
                </span>
                <span className="flex items-baseline gap-2 sm:ml-auto">
                  <span className="text-ink font-bold">
                    <AddToSlip leg={legFromWager(w, home, away, game.startDate)} asPrice>
                      {formatAmericanOdds(w.price)}
                    </AddToSlip>
                  </span>
                  <span className="text-dim">{`fair ${formatAmericanOdds(fairAmerican(w.fairProb))}`}</span>
                  <span className="text-target ml-auto w-12 text-right font-extrabold sm:ml-0">
                    {formatEdge(w.ev)}
                  </span>
                </span>
              </li>
            );
          })}
          {wagers.length > PER_LIST ? (
            <li className="text-dim border-border-subtle border-t pt-1.5 text-xs">
              {`and ${wagers.length - PER_LIST} more on the games’ pages`}
            </li>
          ) : null}
        </ul>
      )}
    </div>
  );
}
