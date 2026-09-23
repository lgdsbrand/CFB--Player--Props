import Link from "next/link";

import { ArbCalculator } from "@/components/arbitrage/arb-calculator";
import { SiteHeader } from "@/components/site-header";
import { scopedHref } from "@/lib/core/board-params";
import { resolveSport, SPORT_LABEL } from "@/lib/core/sport";
import type { RawParams } from "@/lib/core/board-params";

/**
 * Arbitrage calculator — the client's ask, 2026-09-21.
 *
 * READS NOTHING. No Supabase client, no config, no slate: the whole page is a
 * function of three numbers the reader types. That is why it needs no
 * `isSupabaseConfigured` guard and why it is the one route here that cannot be
 * affected by a stale odds pool or an empty week.
 *
 * A CALCULATOR, NOT A FINDER, and the distinction is the reason this could ship
 * at all. The client scratched arbitrage in August on the cadence argument —
 * arbs last minutes, the odds budget affords a refresh every six hours — and
 * that argument still stands against a SCANNER of live prices. It says nothing
 * against arithmetic on a pair the reader has already found, which is what he
 * asked for by name. See `lib/core/arbitrage.ts`.
 *
 * WHY IT SITS NEXT TO NO-VIG. The hold and the arb are one quantity read from
 * two directions: two prices whose implied probabilities sum above 1 carry the
 * book's margin, and below 1 carry the reader's. The no-vig page reports the
 * first across the slate; this reports the second for one pair. Same arithmetic,
 * same language, and neither makes a claim about our model.
 */
export default async function Arbitrage({
  searchParams,
}: {
  searchParams: Promise<RawParams>;
}) {
  // Sport is carried even though nothing here varies by it: the header's nav
  // and toggle are built from it, and a page that dropped it would return an
  // NFL reader to the college board on their next click. That is the failure
  // `sport-links.test.ts` exists for, and it has shipped six times.
  const raw = await searchParams;
  const sport = resolveSport(raw.sport);

  return (
    <>
      <SiteHeader activeHref="/arbitrage" sport={sport} />
      <main className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6">
        <div>
          <span className="label-caption">
            Legends Sports · {SPORT_LABEL[sport]}
          </span>
          <h1 className="mt-1 text-2xl font-extrabold tracking-tight">
            Arbitrage Calculator
          </h1>
        </div>

        <p className="border-border-subtle bg-panel/60 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-ink font-bold uppercase tracking-label">
            What this does
          </span>{" "}
          — you give it both sides of a market and it runs either way round:
          from a stake you have, to the profit it guarantees; or from a profit
          you want, to the stake each side needs. Both answers split the money
          so the two outcomes pay the same.{" "}
          <strong className="text-ink">
            It does not look for arbs for you.
          </strong>{" "}
          Prices move in minutes and nothing here is live, so the two numbers
          have to come from the books themselves. For what the books are charging
          across this week&rsquo;s slate, see{" "}
          <Link
            href={scopedHref("/no-vig", { sport })}
            className="text-accent-cyan hover:underline"
          >
            No-Vig
          </Link>
          .
        </p>

        <ArbCalculator />

        <p className="text-dim max-w-prose text-xs">
          An arbitrage exists when the two prices imply less than 100% between
          them. That is the same number the No-Vig page calls hold, with the sign
          flipped: a book keeping 4.8% is the ordinary case, and the pair arbs
          only when that margin lands on your side instead. Books price to avoid
          this, so genuine arbs are thin, short-lived, and usually across two
          books rather than inside one.
        </p>
      </main>
    </>
  );
}

export const metadata = {
  title: "Arbitrage Calculator · Legends Sports",
  description:
    "Check whether two prices guarantee a profit, from a stake you have or a profit you want.",
};
