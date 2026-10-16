import assert from "node:assert/strict";
import { test } from "node:test";

import { BOARD_PATH } from "./board-params.ts";
import { FOOTBALL_TOOLS, footballTabs } from "./football-tabs.ts";

const labels = (sport: "cfb" | "nfl") => footballTabs(sport).map((t) => t.label);

test("the tabs are the client's, in his order, for each league", () => {
  assert.deepEqual(labels("cfb"), ["Games", "Records", "Player Props", "Sheets", "No Vig", "Team Analytics"]);
  assert.deepEqual(labels("nfl"), ["Game Cards", "Records", "Player Props", "Fantasy Model", "Sheets", "No Vig"]);
});

test("his views open his site; ours stay relative so the header can add the sport", () => {
  const cfb = Object.fromEntries(footballTabs("cfb").map((t) => [t.label, t]));
  assert.equal(cfb["Team Analytics"].href, "https://lgdsanalytics.vercel.app/?sport=cfb");
  assert.equal(cfb["Team Analytics"].external, true);
  assert.equal(cfb["Records"].href, "/tracker");
  assert.equal(cfb["Player Props"].href, BOARD_PATH);

  const nfl = Object.fromEntries(footballTabs("nfl").map((t) => [t.label, t]));
  assert.equal(nfl["Game Cards"].href, "https://lgdsanalytics.vercel.app/?sport=nfl");
  assert.equal(nfl["Records"].href, "https://lgdsanalytics.vercel.app/?sport=nfl&view=records");
  assert.equal(nfl["Fantasy Model"].href, "/games");
  for (const tab of [...footballTabs("cfb"), ...footballTabs("nfl")]) {
    assert.equal(tab.external, tab.href.startsWith("https://"), tab.label);
  }
});

test("Overview is no longer a tab, and NFL's Records is his, not our tracker", () => {
  for (const sport of ["cfb", "nfl"] as const) {
    assert.ok(!footballTabs(sport).some((t) => t.label === "Overview" || t.href === "/"));
  }
  assert.ok(!footballTabs("nfl").some((t) => t.href === "/tracker"));
});

test("the tools are a menu of their own, in his order, not tabs", () => {
  assert.deepEqual(
    FOOTBALL_TOOLS.map((t) => [t.label, t.href]),
    [["Arb Calculator", "/arbitrage"], ["Slip Builder", "/builder"], ["Slip Check", "/slip-check"]],
  );
  for (const sport of ["cfb", "nfl"] as const) {
    const hrefs = footballTabs(sport).map((t) => t.href);
    for (const tool of FOOTBALL_TOOLS) assert.ok(!hrefs.includes(tool.href), tool.label);
  }
});
