"use client";

import { useState } from "react";

import {
  americanToDecimal,
  arbitrage,
  breakEvenOtherSide,
  stakeForProfit,
  type ArbitrageResult,
  type StakeForProfitResult,
} from "@/lib/core/arbitrage";
import { formatMoney } from "@/lib/core/format";

/**
 * The arbitrage calculator's form and result.
 *
 * A CLIENT COMPONENT BECAUSE IT IS A CALCULATOR. There is nothing to fetch and
 * no URL state worth keeping: a reader types three numbers and reads an answer,
 * and round-tripping each keystroke to the server to re-render arithmetic would
 * be slower and would put a reader's stake in their browser history.
 *
 * THE INPUTS ARE TEXT, NOT `type="number"`. A number input silently discards
 * what it cannot parse, so "+1" and a half-typed "-" vanish as they are typed
 * and the field fights the reader. These hold a string, and the parse decides
 * what is usable.
 *
 * WHAT IT REFUSES TO DO. It states no opinion about whether a price is
 * available, never calls the pair a good bet, and reads nothing from our model.
 * Everything on screen is a function of the three numbers in the fields — see
 * `lib/core/arbitrage.ts` for why that boundary is the point.
 */

/** Leading "+" is how books print an underdog, so accept it. */
function parsePrice(raw: string): number | null {
  const text = raw.trim().replace(/^\+/, "");
  if (text === "" || text === "-") return null;
  const value = Number(text);
  if (!Number.isFinite(value)) return null;
  // Between -100 and +100 exclusive is not a price any book posts, and it makes
  // the conversion produce a decimal below 1 (a bet that pays less than the
  // stake). Rejecting it is kinder than computing a confident wrong answer.
  if (value > -100 && value < 100) return null;
  return value;
}

function parseStake(raw: string): number | null {
  const text = raw.trim().replace(/[$,]/g, "");
  if (text === "") return null;
  const value = Number(text);
  if (!Number.isFinite(value) || value <= 0) return null;
  return value;
}

const signedPrice = (price: number) =>
  price > 0 ? `+${Math.round(price)}` : `${Math.round(price)}`;

/**
 * Which direction the calculator runs.
 *
 * `stake` is the one every calculator has: money in, profit out. `profit` is
 * the client's ask of 2026-09-23 and the reason he wanted one of these at all —
 * he knows what he wants to clear and could not find a tool that starts there.
 */
type Mode = "stake" | "profit";

export function ArbCalculator() {
  // Prefilled with a real arb rather than blank fields. A calculator opening
  // empty asks the reader to work out what it wants before it shows them
  // anything; this way the shape of the answer is on screen immediately, and
  // the numbers are obviously placeholders to be replaced.
  const [priceA, setPriceA] = useState("+120");
  const [priceB, setPriceB] = useState("-105");
  const [stake, setStake] = useState("1000");
  const [target, setTarget] = useState("50");
  const [mode, setMode] = useState<Mode>("stake");

  const a = parsePrice(priceA);
  const b = parsePrice(priceB);

  // BOTH AMOUNTS KEEP THEIR OWN FIELD across a mode switch. One shared box
  // would carry a $1,000 stake over into "profit I want", and $1,000 of profit
  // off a pair of two-way prices asks for a five-figure stake — an answer that
  // looks like a bug and is really a field the reader never filled in.
  const total = parseStake(stake);
  const wanted = parseStake(target);

  const result: ArbitrageResult | null =
    mode === "stake" && a !== null && b !== null && total !== null
      ? arbitrage({ priceA: a, priceB: b, stake: total })
      : null;

  // Null covers two different things — an incomplete form and a pair that does
  // not arb — so the view below distinguishes them rather than showing one
  // message for both.
  const plan: StakeForProfitResult | null =
    mode === "profit" && a !== null && b !== null && wanted !== null
      ? stakeForProfit({ priceA: a, priceB: b, targetProfit: wanted })
      : null;

  const pricesReady = a !== null && b !== null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label-caption">Start from</span>
        <ModeTab
          active={mode === "stake"}
          onClick={() => setMode("stake")}
          label="A stake I have"
        />
        <ModeTab
          active={mode === "profit"}
          onClick={() => setMode("profit")}
          label="A profit I want"
        />
      </div>

      <div className="panel grid gap-3 p-4 sm:grid-cols-3">
        <Field
          label="Side A price"
          hint="American odds, e.g. +120"
          value={priceA}
          onChange={setPriceA}
          invalid={priceA.trim() !== "" && a === null}
        />
        <Field
          label="Side B price"
          hint="The other side, usually another book"
          value={priceB}
          onChange={setPriceB}
          invalid={priceB.trim() !== "" && b === null}
        />
        {mode === "stake" ? (
          <Field
            label="Total stake"
            hint="Split across both sides, not per side"
            value={stake}
            onChange={setStake}
            invalid={stake.trim() !== "" && total === null}
          />
        ) : (
          <Field
            label="Profit wanted"
            hint="Cleared whichever side wins, not the two added up"
            value={target}
            onChange={setTarget}
            invalid={target.trim() !== "" && wanted === null}
          />
        )}
      </div>

      {mode === "stake" ? (
        result === null ? (
          <Incomplete what="a stake" />
        ) : (
          <Result result={result} priceA={a!} priceB={b!} stake={total!} />
        )
      ) : plan !== null ? (
        <ProfitPlan plan={plan} priceA={a!} priceB={b!} target={wanted!} />
      ) : pricesReady && wanted !== null ? (
        // THE PRICES ARE FINE AND THE ANSWER IS STILL NO. Worth its own state:
        // the reader asked what it would take, and "nothing takes it" is the
        // true answer, with the price to wait for attached.
        <NoStakeWorks priceA={a!} priceB={b!} />
      ) : (
        <Incomplete what="the profit you want" />
      )}
    </div>
  );
}

