/**
 * The theme tokens, as assertions rather than as judgement.
 *
 * WHY THIS FILE EXISTS. Reskinning to the client's measured palette moved the
 * canvas from #070B14 to #0F172A — lighter, and bluer. Two things broke that a
 * type check, a lint and a build all passed cleanly:
 *
 *   1. `panel` (#0E1420) was picked to sit ABOVE the old canvas. Against the new
 *      one it sits BELOW it, inverting the single relationship CLAUDE.md §7
 *      specifies — panels lift, inset sub-cards recess.
 *   2. `dim` is the colour of every 10px uppercase tracked caption in the
 *      product. Contrast is measured against the background, so lightening the
 *      background DROPPED it, from 2.9:1 to 2.35:1. The reskin would have made
 *      the smallest text in the app harder to read while looking like a pure
 *      colour change.
 *
 * Neither is visible in a diff, and neither is caught by looking at one page.
 * Both are arithmetic. So the ramp is stated here as invariants: any future
 * change to the canvas has to keep the surfaces ordered and the text legible, or
 * this fails and says which token and by how much.
 *
 * It reads `app/globals.css` rather than a TypeScript copy of the values on
 * purpose — a second copy would be the thing that drifts.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// ---------------------------------------------------------------------------
// Colour maths. sRGB + WCAG 2.1 relative luminance, plus the Oklab transform,
// because the tokens are written in oklch — the values are copied verbatim out
// of the client's Tailwind v4 bundle, so ours are identical to theirs rather
// than a hex approximation of theirs.
// ---------------------------------------------------------------------------

type Rgb = [number, number, number];

function gamma(x: number): number {
  return x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
}

function oklchToRgb(L: number, C: number, hDeg: number): Rgb {
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const linear: Rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return linear.map((v) =>
    Math.round(Math.min(Math.max(gamma(v), 0), 1) * 255),
  ) as Rgb;
}

function relativeLuminance([r, g, b]: Rgb): number {
  const [lr, lg, lb] = [r, g, b].map((v) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
}

/** WCAG contrast ratio, 1 (identical) to 21 (black on white). */
function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort(
    (x, y) => y - x,
  );
  return (hi + 0.05) / (lo + 0.05);
}

// ---------------------------------------------------------------------------
// Reading the tokens out of the stylesheet.
// ---------------------------------------------------------------------------

function parseColor(raw: string): Rgb {
  const value = raw.trim();

  const oklch = /^oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*\)$/.exec(value);
  if (oklch) {
    return oklchToRgb(
      Number(oklch[1]) / 100,
      Number(oklch[2]),
      Number(oklch[3]),
    );
  }

  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const digits =
      hex[1].length === 3
        ? hex[1]
            .split("")
            .map((d) => d + d)
            .join("")
        : hex[1];
    return [0, 2, 4].map((i) =>
      Number.parseInt(digits.slice(i, i + 2), 16),
    ) as Rgb;
  }

  throw new Error(`theme token is not a colour this test can read: ${value}`);
}

