/**
 * Measure whether specific controls actually overflow their container.
 *
 *   node scripts/measure.mjs
 *
 * The companion to `shoot.mjs`: that one shows you the pixels, this one gives
 * you the numbers. Written because a screenshot could not settle it — at 768
 * the row counter and the MIN CONFIDENCE field LOOK flush with the container
 * edge, and flush and clipped are one pixel apart by eye. Asking the browser
 * retired two of the four issues on the 2026-08-08 list as non-reproducing,
 * and turned a third from "the week strip looks cut off on a phone" into the
 * real defect: the SELECTED week was off-screen at 390 and 768.
 *
 * Reports, per width: whether a control's right edge passes the container's
 * content edge, how far the caption in a wrapped pill group drifts from the
 * first row, and whether the active week is visible. `board-controls.tsx`
 * cites the caption number, so keep this runnable.
 *
 * Prints; asserts nothing; never fails a build. Do not grow it into an
 * assertion suite — visual regressions want golden-file diffing, which is a
 * bigger commitment than this repo needs.
 */

import { chromium } from "playwright";

const base = process.argv[2] ?? "http://localhost:3000/?season=2025&week=9";
const WIDTHS = [390, 768, 1440];

const browser = await chromium.launch();

for (const width of WIDTHS) {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  await page.goto(base, { waitUntil: "networkidle" });

  const report = await page.evaluate(() => {
    const main = document.querySelector("main");
    const m = main.getBoundingClientRect();
    const style = getComputedStyle(main);
    const padRight = parseFloat(style.paddingRight);
    const contentRight = m.right - padRight;
    const out = { contentRight: Math.round(contentRight), items: [] };

    const note = (label, el) => {
      if (!el) return out.items.push({ label, missing: true });
      const r = el.getBoundingClientRect();
      out.items.push({
        label,
        right: Math.round(r.right),
        over: Math.round(r.right - contentRight),
        scrolls: el.scrollWidth > el.clientWidth + 1,
        overflowBy: el.scrollWidth - el.clientWidth,
      });
    };

    const byText = (sel, re) =>
      [...document.querySelectorAll(sel)].find((e) => re.test(e.textContent));

    note("row counter", byText("span", /^\s*[\d,]+ rows\s*$/));
    note("MIN CONFIDENCE label", byText("span", /^MIN CONFIDENCE$/i));
    const confLabel = byText("span", /^MIN CONFIDENCE$/i);
    note("MIN CONFIDENCE select", confLabel?.parentElement?.querySelector("select"));
    note("week strip", document.querySelector('nav[aria-label="Select week"]'));
    note("OPP RANK select", byText("span", /^OPP RANK/i)?.parentElement?.querySelector("select"));

    // The MARKET label against a wrapped pill group: compare the label's
    // vertical centre with the first row of pills.
    const marketLabel = byText("span", /^MARKET$/i);
    if (marketLabel) {
      const group = marketLabel.parentElement;
      const pills = group.querySelectorAll("a");
      const lr = marketLabel.getBoundingClientRect();
      const first = pills[0].getBoundingClientRect();
      const last = pills[pills.length - 1].getBoundingClientRect();
      out.market = {
        rows: Math.round((last.bottom - first.top) / first.height),
        labelMid: Math.round(lr.top + lr.height / 2),
        firstRowMid: Math.round(first.top + first.height / 2),
        offBy: Math.round(lr.top + lr.height / 2 - (first.top + first.height / 2)),
      };
    }
    // Is the SELECTED week even visible without scrolling? A selector that
    // does not show your selection is a worse problem than a missing fade.
    const strip = document.querySelector('nav[aria-label="Select week"]');
    const current = strip?.querySelector('[aria-current="page"]');
    if (strip && current) {
      const s = strip.getBoundingClientRect();
      const c = current.getBoundingClientRect();
      out.active = {
        visible: c.left >= s.left - 1 && c.right <= s.right + 1,
        leftOffset: Math.round(c.left - s.left),
        scrollLeft: strip.scrollLeft,
        stripWidth: Math.round(s.width),
      };
    } else if (strip) {
      out.active = { noCurrent: true };
    }

    // Pill row height vs label height, to size the alignment nudge instead of
    // guessing it.
    const ml = byText("span", /^MARKET$/i);
    if (ml) {
      const pill = ml.parentElement.querySelector("a");
      out.sizes = {
        pillRow: +pill.getBoundingClientRect().height.toFixed(1),
        label: +ml.getBoundingClientRect().height.toFixed(1),
      };
    }
    return out;
  });

  console.log(`\n=== ${width}px   content right edge = ${report.contentRight}`);
  for (const i of report.items) {
    if (i.missing) {
      console.log(`  ${i.label.padEnd(22)} NOT FOUND`);
      continue;
    }
    const flag = i.over > 0 ? `CLIPPED by ${i.over}px` : "inside";
    const scroll = i.scrolls ? `  scrolls (+${i.overflowBy}px hidden)` : "";
    console.log(`  ${i.label.padEnd(22)} right=${String(i.right).padStart(5)}  ${flag}${scroll}`);
  }
  if (report.market) {
    const { rows, offBy } = report.market;
    console.log(
      `  MARKET label            ${rows} pill row(s), label centre is ${offBy}px below the first row`,
    );
  }
  if (report.sizes) {
    const { pillRow, label } = report.sizes;
    console.log(
      `  sizes                   pill row ${pillRow}px, label ${label}px -> nudge ${((pillRow - label) / 2).toFixed(1)}px`,
    );
  }
  if (report.active) {
    console.log(`  active week             ${JSON.stringify(report.active)}`);
  }
  await page.close();
}

await browser.close();
