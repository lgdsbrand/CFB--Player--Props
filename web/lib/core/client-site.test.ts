import assert from "node:assert/strict";
import { test } from "node:test";

import { BAR_SPORTS, SOON_SPORTS, hisInfoHref, hisSiteHref } from "./client-site.ts";

test("his bar and More menu are his, in his order", () => {
  // As lgdsanalytics.vercel.app showed them on 2026-10-14.
  assert.deepEqual(
    BAR_SPORTS.map((s) => s.name),
    ["Home", "MLB", "Tennis", "NBA", "NFL", "Fantasy", "WNBA", "CFB"],
  );
  assert.deepEqual(SOON_SPORTS.map((s) => s.name), ["Soccer", "UFC", "NHL", "CBB", "PGA Tour"]);
  // NFL and CFB highlight for our two sports (both still open his pages).
  assert.deepEqual(
    BAR_SPORTS.filter((s) => s.football).map((s) => [s.id, s.football]),
    [["nfl", "nfl"], ["cfb", "cfb"]],
  );
});

test("his pages open by address", () => {
  assert.equal(hisSiteHref("mlb"), "https://lgdsanalytics.vercel.app/?sport=mlb");
  assert.equal(hisSiteHref("home"), "https://lgdsanalytics.vercel.app/");
  assert.equal(hisSiteHref(), "https://lgdsanalytics.vercel.app/");
  assert.equal(hisInfoHref("contact"), "https://lgdsanalytics.vercel.app/?open=contact");
  assert.equal(hisSiteHref("nfl", "records"), "https://lgdsanalytics.vercel.app/?sport=nfl&view=records");
});
