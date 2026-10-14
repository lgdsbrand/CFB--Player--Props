/**
 * No URL to one of our own routes may skip `withBasePath`.
 *
 * Once the app is served under `/football` on the client's site, Next.js
 * prefixes `Link`, the router and its own assets, but not a URL built by hand.
 * A `fetch("/builder/pool")` then asks the client's site for a page that does
 * not exist; a share link copies an address that 404s. Both still work on the
 * old address, so nothing short of the moved site would show them. The shape is
 * refused here instead, the way `sport-links.test.ts` refuses a dropped sport.
 *
 * THE RULE: every `fetch` of our own route, every page URL built with
 * `new URL(..., window.location.origin)`, and every metadata image `url:`
 * passes its path through `withBasePath` (lib/core/site.ts).
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { BASE_PATH, withBasePath } from "./site.ts";

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const ROOTS = ["app", "components", "lib"];

/** Comments out, newlines kept, so a comment QUOTING the bad shape is not one. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1");
}

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return files(full);
    return /\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".test.ts") ? [full] : [];
  });
}

/** `fetch(` whose argument is not `withBasePath(...)` or an absolute https URL. */
function offences(source: string): string[] {
  const found: string[] = [];
  const text = code(source);
  for (const match of text.matchAll(/\bfetch\(\s*([^\s)]+)/g)) {
    const arg = match[1];
    if (arg.startsWith("withBasePath(") || /^[`"']https?:/.test(arg)) continue;
    found.push(`fetch(${arg}`);
  }
  for (const match of text.matchAll(/new URL\(\s*([^,)]+),\s*window\.location\.origin/g)) {
    if (!match[1].trim().startsWith("withBasePath(")) found.push(`new URL(${match[1]}, window.location.origin)`);
  }
  for (const match of text.matchAll(/\burl:\s*(`\/|"\/|'\/)/g)) found.push(`url: ${match[1]}...`);
  return found;
}

test("every hand-built URL to our own routes goes through withBasePath", () => {
  // Fonts for the share images come from Google over https: that fetch is
  // outside this app and must NOT be prefixed, which the https rule allows.
  const allowed = new Set([path.join("lib", "og", "fonts.ts")]);
  const bad: string[] = [];
  for (const root of ROOTS) {
    for (const file of files(path.join(WEB, root))) {
      const rel = path.relative(WEB, file);
      for (const hit of offences(readFileSync(file, "utf8"))) {
        if (allowed.has(rel) && hit.startsWith("fetch(url")) continue;
        bad.push(`${rel}: ${hit}`);
      }
    }
  }
  assert.deepEqual(bad, [], `Wrap these in withBasePath (lib/core/site.ts):\n${bad.join("\n")}`);
});

test("the guard catches the shapes it is for", () => {
  assert.deepEqual(offences(`fetch("/builder/pool")`), [`fetch("/builder/pool"`]);
  assert.deepEqual(offences(`fetch(url).then()`), ["fetch(url"]);
  assert.deepEqual(offences(`fetch(withBasePath(url))`), []);
  assert.deepEqual(offences(`fetch("https://fonts.googleapis.com/x")`), []);
  assert.equal(offences(`new URL(pagePath, window.location.origin)`).length, 1);
  assert.equal(offences("const image = { url: `/games/1/share-image` }").length, 1);
});

test("withBasePath prefixes once, and leaves other URLs alone", () => {
  // The test run has no NEXT_PUBLIC_BASE_PATH, so this is the live default.
  assert.equal(BASE_PATH, "/football");
  assert.equal(withBasePath("/games/1"), "/football/games/1");
  assert.equal(withBasePath("/football/games/1"), "/football/games/1");
  assert.equal(withBasePath("/football?sport=nfl"), "/football?sport=nfl");
  assert.equal(withBasePath("/footballers"), "/football/footballers");
  assert.equal(withBasePath("https://x.test/a"), "https://x.test/a");
  assert.equal(withBasePath("//cdn.test/a"), "//cdn.test/a");
});