function Incomplete({ what }: { what: string }) {
  return (
    <p className="panel text-muted p-4 text-sm">
      Enter two prices and {what}. A price between &minus;100 and +100 is not
      one a book posts, so it is treated as incomplete rather than calculated.
    </p>
  );
}

function ModeTab({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        "rounded-full border px-3 py-1 text-xs font-semibold transition-colors " +
        (active
          ? "border-accent-cyan/60 bg-accent-cyan/10 text-ink"
          : "border-border-subtle text-muted hover:text-ink")
      }
    >
      {label}
    </button>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
  invalid,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (next: string) => void;
  invalid: boolean;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="label-caption">{label}</span>
      <input
        type="text"
        inputMode="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={invalid}
        className={
          "bg-panel-inset text-ink placeholder:text-dim w-full rounded-lg border px-2.5 py-1.5 font-mono text-sm tabular-nums outline-none " +
          (invalid
            ? "border-negative/70"
            : "border-border-subtle focus:border-accent-cyan/60")
        }
      />
      <span className="text-dim text-[0.625rem]">{hint}</span>
    </label>
  );
}

function Result({
  result,
  priceA,
  priceB,
  stake,
}: {
  result: ArbitrageResult;
  priceA: number;
  priceB: number;
  stake: number;
}) {
  const {
    isArbitrage,
    margin,
    returnOnStake,
    stakeA,
    stakeB,
    payoutA,
    payoutB,
    worstPayout,
    worstProfit,
  } = result;

  // ROUNDING CAN UNDO A THIN ARB, so the page checks the rounded split rather
  // than trusting the verdict. An arb whose worse outcome loses money after
  // rounding to cents is not a guarantee, and it must not be presented as one.
  const guaranteed = isArbitrage && worstProfit > 0;

  return (
    <div className="flex flex-col gap-4">
      <div
        className={
          "panel flex flex-wrap items-baseline gap-x-4 gap-y-2 p-4 " +
          (guaranteed ? "border-positive/40" : "")
        }
      >
        {/*
          THREE STATES, BECAUSE THERE ARE THREE. An arb that rounding has eaten
          is not the same answer as a pair that never arbed, and labelling both
          "No arbitrage" put the badge in direct contradiction with the green
          EDGE figure beside it and the amber note below it.
        */}
        <span
          className={
            "rounded-full px-2.5 py-1 text-[0.6875rem] font-bold uppercase tracking-label " +
            (guaranteed
              ? "bg-positive/15 text-positive"
              : isArbitrage
                ? "bg-target/15 text-target"
                : "bg-panel-inset text-muted")
          }
        >
          {guaranteed
            ? "Arbitrage"
            : isArbitrage
              ? "Arb, too thin"
              : "No arbitrage"}
        </span>

        <Stat
          label={isArbitrage ? "Edge" : "Hold"}
          value={`${Math.abs(margin * 100).toFixed(2)}%`}
          tone={isArbitrage ? "positive" : "muted"}
        />
        {guaranteed ? (
          <Stat
            label="Guaranteed return"
            value={`${(returnOnStake * 100).toFixed(2)}%`}
            tone="positive"
          />
        ) : null}
      </div>

      {isArbitrage && !guaranteed ? (
        <p className="border-target/40 bg-target/5 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-target font-bold uppercase tracking-label">
            Too thin to place
          </span>{" "}
          — the prices do arb on paper, but once the stakes round to whole cents
          the worse outcome returns ${formatMoney(worstPayout)} on a stake of $
          {formatMoney(stake)}. A larger stake gives the rounding more room.
        </p>
      ) : null}

      <div className="panel overflow-x-auto">
        <table className="w-full min-w-[32rem] border-collapse text-sm">
          <thead>
            <tr className="text-dim text-left text-[0.625rem] font-bold uppercase tracking-label">
              <th scope="col" className="px-3 py-2 font-bold">
                Side
              </th>
              <th scope="col" className="px-2 py-2 text-right font-bold">
                Price
              </th>
              <th scope="col" className="px-2 py-2 text-right font-bold">
                Stake
              </th>
              <th scope="col" className="px-3 py-2 text-right font-bold">
                Returns if it wins
              </th>
            </tr>
          </thead>
          <tbody>
            <Row
              side="A"
              price={priceA}
              stakeOnSide={stakeA}
              payout={payoutA}
              worst={payoutA === worstPayout}
            />
            <Row
              side="B"
              price={priceB}
              stakeOnSide={stakeB}
              payout={payoutB}
              worst={payoutB === worstPayout}
            />
          </tbody>
        </table>
      </div>

      <p className="text-dim max-w-prose text-xs">
        {guaranteed ? (
          <>
            Both outcomes return more than the ${formatMoney(stake)} staked, so
            the worse one still profits ${formatMoney(worstProfit)}. The two
            stakes are the split of that one stake, not an amount per side.
          </>
        ) : isArbitrage ? (
          <>
            {/* NO BREAK-EVEN SENTENCE HERE. The prices already clear the
                threshold — what failed was the rounding, which the amber note
                above explains. Printing "you would need better than -101 on B"
                for a B already at -100 told the reader to go and find the price
                they were holding. */}
            The stake is what is short here, not the prices. Both sides are
            priced to arb; there is simply not enough room in whole cents at
            this size to hold the guarantee.
          </>
        ) : (
          <>
            {/* WHAT TO WAIT FOR, not just "no". A calculator that only says no
                leaves the reader with nothing to do; the break-even price turns
                the answer into a number they can watch for. */}
            <BreakEven priceA={priceA} priceB={priceB} />
          </>
        )}
      </p>
    </div>
  );
}