function readTokens(): Map<string, Rgb> {
  // Run from `web/`, which is where `npm test` runs.
  const css = readFileSync("app/globals.css", "utf8");
  const theme = /@theme\s*\{([\s\S]*?)\n\}/.exec(css);
  assert.ok(theme, "app/globals.css must contain an @theme block");

  const tokens = new Map<string, Rgb>();
  for (const line of theme[1].split("\n")) {
    // Strip trailing `/* … */` annotations before matching.
    const declaration = /^\s*(--color-[a-z-]+)\s*:\s*([^;]+);/.exec(
      line.replace(/\/\*[\s\S]*?\*\//g, ""),
    );
    if (declaration) {
      tokens.set(declaration[1], parseColor(declaration[2]));
    }
  }
  return tokens;
}

const TOKENS = readTokens();

function token(name: string): Rgb {
  const value = TOKENS.get(name);
  assert.ok(value, `expected ${name} in app/globals.css @theme`);
  return value;
}

const canvas = () => token("--color-canvas");
const panel = () => token("--color-panel");
const inset = () => token("--color-panel-inset");

const SURFACES: [string, () => Rgb][] = [
  ["canvas", canvas],
  ["panel", panel],
  ["panel-inset", inset],
];

// ---------------------------------------------------------------------------

test("the canvas is the client's measured background, not our approximation", () => {
  // `:root { --mlb-dark: #0f172a }` on lgdsanalytics.vercel.app. CLAUDE.md §7's
  // own #070B14 is explicitly labelled an approximation to be corrected from
  // the live CSS; this asserts the correction stuck.
  assert.deepEqual(
    canvas(),
    [0x0f, 0x17, 0x2a],
    "canvas must be #0F172A — the value read from the live site",
  );
});

test("panels lift above the canvas and insets recess below their panel", () => {
  const [lCanvas, lPanel, lInset] = [canvas(), panel(), inset()].map(
    relativeLuminance,
  );

  assert.ok(
    lPanel > lCanvas,
    `panel must be lighter than canvas (panel ${lPanel.toFixed(4)} vs canvas ${lCanvas.toFixed(4)})`,
  );
  assert.ok(
    lInset < lPanel,
    `panel-inset must be darker than panel (inset ${lInset.toFixed(4)} vs panel ${lPanel.toFixed(4)})`,
  );
});

test("every surface is distinguishable from every other surface", () => {
  // Including inset-vs-canvas, which is NOT implied by the ordering above. The
  // board's inputs and selects are `bg-panel-inset` sitting directly on the
  // canvas, so an inset that merely equalled the canvas would erase every form
  // field on the page while leaving the nesting rules satisfied.
  for (const [nameA, a] of SURFACES) {
    for (const [nameB, b] of SURFACES) {
      if (nameA >= nameB) continue;
      const ratio = contrast(a(), b());
      assert.ok(
        ratio >= 1.1,
        `${nameA} and ${nameB} are too close to tell apart (${ratio.toFixed(3)}:1, need 1.1)`,
      );
    }
  }
});

test("text tokens stay legible on both the canvas and a panel", () => {
  // `dim` is held to AA-large rather than AA because it carries de-emphasised
  // prose. The smallest text in the product — `.label-caption`, 10px uppercase
  // at 0.12em — is deliberately painted in `muted`, which is held to full AA.
  const minimums: [string, number][] = [
    ["--color-ink", 7],
    ["--color-muted", 4.5],
    ["--color-dim", 3],
  ];

  for (const [name, minimum] of minimums) {
    for (const [surface, background] of SURFACES) {
      const ratio = contrast(token(name), background());
      assert.ok(
        ratio >= minimum,
        `${name} on ${surface} is ${ratio.toFixed(2)}:1, below the ${minimum}:1 this token is held to`,
      );
    }
  }
});

test("semantic colours read as text, not just as fills", () => {
  // positive/negative carry the OVER/UNDER call and the hit dots; target carries
  // the weekly-targets accent. All three are used as `text-*` somewhere, so 3:1
  // against every surface is the floor.
  for (const name of [
    "--color-positive",
    "--color-negative",
    "--color-target",
    "--color-accent-cyan",
  ]) {
    for (const [surface, background] of SURFACES) {
      const ratio = contrast(token(name), background());
      assert.ok(
        ratio >= 3,
        `${name} on ${surface} is ${ratio.toFixed(2)}:1, below 3:1`,
      );
    }
  }
});

test("one border colour reads against all three surfaces", () => {
  // The point of a solid slate border rather than white-at-low-alpha: our ramp
  // spans slate-950 to slate-800, which is narrow enough that a single mid
  // slate edges every surface. If a future canvas widens the ramp, this is the
  // check that says the single border no longer works.
  for (const name of ["--color-border-subtle", "--color-border-strong"]) {
    for (const [surface, background] of SURFACES) {
      const ratio = contrast(token(name), background());
      assert.ok(
        ratio >= 1.25,
        `${name} is invisible on ${surface} (${ratio.toFixed(3)}:1, need 1.25)`,
      );
    }
  }
});

test("the brand blue is the client's, and carries white text", () => {
  assert.deepEqual(
    token("--color-brand"),
    [0x00, 0x8c, 0xff],
    "brand must be #008CFF — the fill on the live site's primary buttons",
  );
  const onBrand = contrast([0xff, 0xff, 0xff], token("--color-brand"));
  assert.ok(
    onBrand >= 3,
    `white on the brand blue is ${onBrand.toFixed(2)}:1 — the CTA label would be hard to read`,
  );
});
