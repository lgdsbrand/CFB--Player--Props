import assert from "node:assert/strict";
import { test } from "node:test";

import type { CheatSheetRow } from "./cheat-sheet.ts";
import { shareSheetView, streakLabel } from "./share-sheet.ts";

function row(overrides: Partial<CheatSheetRow> = {}): CheatSheetRow {
  return {
    projectionId: 1,
    season: 2026,
    week: 7,
    playerId: 100,
    playerName: "Taylen Green",
    positionGroup: "QB",
    teamId: 8,
    teamSchool: "Arkansas",
    teamAbbreviation: "ARK",
    teamColor: "#9d2235",
    teamAltColor: "#ffffff",
    opponentSchool: "Auburn",
    opponentAbbreviation: "AUB",
    opponentRankVsPosition: 44,
    gameId: 900,
    startDate: "2026-10-17T16:00:00+00:00",
    isHome: true,
    neutralSite: false,
    marketKey: "rush_yards",
    marketLabel: "RUSH YDS",
    marketEmoji: "🏈",
    isBinary: false,
    line: 52.5,
    modelSide: "under",
    displayConfidence: 0.53,
    edge: null,
    hasCall: true,
    hasBookLine: true,
    sportsbookKey: "fanduel",
    windowSize: 5,
    decided: 5,
    pushes: 0,
    hits: 5,
    hitSide: "over",
    hitRate: 1,
    ...overrides,
  };
}

test("a streak reads as the bet: an over is the alternate line, an under keeps its line", () => {
  assert.equal(streakLabel(row()), "53+ RUSH YDS");
  assert.equal(streakLabel(row({ line: 60 })), "61+ RUSH YDS");
  assert.equal(streakLabel(row({ hitSide: "under" })), "Under 52.5 RUSH YDS");
  assert.equal(streakLabel(row({ isBinary: true, marketKey: "anytime_td", marketLabel: "ATD", line: 0.5 })), "Anytime TD");
});

test("the view: title by tier, filters in the subtitle, the model's stance on every row", () => {
  const rows = [row(), row({ playerName: "B", modelSide: "over" }), row({ playerName: "C", hasCall: false, modelSide: null })];
  const view = shareSheetView({
    rows, sportLabel: "NCAAF", week: 7, windowSize: 5, tier: "perfect", position: "QB", side: "over",
  });
  assert.equal(view.title, "100% Hit Rates");
  assert.equal(view.subtitle, "NCAAF Week 7 · Last 5 games · QB · Overs");
  assert.deepEqual(view.rows.map((r) => r.agreement), ["disagrees", "agrees", "no-call"]);
  assert.equal(view.rows[0].record, "5/5");
  assert.equal(view.more, 0);

  const all = shareSheetView({ rows, sportLabel: "NFL", week: 6, windowSize: 10, tier: undefined, limit: 2 });
  assert.equal(all.title, "80%+ Hit Rates");
  assert.equal(all.rows.length, 2);
  assert.equal(all.more, 1);
});
