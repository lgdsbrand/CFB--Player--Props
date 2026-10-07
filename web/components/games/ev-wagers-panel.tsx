import { formatAmericanOdds, formatEdge } from "@/lib/core/format";
import { EV_HIGHLIGHT, fairAmerican, splitWagers, wagerLabel, type EvWager } from "@/lib/core/ev";
import { legFromWager } from "@/lib/core/slip";
import { AddToSlip } from "@/components/slip/add-to-slip";

/**
 * "+EV wagers" for one game (client, 2026-10-06, after the Bettor Odds card):
 * each book price that beats Pinnacle's fair price at the same line, with
 * that fair price and the expected value. Price-based only — it says a book
 * is paying more than the market's fair price, not who wins (migration 0087).
 *
 * The page renders this only before kickoff: the rows are the last capture's,
 * and after kickoff they are history rather than prices anyone can take.
 */

/** Below this the "edge" is inside rounding of the two prices. */
const SHOWN_FROM = 0.005;
const PER_LIST = 8;

const AS_OF = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/New_York",
});

export function EvWagersPanel({
  wagers,
  home,
  away,
  startDate,
}: {
  wagers: EvWager[];
  home: string;
  away: string;
  startDate: string | null;
}) {
  const shown = wagers.filter((w) => w.ev >= SHOWN_FROM);
  const { books, exchanges } = splitWagers(shown);
  const asOf = wagers.reduce<string | null>(
    (latest, w) => (latest === null || w.capturedAt > latest ? w.capturedAt : latest),
    null,
  );
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">💰 +EV wagers</h2>
        <p className="text-muted text-xs">
          Book prices that beat Pinnacle&rsquo;s fair price at the same line.
          Fair is Pinnacle&rsquo;s price with its margin removed (power method);
          EV is the expected return per $1 staked if that fair price is right.{" "}
          {Math.round(EV_HIGHLIGHT * 100)}% or more is highlighted. Exchange
          prices are before their fees.
          {asOf ? ` Prices as of ${AS_OF.format(new Date(asOf))} ET.` : ""} Click
          a price to add it to your bet slip.
        </p>
      </div>

      {books.length === 0 && exchanges.length === 0 ? (
        <p className="text-muted text-xs">
          No book beats Pinnacle&rsquo;s fair price on this game right now.
        </p>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <WagerList
            title="Sportsbooks"
            empty="No sportsbook beats the fair price."
            wagers={books.slice(0, PER_LIST)}
            home={home}
            away={away}
            startDate={startDate}
          />
          <WagerList
            title="Exchanges · before fees"
            empty="No exchange beats the fair price."
            wagers={exchanges.slice(0, PER_LIST)}
            home={home}
            away={away}
            startDate={startDate}
          />
        </div>
      )}
    </section>
  );
}

function WagerList({
  title,
  empty,
  wagers,
  home,
  away,
  startDate,
}: {
  title: string;
  empty: string;
  wagers: EvWager[];
  home: string;
  away: string;
  startDate: string | null;
}) {
  return (
    <div className="bg-panel-inset flex flex-col gap-2 rounded-xl p-3">
      <span className="label-caption">{title}</span>
      {wagers.length === 0 ? (
        <p className="text-dim text-xs">{empty}</p>
      ) : (
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-dim text-left">
              <th className="label-caption pb-1 font-normal">Bet</th>
              <th className="label-caption pb-1 font-normal">Book</th>
              <th className="label-caption pb-1 text-right font-normal">Price</th>
              <th className="label-caption pb-1 text-right font-normal">Fair</th>
              <th className="label-caption pb-1 text-right font-normal">EV</th>
            </tr>
          </thead>
          <tbody>
            {wagers.map((w) => (
              <tr key={`${w.bookKey}-${w.market}-${w.side}`} className="border-border-subtle border-t">
                <td className="text-ink py-1 pr-2 font-bold whitespace-nowrap">
                  {wagerLabel(w, home, away)}
                </td>
                <td className="text-muted py-1 pr-2">{w.bookName}</td>
                <td className="text-ink py-1 text-right font-bold">
                  <AddToSlip leg={legFromWager(w, home, away, startDate)} asPrice>
                    {formatAmericanOdds(w.price)}
                  </AddToSlip>
                </td>
                <td className="text-muted py-1 text-right">{formatAmericanOdds(fairAmerican(w.fairProb))}</td>
                <td
                  className={
                    "py-1 text-right font-extrabold " +
                    (w.ev >= EV_HIGHLIGHT ? "text-target" : "text-muted")
                  }
                >
                  {formatEdge(w.ev)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
