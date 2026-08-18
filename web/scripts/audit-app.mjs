/**
 * Systematic click-through of the whole deployed app.
 *
 * Every pill control is a real link and the filter row is a GET form, so the
 * board's whole state space is addressable. This walks it: collect the links,
 * read the option values out of the selects, visit everything, and report
 * anything that would count as broken. It then walks the other pages too --
 * games index, game detail, cheat sheets, health, home.
 *
 *   node scripts/audit-app.mjs --base https://cfb-player-props.vercel.app
 *   node scripts/audit-app.mjs --pace 400      # gentler on the database
 *
 * FOUR MISTAKES THIS SCRIPT ALREADY MADE, kept as warnings:
 *
 * 1. The URL keys are not the prop names. Search is `q`, not `search`; the
 *    toggle is `edges`, not `edgesOnly`; confidence is `conf`; opponent rank is
 *    `rank`; the hit-rate window is `window`. A wrong key is IGNORED rather
 *    than rejected, so a filter test with the wrong name silently tests the
 *    unfiltered board and passes.
 * 2. Classifying a URL by "which known param does it contain" buckets
 *    everything as `week`, because every board link carries season and week.
 *    Categories come from DIFFING against the page the link was found on.
 * 3. THE REFERENCE URL WENT STALE AND THE AUDIT SILENTLY SHRANK. It pointed at
 *    `/` long after the board moved to `/props` (the home page now 307s to it).
 *    The walk still "passed" -- it was just auditing a redirect and whatever
 *    the home page happened to link to, and the newer pages were never visited
 *    at all. A stale REF does not fail; it quietly tests less. If you move a
 *    page, move this.
 * 4. THE SCRIPT CAUSED ITS OWN 500s AND REPORTED THEM AS DEFECTS. Visiting ~90
 *    URLs back to back with no gap keeps the free-tier Supabase instance under
 *    sustained load, and its queries then hit `statement_timeout` -- surfacing
 *    as `Read failed (v_slate_weeks): canceling statement due to statement
 *    timeout` and a 500 from an otherwise healthy page. Verified 2026-08-18:
 *    the same URLs served 200 every time when requested at a human pace. Hence
 *    `--pace`, defaulting to a small gap. A 500 from this script means "under
 *    load", not necessarily "broken" -- confirm it serially before believing
 *    it. The underlying capacity limit is real and separate; see
 *    docs/deployment.md.
 */

import { chromium } from "playwright";
import { writeFileSync } from "node:fs";

const arg = (n, d) => {
  const i = process.argv.indexOf(`--${n}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : d;
};
const BASE = arg("base", "https://cfb-player-props.vercel.app");
/** The BOARD. Not `/` -- that is the home page and only redirects here. */
const REF = `${BASE}/props?season=2025&week=8`;
/**
 * Milliseconds between visits. Not politeness: without it this script's own
 * sustained load produces the 500s it then reports. See warning 4 above.
 */
const PACE = Number(arg("pace", "250"));
const rest = () => new Promise((r) => setTimeout(r, PACE));

/** What changed relative to the page this link was found on. */
function classify(url, refUrl) {
  const u = new URL(url, BASE);
  const r = new URL(refUrl, BASE);
  if (u.pathname.startsWith("/player/")) return "player";
  if (u.pathname.startsWith("/games/")) return "game-detail";
  if (u.pathname === "/games") return "games-index";
  if (u.pathname === "/cheat-sheets") return "cheat-sheets";
  if (u.pathname === "/health") return "health";
  if (u.pathname === "/") return "home";
  const keys = new Set([...u.searchParams.keys(), ...r.searchParams.keys()]);
  const diff = [...keys].filter(
    (k) => (u.searchParams.get(k) ?? "") !== (r.searchParams.get(k) ?? ""),
  );
  if (diff.length === 0) return "same";
  if (diff.includes("season")) return "season";
  if (diff.includes("week")) return "week";
  return diff.sort().join("+");
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

const results = [];
const seen = new Set();

async function visit(url, category) {
  if (seen.has(url)) return null;
  seen.add(url);
  if (seen.size > 1) await rest();
  const problems = [];
  const onConsole = (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text().slice(0, 130)}`);
  };
  const onError = (e) => problems.push(`pageerror: ${e.message.slice(0, 130)}`);
  page.on("console", onConsole);
  page.on("pageerror", onError);

  let status = 0;
  let cards = 0;
  let empty = false;
  let overflow = false;
  let glued = [];
  try {
    const res = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });
    status = res ? res.status() : 0;
    cards = await page.locator('a[href^="/player/"]').count();
    const text = await page.locator("body").innerText();
    empty = /No players match|Not configured|No results/i.test(text);
    // A number fused to a word is the JSX-whitespace defect that produced
    // "4effective games" on every opening-weekend board.
    glued = [...new Set(text.match(/[0-9]+[a-z]{4,}/g) || [])];
    overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth + 1,
    );
  } catch (e) {
    problems.push(`nav: ${String(e).slice(0, 150)}`);
  } finally {
    page.off("console", onConsole);
    page.off("pageerror", onError);
  }
  results.push({ url, category, status, cards, empty, overflow, glued, problems });
  return { status, cards, empty };
}

