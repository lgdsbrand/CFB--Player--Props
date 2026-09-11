/**
 * Walk the running app like a reader and report every path that breaks.
 *
 *   npm run dev                                   # one terminal, pointed at PROD data
 *   node scripts/crawl.mjs                        # both sports, everything reachable
 *   node scripts/crawl.mjs --sport nfl --max 150  # narrower
 *   node scripts/crawl.mjs --no-interact          # links only, no clicking
 *
 * WHY THIS EXISTS. "The toggle flips back to college" has shipped more times
 * than any other defect in this app, and every time `tsc`, the unit tests and a
 * screenshot of the page were all green. A dropped `?sport=` renders a normal,
 * well-formed page -- of the other league. Only following the link finds it, so
 * this follows every link.
 *
 * WHAT IT CHECKS, on every page it lands on:
 *   SPORT_LOST      the header toggle shows a different sport from the one the
 *                   reader was in. Sport is inherited along every link except
 *                   the toggle itself, which is the only thing allowed to change it.
 *   HTTP_4xx/5xx    the response status.
 *   JS_ERROR        console errors and uncaught exceptions (hydration included).
 *   ERROR_TEXT      an error page rendered with a 200.
 *   EMPTY           an empty-state message. Not always a bug -- reported so a
 *                   human decides, and always a bug when it follows SPORT_LOST.
 *   H_OVERFLOW      the document scrolls sideways.
 *
 * AND WHAT IT DOES, once per page shape per sport: every <select> in the page
 * body (three options each), every <button>, the search box, and two workflows
 * -- open a player then press Back, and open a player then use the page's own
 * back link.
 *
 * Deliberately serial. The board 500s at 12 concurrent readers against
 * production, and a crawl that manufactured its own failures would be worthless.
 *
 * Writes `screenshots/crawl-report.json` (gitignored).
 */

import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const BASE = arg("base", "http://localhost:3000");
const MAX_PAGES = Number(arg("max", "600"));
// Visits per page SHAPE (path with ids masked + the set of query keys). The
// board has thousands of filter combinations; two of each shape exercise the
// link-building code without re-rendering the same template a thousand times.
const PER_SHAPE = Number(arg("per", "2"));
const ONLY_SPORT = arg("sport", null);
const INTERACT = !process.argv.includes("--no-interact");
const OUT = path.resolve("screenshots", arg("out", "crawl-report.json"));

const TOGGLE = { NCAAF: "cfb", NFL: "nfl" };
const SPORTS = ONLY_SPORT ? [ONLY_SPORT] : ["cfb", "nfl"];
// Addresses a reader reaches WITHOUT clicking anything here: shared, bookmarked,
// truncated or hand-edited links. `--edges` starts from these instead. Each must
// land on a real page in the right sport -- a stale link that 500s, or quietly
// switches league, is the same bug as a broken button.
const EDGE_PATHS = [
  "/?season=2026&week=2", // a board link from before the board moved off `/`
  "/props?week=99", // a week that does not exist
  "/props?page=999", // a page past the end
  "/props?position=LB&market=nope&sort=bogus&view=grid", // values the parser drops
  "/props?preset=best&page=50",
  "/props?preset=edges",
  "/no-vig?page=999&sort=bogus",
  "/cheat-sheets?window=7&position=LB",
  "/games?conference=Nowhere&day=2020-01-01",
  "/games/999999999", // no such game: a 404, never a 500
  "/player/999999999", // no such player
  "/player/abc",
];
const START_PATHS = process.argv.includes("--edges")
  ? EDGE_PATHS
  : ["/", "/props", "/games", "/cheat-sheets", "/no-vig"];

