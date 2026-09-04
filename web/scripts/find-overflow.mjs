/**
 * Which element is hanging off the right edge?
 *
 *   node scripts/find-overflow.mjs "/no-vig?season=2026&week=1" 390
 *
 * `shoot.mjs` reports THAT the document scrolls sideways; this reports WHAT is
 * doing it. Written after a one-word CSS change put 37px of overflow on a page
 * that had none, and reading the screenshot could not say which control grew —
 * the same reason `measure.mjs` exists.
 *
 * Prints the widest offenders with their tag, classes and text. An element
 * inside its own `overflow-x-auto` box is excluded: that one is scrolling by
 * design and is not what makes the DOCUMENT scroll.
 */

import { chromium } from "playwright";

const path = process.argv[2] ?? "/";
const width = Number(process.argv[3] ?? 390);
const base = process.env.BASE ?? "http://localhost:3000";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height: 1000 } });
await page.goto(base + path, { waitUntil: "networkidle" });

const report = await page.evaluate((viewport) => {
  const scrolls = (el) => {
    for (let node = el.parentElement; node; node = node.parentElement) {
      const overflow = getComputedStyle(node).overflowX;
      if (overflow === "auto" || overflow === "scroll") return true;
    }
    return false;
  };

  const offenders = [];
  for (const el of document.querySelectorAll("body *")) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.right <= viewport + 0.5) continue;
    if (scrolls(el)) continue;
    offenders.push({
      tag: el.tagName.toLowerCase(),
      over: Math.round((rect.right - viewport) * 10) / 10,
      width: Math.round(rect.width),
      className: String(el.className).slice(0, 90),
      text: (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 60),
    });
  }

  return {
    documentWidth: document.documentElement.scrollWidth,
    offenders: offenders.sort((a, b) => b.over - a.over).slice(0, 12),
  };
}, width);

console.log(`viewport ${width}px · document ${report.documentWidth}px`);
for (const row of report.offenders) {
  console.log(
    `  +${row.over}px  ${row.tag}  w=${row.width}  ${row.className}\n      "${row.text}"`,
  );
}
if (report.offenders.length === 0) console.log("  nothing overflows");

await browser.close();
