/**
 * Screenshot the running app at real device widths.
 *
 *   npm run dev                      # in one terminal
 *   node scripts/shoot.mjs           # in another
 *   node scripts/shoot.mjs --path "/?season=2025&week=9" --tag board
 *   node scripts/shoot.mjs --width 390 --full
 *
 * WHY THIS EXISTS. CLAUDE.md §7 pins this board to the look of the client's
 * existing MLB pitcher model — exact hex values, tracked uppercase labels,
 * gradient hero numbers. None of that can be verified by reading markup or by
 * asserting on class names; somebody has to look at the pixels. This makes
 * looking cheap and repeatable, and it puts the widths in version control so
 * two people comparing "does the card break on a phone" are comparing the same
 * viewport rather than two different browser windows.
 *
 * Writes PNGs to `screenshots/` (gitignored — they are output, not source).
 *
 * Deliberately NOT a test. It asserts nothing and never fails a build: its
 * output is an image for a human or an agent to read. Visual assertions want
 * golden-file diffing, which is a bigger commitment than this repo needs today.
 */

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";

// Chosen to bracket the real failure points rather than to enumerate devices:
// 390 is a current iPhone, 768 is where Tailwind's `md` lands and where a
// two-column grid first has room, 1440 is a laptop.
const VIEWPORTS = [
  { tag: "phone", width: 390, height: 844, scale: 2 },
  { tag: "tablet", width: 768, height: 1024, scale: 2 },
  { tag: "laptop", width: 1440, height: 900, scale: 1 },
];

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const flag = (name) => process.argv.includes(`--${name}`);

const base = arg("base", "http://localhost:3000");
const target = arg("path", "/?season=2025&week=9");
const tag = arg("tag", "board");
const only = arg("width", null);
const fullPage = flag("full");
const outDir = path.resolve("screenshots");

const viewports = only
  ? VIEWPORTS.filter((v) => String(v.width) === only) .concat(
      VIEWPORTS.some((v) => String(v.width) === only)
        ? []
        : [{ tag: `w${only}`, width: Number(only), height: 900, scale: 2 }],
    )
  : VIEWPORTS;

await mkdir(outDir, { recursive: true });
const browser = await chromium.launch();

try {
  for (const vp of viewports) {
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: vp.scale,
      // The board is a touch surface on a phone; some hover-only affordances
      // render differently and that difference is the point of looking.
      hasTouch: vp.width < 768,
      isMobile: vp.width < 768,
    });
    const page = await context.newPage();

    const problems = [];
    page.on("console", (m) => {
      if (m.type() === "error") problems.push(`console: ${m.text()}`);
    });
    page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));

    const url = `${base}${target}`;
    const response = await page.goto(url, {
      waitUntil: "networkidle",
      timeout: 90_000,
    });

    // A horizontally scrolling BODY is the classic responsive break: the page
    // is wider than the device and everything shifts. Worth reporting next to
    // the image, because it is easy to miss by eye on a scaled-down shot.
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));

    const file = path.join(outDir, `${tag}-${vp.tag}-${vp.width}.png`);
    await page.screenshot({ path: file, fullPage });

    const bleeds = overflow.scrollWidth > overflow.clientWidth + 1;
    console.log(
      `${vp.tag.padEnd(7)} ${String(vp.width).padStart(4)}px  ` +
        `HTTP ${response?.status() ?? "?"}  ` +
        `${bleeds ? `OVERFLOWS by ${overflow.scrollWidth - overflow.clientWidth}px` : "no h-overflow"}  ` +
        `-> ${path.relative(process.cwd(), file)}`,
    );
    for (const p of problems.slice(0, 5)) console.log(`         ${p}`);

    await context.close();
  }
} finally {
  await browser.close();
}