// The app's own empty-state headings, copied from the pages rather than
// guessed -- a phrase that never appears would report every empty page as full.
const EMPTY_PHRASES = [
  "No slate yet", // every index page, no week at all
  "Nothing projected for", // player page, off the slate
  "No players match", // board, filters matched nothing
  "Nothing priced yet", // board edges-only / cheat sheet
  "No edges this week", // board edges-only
  "No games here", // games index, conference filter
  "Too early in the season", // cheat sheet
  "No games to grade yet", // cheat sheet
  "Nothing clears the bar", // cheat sheet
  "No two-way prices yet", // no-vig
  "Nothing matches", // no-vig
];
const ERROR_PHRASES = [
  // `app/error.tsx`. A styled error page is still an error page; a substring,
  // so the curly apostrophe in "didn’t" cannot make it miss.
  "This page didn",
  "Application error",
  "Something went wrong",
  "Unhandled Runtime Error",
  "This page could not be found",
  "Internal Server Error",
  "Build Error",
];

const withSport = (p, sport) =>
  sport === "cfb" ? p : `${p}${p.includes("?") ? "&" : "?"}sport=${sport}`;

function normalise(href) {
  const u = new URL(href);
  u.hash = "";
  u.searchParams.sort();
  return u.toString();
}

function shape(href) {
  const u = new URL(href);
  const p = u.pathname.replace(/\/\d+(?=\/|$)/g, "/:id");
  const keys = [...new Set(u.searchParams.keys())].filter((k) => k !== "sport").sort();
  return `${p}?${keys.join("&")}`;
}

/** The path with ids masked and no query: which PAGE a link sat on. */
function pathOf(href) {
  return new URL(href).pathname.replace(/\/\d+(?=\/|$)/g, "/:id");
}

const findings = [];
const record = (kind, ctx, detail = "") => {
  findings.push({ kind, sport: ctx.sport, url: ctx.url, from: ctx.from, via: ctx.via, detail });
  console.log(`   !! ${kind} ${detail}`.slice(0, 220));
};

// `--width 390` crawls as a phone: touch, mobile UA, and a viewport where the
// sideways-scrolling document this site has shipped twice actually shows up.
const WIDTH = Number(arg("width", "1440"));
const PHONE = WIDTH < 768;

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: WIDTH, height: PHONE ? 844 : 900 },
  hasTouch: PHONE,
  isMobile: PHONE,
});
const page = await context.newPage();

let consoleErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(`console: ${m.text().slice(0, 300)}`);
});
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message.slice(0, 300)}`));

async function settle(before) {
  if (before) {
    try {
      await page.waitForURL((u) => u.toString() !== before, { timeout: 8_000 });
    } catch {}
  }
  try {
    await page.waitForLoadState("networkidle", { timeout: 90_000 });
  } catch {}
}

async function load(url) {
  consoleErrors = [];
  try {
    const response = await page.goto(url, { waitUntil: "networkidle", timeout: 150_000 });
    return { status: response?.status() ?? null };
  } catch (error) {
    return { status: null, error: String(error.message).split("\n")[0] };
  }
}

async function inspect() {
  return page.evaluate(
    ({ EMPTY_PHRASES, ERROR_PHRASES }) => {
      const header = document.querySelector("header");
      const toggle = header
        ? [...header.querySelectorAll("a[aria-current='true']")]
            .map((a) => (a.textContent || "").trim())
            .find((t) => t === "NCAAF" || t === "NFL") ?? null
        : null;
      const text = document.body.innerText || "";
      const links = [...document.querySelectorAll("a[href]")].map((a) => {
        const label = (a.textContent || "").trim().replace(/\s+/g, " ");
        return {
          href: a.href,
          text: label.slice(0, 60),
          toggle: !!(a.closest("header") && (label === "NCAAF" || label === "NFL")),
          label,
        };
      });
      return {
        toggle,
        h1: document.querySelector("h1")?.textContent?.trim().slice(0, 80) ?? null,
        empty: EMPTY_PHRASES.filter((p) => text.includes(p)),
        errors: ERROR_PHRASES.filter((p) => text.includes(p)),
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        links,
      };
    },
    { EMPTY_PHRASES, ERROR_PHRASES },
  );
}

function checkPage(ctx, info, status) {
  if (status && status >= 400) record(`HTTP_${status}`, ctx);
  for (const e of consoleErrors) {
    // A 404 document logs its own status as a failed resource. That is the
    // HTTP_404 above restated, not a second fault.
    if (status && status >= 400 && e.includes("Failed to load resource")) continue;
    record("JS_ERROR", ctx, e);
  }
  consoleErrors = [];
  for (const e of info.errors) record("ERROR_TEXT", ctx, e);
  if (!info.toggle) record("NO_SPORT_TOGGLE", ctx);
  else if (TOGGLE[info.toggle] !== ctx.sport) {
    record("SPORT_LOST", ctx, `reader was in ${ctx.sport}, page shows ${TOGGLE[info.toggle]}`);
  }
  if (info.empty.length) record("EMPTY", ctx, info.empty.join(" | "));
  if (info.overflow > 1) record("H_OVERFLOW", ctx, `${info.overflow}px`);
}

// --- the crawl ---------------------------------------------------------------
const queue = [];
for (const sport of SPORTS) {
  for (const p of START_PATHS) {
    queue.push({ url: `${BASE}${withSport(p, sport)}`, sport, from: null, via: "start" });
  }
}
const seen = new Set(queue.map((q) => `${q.sport}|${normalise(q.url)}`));
const shapeVisits = new Map();
const interacted = new Set();
let visited = 0;

function enqueue(ctx, links) {
  for (const link of links) {
    if (!link.href.startsWith(BASE)) continue;
    const u = new URL(link.href);
    if (u.pathname.startsWith("/_next")) continue;
    const sport = link.toggle ? TOGGLE[link.label] : ctx.sport;
    const key = `${sport}|${normalise(link.href)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    queue.push({
      url: normalise(link.href),
      sport,
      from: ctx.url,
      // Which PAGE the link sat on. Part of the shape key below, because the
      // same destination shape reached from two pages is two pieces of
      // link-building code -- the home tiles and the week strip both build
      // `/props?season&week`, and capping on the destination alone meant the
      // week strip used up the quota and the tiles were never followed.
      fromPath: pathOf(ctx.url),
      via: `link "${link.text}"`,
    });
  }
}

async function afterAction(ctx, via) {
  const actionCtx = { ...ctx, url: page.url(), from: ctx.url, via };
  const info = await inspect();
  checkPage(actionCtx, info, null);
  enqueue(actionCtx, info.links);
}

async function interact(ctx) {
  const pageUrl = ctx.url;

  const selects = await page.$$eval("main select", (els) =>
    els.map((el, i) => ({
      i,
      name: el.name || el.getAttribute("aria-label") || el.id || `select#${i}`,
      value: el.value,
      options: [...el.options].map((o) => o.value),
    })),
  );
  for (const select of selects) {
    for (const value of select.options.filter((v) => v !== select.value).slice(0, 3)) {
      await load(pageUrl);
      const before = page.url();
      try {
        await page.locator("main select").nth(select.i).selectOption(value, { timeout: 10_000 });
      } catch (error) {
        record("ACTION_FAILED", ctx, `select ${select.name}=${value}: ${String(error.message).split("\n")[0]}`);
        continue;
      }
      await settle(before);
      await afterAction(ctx, `select ${select.name}=${value}`);
    }
  }

  const buttons = await page.$$eval("main button", (els) =>
    els.map((el, i) => ({ i, text: (el.textContent || "").trim().slice(0, 40), disabled: el.disabled })),
  );
  for (const button of buttons) {
    if (button.disabled) continue;
    await load(pageUrl);
    const before = page.url();
    try {
      await page.locator("main button").nth(button.i).click({ timeout: 10_000 });
    } catch (error) {
      record("ACTION_FAILED", ctx, `button "${button.text}": ${String(error.message).split("\n")[0]}`);
      continue;
    }
    await settle(before);
    await afterAction(ctx, `button "${button.text}"`);
  }

  const search = page.locator("main input[name='q']");
  if ((await search.count()) > 0) {
    await load(pageUrl);
    const before = page.url();
    await search.first().fill("a");
    await page.waitForTimeout(1_500);
    await settle(before);
    await afterAction(ctx, `search "a"`);
  }
}