// ---- 1. every link the board offers ----------------------------------------
await visit(REF, "board");
const hrefs = await page.$$eval("a[href]", (as) =>
  as.map((a) => a.getAttribute("href")).filter((h) => h && h.startsWith("/")),
);
const links = [...new Set(hrefs)].map((h) => new URL(h, BASE).toString());

// ---- 2. every option inside the selects, using the REAL url keys ------------
const selects = await page.$$eval("select", (els) =>
  els.map((s) => ({
    name: s.getAttribute("name"),
    values: [...s.options].map((o) => o.value).filter((v) => v !== ""),
  })),
);
const fromSelects = [];
for (const s of selects) {
  if (!s.name) continue;
  // Games are ~60-100 per slate; a sample is enough to prove the control.
  const cap = s.name === "game" ? 8 : 12;
  for (const v of s.values.slice(0, cap)) {
    fromSelects.push(`${REF}&${s.name}=${encodeURIComponent(v)}`);
  }
}
console.log(
  "selects:",
  selects.map((s) => `${s.name}=${s.values.length}`).join(" "),
);

// ---- 3. states no control exposes directly ---------------------------------
const manual = [
  `${REF}&q=smith`,
  `${REF}&q=zzzzznobodyhasthisname`,
  `${REF}&edges=1`,
  `${REF}&conf=0.9`,
  `${REF}&rank=100`,
  `${REF}&window=10`,
  `${REF}&page=2`,
  `${REF}&page=999`,
  `${REF}&position=QB&market=pass_yards`,
  `${REF}&position=ZZ`,
  `${BASE}/props?season=9999&week=1`,
  `${BASE}/player/99999999`,
  `${BASE}/health`,
  `${BASE}/no-such-page`,

  // The pages the audit did not know about until 2026-08-18. Each is a real
  // product surface: the home page tiles, Analyze Games and its per-game card,
  // and the cheat sheet. The 2025 week 8 variants matter because the CURRENT
  // slate cannot exercise them -- the cheat sheet needs four decided games and
  // is legitimately empty until about week 5 of a live season, so auditing it
  // only on the live slate proves nothing.
  `${BASE}/`,
  `${BASE}/games`,
  `${BASE}/games?season=2025&week=8`,
  `${BASE}/games?season=2026&week=1`,
  `${BASE}/cheat-sheets`,
  `${BASE}/cheat-sheets?season=2025&week=8`,
  `${BASE}/games/99999999`,
];

const queue = [
  ...links.map((u) => [u, classify(u, REF)]),
  ...fromSelects.map((u) => [u, classify(u, REF)]),
  ...manual.map((u) => [u, classify(u, REF)]),
];

const perCat = new Map();
for (const [url, cat] of queue) {
  const n = perCat.get(cat) ?? 0;
  // Weeks and players are large but cheap to sample; controls are exhausted.
  const cap = cat === "player" ? 10 : cat === "week" ? 20 : 40;
  if (n >= cap) continue;
  perCat.set(cat, n + 1);
  await visit(url, cat);
}

