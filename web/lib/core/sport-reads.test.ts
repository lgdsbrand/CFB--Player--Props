/**
 * Every page that knows its sport must pass it to the reads that take one.
 *
 * THIS CLASS OF BUG HAS NOW SHIPPED FIVE TIMES AND IS INVISIBLE TO EVERY OTHER
 * CHECK. The reads default to college — `sport: Sport = DEFAULT_SPORT`, or
 * `.eq("sport", filters.sport ?? DEFAULT_SPORT)` — so a call that omits it
 * compiles, typechecks, returns a well-formed result, and renders a page that
 * looks entirely normal. It is just the wrong sport's data, or none.
 *
 * Reported from the live site on 2026-09-08: an NFL game page showed NO player
 * props at all, because `getBoardRows` was called without a sport and queried an
 * NFL game id against `sport='cfb'`. Found the same day: `getDefenseRatings` on
 * the board and the games index, and `getNoVigMarkets` on the no-vig page.
 *
 * Source-level, because there is nothing else to inspect: the failure is an
 * ABSENT argument, and an absent argument has no runtime signature.
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const APP = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "app");

/**
 * Reads whose sport argument silently defaults to college.
 *
 * Listed by hand rather than derived from `lib/data`, so that adding a read with
 * a defaulted sport is a deliberate act that also updates this list. A derived
 * list would grow itself and quietly cover nothing.
 */
const SPORT_AWARE_READS = [
  "getBoardRows",
  "getConferences",
  "getDefenseRatings",
  "getNoVigMarkets",
  "getNoVigPage",
  "getSlateGames",
  "getSlateWeeks",
] as const;

function pageFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...pageFiles(full));
    else if (entry.name === "page.tsx") out.push(full);
  }
  return out;
}

/** The argument text of every call to `name`, paren-balanced. */
function callArguments(source: string, name: string): string[] {
  const calls: string[] = [];
  const needle = `${name}(`;
  let from = 0;
  for (;;) {
    const start = source.indexOf(needle, from);
    if (start === -1) break;
    // Not a call if it is part of a longer identifier (getGame vs getGameLine).
    const before = source[start - 1] ?? " ";
    if (/[A-Za-z0-9_$]/.test(before)) {
      from = start + needle.length;
      continue;
    }
    let depth = 0;
    let i = start + needle.length - 1;
    for (; i < source.length; i++) {
      if (source[i] === "(") depth++;
      else if (source[i] === ")") {
        depth--;
        if (depth === 0) break;
      }
    }
    calls.push(source.slice(start + needle.length, i));
    from = i + 1;
  }
  return calls;
}

test("the call scanner actually finds calls", () => {
  // Guards the guard: a scanner that matched nothing would make the assertion
  // below pass vacuously, which is the exact failure these source tests exist
  // to prevent.
  const files = pageFiles(APP);
  assert.ok(files.length >= 5, `only found ${files.length} pages`);
  const found = files.flatMap((f) =>
    SPORT_AWARE_READS.flatMap((n) => callArguments(readFileSync(f, "utf8"), n)),
  );
  assert.ok(found.length >= 10, `only found ${found.length} calls`);
});

test("every sport-aware read on a page is given a sport", () => {
  const offenders: string[] = [];

  for (const file of pageFiles(APP)) {
    const source = readFileSync(file, "utf8");
    // A page that never resolves a sport cannot pass one, and does not need to.
    if (!source.includes("resolveSport") && !source.includes(".sport")) continue;

    for (const name of SPORT_AWARE_READS) {
      for (const args of callArguments(source, name)) {
        if (!args.includes("sport")) {
          offenders.push(`${path.relative(APP, file)}: ${name}(${args.trim()})`);
        }
      }
    }
  }

  assert.deepEqual(
    offenders,
    [],
    `these reads default to college on a page that knows better:\n  ${offenders.join("\n  ")}`,
  );
});