async function playerRoundTrip(ctx) {
  await load(ctx.url);
  const player = page.locator("main a[href^='/player/']").first();
  if ((await player.count()) === 0) return;
  const before = page.url();
  await player.click();
  await settle(before);
  const playerUrl = page.url();
  await afterAction(ctx, "workflow: open a player");

  await page.goBack();
  await settle(playerUrl);
  await afterAction({ ...ctx, url: playerUrl }, "workflow: player -> browser Back");

  await load(playerUrl);
  const back = page.locator("main a", { hasText: /back to/i }).first();
  if ((await back.count()) > 0) {
    await back.click();
    await settle(playerUrl);
    await afterAction({ ...ctx, url: playerUrl }, "workflow: player -> page's back link");
  } else {
    record("NO_BACK_LINK", { ...ctx, url: playerUrl });
  }
}

// DIVERSITY FIRST, NOT FIFO. A plain queue spent 457 of 500 NFL pages on /props
// filter combinations -- every board page links to ~50 others, so they flood
// the queue -- and reached 8 player pages and 2 game pages, which is where both
// bugs the client's contact reported actually lived. Take the queued page whose
// PATH (ids masked) has been visited least, so every kind of page is reached
// before any one kind is explored in depth.
const pathVisits = new Map();
function nextItem() {
  let best = 0;
  let bestScore = Infinity;
  for (let i = 0; i < queue.length; i++) {
    const score = pathVisits.get(`${queue[i].sport}|${pathOf(queue[i].url)}`) ?? 0;
    if (score < bestScore) {
      best = i;
      bestScore = score;
      if (score === 0) break;
    }
  }
  return queue.splice(best, 1)[0];
}

while (queue.length > 0 && visited < MAX_PAGES) {
  const ctx = nextItem();
  const shapeKey = `${ctx.sport}|${ctx.fromPath ?? ""}->${shape(ctx.url)}`;
  const count = shapeVisits.get(shapeKey) ?? 0;
  if (ctx.via !== "start" && count >= PER_SHAPE) continue;
  shapeVisits.set(shapeKey, count + 1);
  visited += 1;
  const pathKey = `${ctx.sport}|${pathOf(ctx.url)}`;
  pathVisits.set(pathKey, (pathVisits.get(pathKey) ?? 0) + 1);

  const started = Date.now();
  const { status, error } = await load(ctx.url);
  console.log(
    `[${visited}] ${ctx.sport} ${ctx.url.replace(BASE, "")}  HTTP ${status ?? "-"}  ${Date.now() - started}ms  (${ctx.via})`.slice(0, 240),
  );
  if (error) {
    record("LOAD_FAILED", ctx, error);
    continue;
  }
  const info = await inspect();
  checkPage(ctx, info, status);
  enqueue(ctx, info.links);

  const pathShape = `${ctx.sport}|${new URL(ctx.url).pathname.replace(/\/\d+(?=\/|$)/g, "/:id")}`;
  if (INTERACT && !interacted.has(pathShape)) {
    interacted.add(pathShape);
    await interact(ctx);
    if (["/props", "/games/:id", "/cheat-sheets", "/no-vig"].some((p) => pathShape.endsWith(`|${p}`))) {
      await playerRoundTrip(ctx);
    }
  }
}

await browser.close();

const byKind = {};
for (const f of findings) (byKind[f.kind] ??= []).push(f);
const summary = Object.fromEntries(Object.entries(byKind).map(([k, v]) => [k, v.length]));
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify({ visited, left: queue.length, summary, findings }, null, 2));
console.log(`\nvisited ${visited} page(s), ${queue.length} still queued`);
console.log(JSON.stringify(summary, null, 2));
console.log(`-> ${path.relative(process.cwd(), OUT)}`);