/**
 * The answer to "I want to clear $X — what do I put where?"
 *
 * THE HEADLINE IS THE TOTAL STAKE, because that is the number the reader needs
 * before they can act: it is the money that has to be in two accounts at once.
 * The per-side split is the instruction; the total is the decision.
 *
 * NO "TOO THIN" STATE HERE, and its absence is a property of the arithmetic
 * rather than an omission. In the other direction a thin arb breaks: the stake
 * is fixed, so rounding eats the guarantee. Starting from the profit, the stake
 * is free to grow — thinness turns into a bigger number on screen instead of a
 * broken promise, and a four-figure stake for $100 says what a 0.2% edge really
 * costs far better than a warning would.
 */
function ProfitPlan({
  plan,
  priceA,
  priceB,
  target,
}: {
  plan: StakeForProfitResult;
  priceA: number;
  priceB: number;
  target: number;
}) {
  const { margin, totalStake, stakeA, stakeB, payoutA, payoutB, worstPayout } =
    plan;

  return (
    <div className="flex flex-col gap-4">
      <div className="panel border-positive/40 flex flex-wrap items-baseline gap-x-4 gap-y-2 p-4">
        <span className="bg-positive/15 text-positive rounded-full px-2.5 py-1 text-[0.6875rem] font-bold uppercase tracking-label">
          Arbitrage
        </span>
        <Stat
          label="Edge"
          value={`${Math.abs(margin * 100).toFixed(2)}%`}
          tone="positive"
        />
        <Stat
          label="Return on stake"
          value={`${(plan.returnOnStake * 100).toFixed(2)}%`}
          tone="positive"
        />
      </div>

      <div className="panel flex flex-col gap-1 p-4">
        <span className="label-caption">Total stake needed</span>
        <span className="text-ink text-3xl font-extrabold tabular-nums">
          ${formatMoney(totalStake)}
        </span>
        <span className="text-dim text-xs">
          to clear ${formatMoney(target)} whichever side wins
        </span>
      </div>

      <div className="panel overflow-x-auto">
        <table className="w-full min-w-[32rem] border-collapse text-sm">
          <thead>
            <tr className="text-dim text-left text-[0.625rem] font-bold uppercase tracking-label">
              <th scope="col" className="px-3 py-2 font-bold">
                Side
              </th>
              <th scope="col" className="px-2 py-2 text-right font-bold">
                Price
              </th>
              <th scope="col" className="px-2 py-2 text-right font-bold">
                Stake
              </th>
              <th scope="col" className="px-3 py-2 text-right font-bold">
                Returns if it wins
              </th>
            </tr>
          </thead>
          <tbody>
            <Row
              side="A"
              price={priceA}
              stakeOnSide={stakeA}
              payout={payoutA}
              worst={payoutA === worstPayout}
            />
            <Row
              side="B"
              price={priceB}
              stakeOnSide={stakeB}
              payout={payoutB}
              worst={payoutB === worstPayout}
            />
          </tbody>
        </table>
      </div>

      {/*
        THE PROFIT PRINTED IS THE ONE THE SPLIT PAYS, not the one that was
        typed. They are within a cent by construction, and echoing the target
        back would make the page agree with itself rather than report.
      */}
      <p className="text-dim max-w-prose text-xs">
        The worse outcome returns ${formatMoney(worstPayout)} on a stake of $
        {formatMoney(totalStake)}, clearing ${formatMoney(plan.worstProfit)}.
        Stakes round up to the cent, which is why that lands a hair above the $
        {formatMoney(target)} asked for rather than a hair below.
      </p>
    </div>
  );
}

