/**
 * No link that carries state may be assembled by hand.
 *
 * REPORTED FROM THE LIVE SITE, 2026-09-11, two in one message: pressing WEEK 2
 * on the NFL board landed on college, and opening an NFL player then going back
 * to the board landed on an EMPTY college board. Both were template strings --
 * `${basePath}?season=${season}&week=${week}` in the week strip and
 * `${BOARD_PATH}?season=..&week=..` on the player page. Scanning for that shape
 * then found it on every home tile, every pill and pager on the cheat sheet and
 * no-vig pages, the player page's not-on-slate link, and `playerHref` itself.
 *
 * `sport-reads.test.ts` guards the READS. This guards the LINKS, which is the
 * other half of the same failure: a hand-built URL compiles, typechecks, is a
 * valid address, and lands on a real page full of real rows -- of the other
 * league. Nothing but following it could find one, so the shape is refused.
 *
 * THE RULE: a URL with a query string is built by a helper that knows the sport
 * -- `boardHref`, `scopedHref`, `gamesHref`, `resetBoardHref`, `playerHref`.
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const ROOTS = ["app", "components", "lib"];

/** The helpers themselves, which legitimately assemble query strings. */
const HELPERS = new Set([
  path.join("lib", "core", "board-params.ts"),
  path.join("lib", "core", "player-params.ts"),
]);

/** Deliberate exceptions, each with the reason it is safe. */
const ALLOWED = new Map<string, string>([
  [
    path.join("app", "page.tsx"),
    "forwards a link from before the board moved off `/` by copying EVERY incoming parameter, sport included",
  ],
]);

const PATTERNS: { name: string; re: RegExp }[] = [
  { name: "a template-literal path with a query string", re: /`\/[^`\s]*\?/g },
  { name: "a template-literal base path with a query string", re: /`\$\{\w+\}\?/g },
  { name: "a hand-built player link", re: /`\/player\/\$\{/g },
  { name: "a bare link to the board, which drops the sport", re: /href=\{BOARD_PATH\}/g },
  { name: "URLSearchParams assembled outside a helper", re: /new URLSearchParams\(\)/g },
  { name: "a form posting to the home page", re: /action="\/"/g },
];

/** Comments out, newlines kept, so a comment QUOTING the bad shape is not one. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1");
}

function offences(source: string): string[] {
  const text = code(source);
  const found: string[] = [];
  for (const { name, re } of PATTERNS) {
    for (const match of text.matchAll(re)) {
      const line = text.slice(0, match.index).split("\n").length;
      found.push(`line ${line}: ${name} -- ${match[0]}`);
    }
  }
  return found;
}

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...sourceFiles(full));
    else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

test("the link scanner recognises every shape it refuses", () => {
  // Guards the guard: a scanner that matched nothing would pass vacuously.
  const sample = [
    "<Link href={`/?season=${season}&week=${week}`} />",
    "const url = `${basePath}?season=${season}`;",
    "<Link href={`/player/${id}`} />",
    "<Link href={BOARD_PATH}>board</Link>",
    "const search = new URLSearchParams();",
    '<form action="/">',
  ].join("\n");
  assert.equal(offences(sample).length, PATTERNS.length, offences(sample).join("\n"));
});

test("a comment quoting the bad shape is not an offence", () => {
  assert.deepEqual(offences("// the link was `/games?season=X&week=Y`\nconst x = 1;"), []);
  assert.deepEqual(offences("/* `${basePath}?season=` */"), []);
});

test("the scan covers the files it claims to", () => {
  const files = ROOTS.flatMap((root) => sourceFiles(path.join(WEB, root)));
  assert.ok(files.length >= 60, `only found ${files.length} source files`);
});

test("no internal link carrying state is built by hand", () => {
  const offenders: string[] = [];
  for (const root of ROOTS) {
    for (const file of sourceFiles(path.join(WEB, root))) {
      const relative = path.relative(WEB, file);
      if (HELPERS.has(relative) || ALLOWED.has(relative)) continue;
      for (const offence of offences(readFileSync(file, "utf8"))) {
        offenders.push(`${relative} ${offence}`);
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `build these with boardHref / scopedHref / gamesHref / playerHref, which carry the sport:\n  ${offenders.join("\n  ")}`,
  );
});
