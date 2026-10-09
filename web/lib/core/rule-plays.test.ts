import assert from "node:assert/strict";
import { test } from "node:test";

import { isTopTier, playLabel, playResult, playsFor, reasonLabel, type RulePlay } from "./rule-plays.ts";

let id = 1;
function play(overrides: Partial<RulePlay> = {}): RulePlay {
  return {
    id: id++,
    gameId: id,
    market: "spreads",
    side: "home",
    line: -2.5,
    price: -110,
    modelProb: 0.51,
    rawProb: null,
    edge: 0.01,
    modelLine: -6.9,
    gap: 4.4,
    sportsbookName: "Pinnacle",
    season: 2026,
    week: 7,
    startDate: "2026-10-10T16:00:00Z",
    neutralSite: false,
    homePoints: null,
    awayPoints: null,
    final: false,
    home: "WKU",
    away: "MOST",
    homeSchool: "Western Kentucky",
    awaySchool: "Missouri State",
    homeColor: null,
    homeAltColor: null,
    awayColor: null,
    awayAltColor: null,
    ...overrides,
  };
}

test("labels name the side taken and why it is a play", () => {
  const spread = play();
  assert.equal(playLabel(spread), "WKU -2.5");
  assert.equal(reasonLabel(spread), "Ours WKU -6.9 · 4.4 pts off");
  // Ours away by 2.0 against home -2.5: the away side.
  const away = play({ side: "away", modelLine: 2, gap: 4.5 });
  assert.equal(playLabel(away), "MOST +2.5");
  assert.equal(reasonLabel(away), "Ours MOST -2.0 · 4.5 pts off");
  const total = play({ market: "totals", side: "under", line: 52.5, modelLine: 45.5, gap: 7 });
  assert.equal(playLabel(total), "Under 52.5");
  assert.equal(reasonLabel(total), "Ours 45.5 · 7.0 pts off");
  const ml = play({ market: "h2h", side: "away", line: null, modelLine: null, gap: null, modelProb: 0.562, rawProb: 0.664 });
  assert.equal(playLabel(ml), "MOST ML");
  assert.equal(reasonLabel(ml), "Model 66% · win chance 56%");
  assert.equal(reasonLabel({ ...ml, rawProb: null }), "Win chance 56%");
});

test("totals 7+ points off are the top tier, nothing else is", () => {
  assert.equal(isTopTier(play({ market: "totals", gap: 7 })), true);
  assert.equal(isTopTier(play({ market: "totals", gap: 6.9 })), false);
  assert.equal(isTopTier(play({ market: "spreads", gap: 9 })), false);
});

test("results grade like the tracker view", () => {
  const done = { final: true, homePoints: 34, awayPoints: 13 };
  assert.equal(playResult(play({ ...done })), "win"); // WKU -2.5, won by 21
  assert.equal(playResult(play({ ...done, side: "away" })), "loss");
  assert.equal(playResult(play({ ...done, market: "totals", side: "under", line: 47 })), "push");
  assert.equal(playResult(play({ ...done, market: "h2h", side: "home", line: null })), "win");
  assert.equal(playResult(play()), "pending");
});

test("today is the current Eastern slate day; the week is everything, in kickoff order", () => {
  const fri = play({ startDate: "2026-10-09T23:30:00Z" }); // Fri 7:30 pm ET
  const friLate = play({ startDate: "2026-10-10T02:15:00Z" }); // Fri 10:15 pm ET
  const sat = play({ startDate: "2026-10-10T16:00:00Z" });
  const now = new Date("2026-10-09T18:00:00Z"); // Fri 2 pm ET
  assert.deepEqual(playsFor([sat, friLate, fri], "today", now).map((p) => p.id), [fri.id, friLate.id]);
  assert.deepEqual(playsFor([sat, friLate, fri], "week", now).map((p) => p.id), [fri.id, friLate.id, sat.id]);
  // 2 am ET Saturday is still Friday's slate (04:00 rollover).
  assert.equal(playsFor([sat, fri], "today", new Date("2026-10-10T06:00:00Z")).length, 1);
});