/**
 * Prices that do not arb, asked from the profit direction.
 *
 * SAYING "NO STAKE" MATTERS MORE HERE than in the other direction. A reader who
 * typed a stake can see for themselves that the numbers are against them; one
 * who typed a profit is owed the fact that the question has no answer at any
 * size, because the intuition that a bigger stake fixes it is wrong and costly.
 */
function NoStakeWorks({ priceA, priceB }: { priceA: number; priceB: number }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="panel flex flex-wrap items-baseline gap-x-4 gap-y-2 p-4">
        <span className="bg-panel-inset text-muted rounded-full px-2.5 py-1 text-[0.6875rem] font-bold uppercase tracking-label">
          No arbitrage
        </span>
        {/* The same quantity the No-Vig page prints, from the same function,
            so a reader who checks a pair in both places sees one number. */}
        <Stat
          label="Hold"
          value={`${(
            (1 / americanToDecimal(priceA) + 1 / americanToDecimal(priceB) - 1) *
            100
          ).toFixed(2)}%`}
          tone="muted"
        />
      </div>

      <p className="text-muted max-w-prose text-sm">
        No stake produces a guaranteed profit at these two prices. One side
        always loses, and a bigger stake scales that loss with it rather than
        covering it.
      </p>

      <p className="text-dim max-w-prose text-xs">
        <BreakEven priceA={priceA} priceB={priceB} />
      </p>
    </div>
  );
}

function BreakEven({ priceA, priceB }: { priceA: number; priceB: number }) {
  const neededOnB = breakEvenOtherSide(priceA);
  const neededOnA = breakEvenOtherSide(priceB);

  if (neededOnB === null || neededOnA === null) {
    return <>These two prices do not arb.</>;
  }

  // A BOOK POSTS NOTHING LIKE THIS ON A HEAVY FAVOURITE, and saying so is more
  // use than printing +2050 as though it were around the corner. The threshold
  // is a presentation judgement and lives here rather than in the core.
  const unrealistic = (price: number) => Math.abs(price) > 1000;

  if (unrealistic(neededOnB) && unrealistic(neededOnA)) {
    return (
      <>
        These two prices do not arb, and neither side is close: it would take a
        price no book posts on either.
      </>
    );
  }

  return (
    <>
      These two prices do not arb. Against {signedPrice(priceA)} on A you would
      need better than {signedPrice(neededOnB)} on B; against{" "}
      {signedPrice(priceB)} on B you would need better than{" "}
      {signedPrice(neededOnA)} on A.
    </>
  );
}

function Row({
  side,
  price,
  stakeOnSide,
  payout,
  worst,
}: {
  side: string;
  price: number;
  stakeOnSide: number;
  payout: number;
  worst: boolean;
}) {
  return (
    <tr className="border-border-subtle/60 border-t">
      <td className="text-ink px-3 py-2 font-semibold">Side {side}</td>
      <td className="px-2 py-2 text-right font-mono text-xs tabular-nums">
        {signedPrice(price)}
      </td>
      <td className="text-ink px-2 py-2 text-right font-mono text-sm font-bold tabular-nums">
        ${formatMoney(stakeOnSide)}
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">
        <span className={worst ? "text-ink font-bold" : "text-muted"}>
          ${formatMoney(payout)}
        </span>
      </td>
    </tr>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "positive" | "muted";
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span
        className={
          "text-sm font-extrabold tabular-nums " +
          (tone === "positive" ? "text-positive" : "text-ink")
        }
      >
        {value}
      </span>
      <span className="label-caption">{label}</span>
    </div>
  );
}