// ---- 4. a player page, and everything it links to --------------------------
const aPlayer = links.find((u) => u.includes("/player/"));
if (aPlayer) {
  await visit(aPlayer, "player-deep");
  const sub = await page.$$eval("a[href]", (as) =>
    as.map((a) => a.getAttribute("href")).filter((h) => h && h.startsWith("/")),
  );
  for (const h of [...new Set(sub)].slice(0, 14)) {
    await visit(new URL(h, BASE).toString(), "from-player");
  }
}

// ---- 5. the games index, and a real game card -------------------------------
// Its own step rather than a manual URL: the game ids are not guessable, and a
// card that renders only because the index linked to it is the thing worth
// proving. Done for the live slate, which is what a reader opens.
// Navigate EXPLICITLY rather than through `visit`: that helper early-returns on
// a URL it has already seen, which leaves the browser parked on whatever page
// it loaded last, and the scrape below would then collect that page's links
// while looking like it had worked. Same shape as warning 3 -- testing the
// wrong thing without failing.
const gamesIndex = `${BASE}/games?season=2026&week=1`;
await visit(gamesIndex, "games-index");
await page.goto(gamesIndex, { waitUntil: "domcontentloaded", timeout: 60_000 });
const gameLinks = await page.$$eval("a[href^='/games/']", (as) =>
  as.map((a) => a.getAttribute("href")),
);
console.log(`games index links: ${new Set(gameLinks).size} game cards`);
for (const h of [...new Set(gameLinks)].slice(0, 5)) {
  await visit(new URL(h, BASE).toString(), "game-detail");
}

// ---- 6. re-check every 500 serially before calling it a defect --------------
// This script's own pace can exhaust the free-tier database (warning 4). A page
// that serves 200 on a quiet retry was never broken; one that fails twice is.
const retried = [];
for (const r of results.filter((x) => x.status >= 500)) {
  seen.delete(r.url);
  const again = await visit(r.url, `${r.category} (retry)`);
  retried.push({ url: r.url, first: r.status, second: again?.status ?? 0 });
}

await browser.close();

if (retried.length) {
  console.log(`\n===== ${retried.length} 5xx re-checked serially =====`);
  for (const r of retried) {
    const verdict = r.second === 200 ? "TRANSIENT (load)" : "REAL";
    console.log(`  ${r.first} -> ${r.second}  ${verdict}  ${r.url}`);
  }
}

// ---- report ----------------------------------------------------------------
writeFileSync("audit-results.json", JSON.stringify(results, null, 1));
const expected404 = (r) =>
  r.url.includes("/player/99999999") ||
  r.url.includes("/games/99999999") ||
  r.url.includes("/no-such-page");
/** A 5xx that served 200 on the quiet retry was load, not a defect. */
const clearedOnRetry = new Set(
  retried.filter((r) => r.second === 200).map((r) => r.url),
);
const bad = results.filter(
  (r) =>
    !clearedOnRetry.has(r.url) &&
    ((r.status !== 200 && !expected404(r)) ||
      r.problems.some((p) => !expected404(r) || !p.includes("404")) ||
      r.overflow ||
      r.glued.length > 0),
);

console.log(`\n===== ${results.length} URLs visited =====`);
console.log(`problems: ${bad.length}`);
for (const r of bad) {
  console.log(` ${r.status} ${r.category} ${r.url}`);
  r.problems.forEach((p) => console.log(`     ${p}`));
  if (r.overflow) console.log("     horizontal body overflow");
  if (r.glued.length) console.log(`     glued text: ${r.glued.join(", ")}`);
}
console.log("\ncategory                       n   avg cards   empty");
const by = {};
for (const r of results) {
  by[r.category] ??= { n: 0, cards: 0, empty: 0 };
  by[r.category].n++;
  by[r.category].cards += r.cards;
  by[r.category].empty += r.empty ? 1 : 0;
}
for (const [k, v] of Object.entries(by).sort()) {
  console.log(
    `  ${k.padEnd(28)} ${String(v.n).padStart(3)}   ${(v.cards / v.n).toFixed(1).padStart(6)}   ${v.empty}`,
  );
}
