import assert from "node:assert/strict";
import { test } from "node:test";

import type { LiveScore } from "./live.ts";
import type { GameLeg, PropLeg, SlipLeg } from "./slip.ts";
import {
  addSaved,
  MAX_SAVED,
  needsPolling,
  parseSavedSlips,
  propStatKey,
  slipOutcome,
  trackLeg,
  type SavedSlip,
  type TrackFacts,
  type TrackGame,
} from "./slip-track.ts";

const KICKOFF = "2026-10-17T19:30:00Z";
const DURING = new Date("2026-10-17T21:00:00Z");
const BEFORE = new Date("2026-10-17T12:00:00Z");
const NEXT_DAY = new Date("2026-10-18T14:00:00Z");

function gameLeg(market: GameLeg["market"], side: GameLeg["side"], line: number | null, period: GameLeg["period"] = "full"): GameLeg {
  return { kind: "game", gameId: 2, period, market, side, line, home: "TTU", away: "ASU", startDate: KICKOFF };
}

function propLeg(side: "over" | "under", line = 62.5, overrides: Partial<PropLeg> = {}): PropLeg {
  return {
    kind: "prop", gameId: 2, playerId: 10, marketKey: "rush_yards", side, line, binary: false,
    player: "R. Brown", market: "Rush Yds", matchup: "ASU @ TTU", startDate: KICKOFF, ...overrides,
  };
}

function live(overrides: Partial<LiveScore>): LiveScore {
  return {
    gameId: 2, status: "in_progress", period: 3, clock: "08:42", homePoints: 24, awayPoints: 17,
    homeLineScores: [7, 10, 7], awayLineScores: [7, 3, 7], possession: null, situation: null,
    lastPlay: null, updatedAt: "2026-10-17T21:00:00Z", ...overrides,
  };
}

function game(overrides: Partial<TrackGame> = {}): TrackGame {
  return { gameId: 2, startDate: KICKOFF, final: null, live: null, hasLiveFeed: true, statsIn: false, ...overrides };
}

const NO_STATS: TrackFacts = { games: [], stats: {} };

test("a game leg before kickoff is pending; during it says how it stands", () => {
  assert.equal(trackLeg(gameLeg("h2h", "home", null), game(), NO_STATS, BEFORE).state, "pending");

  const g = game({ live: live({}) });
  const ml = trackLeg(gameLeg("h2h", "home", null), g, NO_STATS, DURING);
  assert.equal(ml.state, "live");
  assert.equal(ml.trend, "ahead");
  assert.match(ml.detail, /3rd 08:42 · ASU 17 – TTU 24 · Leading by 7/);

  // Home -10.5 with a 7-point lead is short by 3.5; the away side covers.
  const spread = trackLeg(gameLeg("spreads", "home", -10.5), g, NO_STATS, DURING);
  assert.equal(spread.trend, "behind");
  assert.match(spread.detail, /Short by 3\.5/);
  assert.equal(trackLeg(gameLeg("spreads", "away", -10.5), g, NO_STATS, DURING).trend, "ahead");

  assert.match(trackLeg(gameLeg("totals", "over", 55.5), g, NO_STATS, DURING).detail, /41 so far, needs 14\.5 more/);
  assert.match(trackLeg(gameLeg("totals", "under", 55.5), g, NO_STATS, DURING).detail, /41 so far, 14\.5 to spare/);
});

test("a total past its line is decided while the game is still on", () => {
  const g = game({ live: live({}) });
  assert.equal(trackLeg(gameLeg("totals", "over", 40.5), g, NO_STATS, DURING).state, "won");
  assert.equal(trackLeg(gameLeg("totals", "under", 40.5), g, NO_STATS, DURING).state, "lost");
});

test("first quarter and first half settle from the line scores once that period ends", () => {
  const g = game({ live: live({}) });
  // 1Q: 7-7; 1H: TTU 17, ASU 10.
  assert.equal(trackLeg(gameLeg("h2h", "home", null, "q1"), g, NO_STATS, DURING).state, "push");
  assert.equal(trackLeg(gameLeg("spreads", "home", -3.5, "h1"), g, NO_STATS, DURING).state, "won");
  assert.equal(trackLeg(gameLeg("totals", "over", 27.5, "h1"), g, NO_STATS, DURING).state, "lost");

  // Still in the 2nd: the half is live, counting only its own quarters.
  const second = game({ live: live({ period: 2, homeLineScores: [7, 3], awayLineScores: [7, 0], homePoints: 10, awayPoints: 7 }) });
  const h1 = trackLeg(gameLeg("totals", "over", 27.5, "h1"), second, NO_STATS, DURING);
  assert.equal(h1.state, "live");
  assert.match(h1.detail, /17 so far/);
});

test("the final of record decides a full-game leg; overtime counts, not in the half", () => {
  const final = { homePoints: 31, awayPoints: 34, homeLineScores: [10, 7, 7, 4, 3], awayLineScores: [0, 14, 7, 7, 6] };
  const g = game({ final });
  assert.equal(trackLeg(gameLeg("h2h", "away", null), g, NO_STATS, NEXT_DAY).state, "won");
  assert.equal(trackLeg(gameLeg("spreads", "home", 3), g, NO_STATS, NEXT_DAY).state, "push");
  assert.equal(trackLeg(gameLeg("totals", "over", 60.5), g, NO_STATS, NEXT_DAY).state, "won");
  assert.equal(trackLeg(gameLeg("h2h", "home", null, "h1"), g, NO_STATS, NEXT_DAY).state, "won");
});

test("a league without a live feed or quarter scores says so instead of guessing", () => {
  const nfl = game({ hasLiveFeed: false });
  const during = trackLeg(gameLeg("h2h", "home", null), nfl, NO_STATS, DURING);
  assert.equal(during.state, "live");
  assert.match(during.detail, /no live scores/);

  const final = game({ hasLiveFeed: false, final: { homePoints: 20, awayPoints: 17, homeLineScores: null, awayLineScores: null } });
  assert.equal(trackLeg(gameLeg("h2h", "home", null), final, NO_STATS, NEXT_DAY).state, "won");
  assert.equal(trackLeg(gameLeg("h2h", "home", null, "q1"), final, NO_STATS, NEXT_DAY).state, "check");
});

test("a prop settles from the box score; with no line it is for the reader's book", () => {
  const key = propStatKey(2, 10, "rush_yards");
  const facts = (value: number | null): TrackFacts => ({ games: [], stats: { [key]: value } });
  const over = trackLeg(propLeg("over"), game(), facts(87), NEXT_DAY);
  assert.equal(over.state, "won");
  assert.equal(over.detail, "87 Rush Yds");
  assert.equal(trackLeg(propLeg("under"), game(), facts(87), NEXT_DAY).state, "lost");
  assert.equal(trackLeg(propLeg("over", 87), game(), facts(87), NEXT_DAY).state, "push");
  assert.equal(trackLeg(propLeg("over"), game(), facts(null), NEXT_DAY).state, "check");

  const atd = propLeg("over", 0.5, { marketKey: "anytime_td", binary: true, market: "Anytime TD" });
  const atdFacts = { games: [], stats: { [propStatKey(2, 10, "anytime_td")]: 0 } };
  assert.deepEqual(trackLeg(atd, game(), atdFacts, NEXT_DAY), { state: "lost", detail: "Did not score" });

  const noRow = trackLeg(propLeg("under"), game({ statsIn: true }), NO_STATS, NEXT_DAY);
  assert.equal(noRow.state, "check");
  assert.match(noRow.detail, /won if the player played/);

  assert.equal(trackLeg(propLeg("over"), game({ live: live({}) }), NO_STATS, DURING).state, "live");
  assert.equal(trackLeg(propLeg("over"), game({ live: live({ status: "completed" }) }), NO_STATS, DURING).state, "awaiting");
  assert.equal(trackLeg(propLeg("over"), game(), NO_STATS, BEFORE).state, "pending");
});

test("a slip is lost on its first lost leg and won when every leg is in", () => {
  const won = { state: "won" as const, detail: "" };
  const lost = { state: "lost" as const, detail: "" };
  const liveLeg = { state: "live" as const, detail: "" };
  const push = { state: "push" as const, detail: "" };
  assert.equal(slipOutcome([won, lost, liveLeg]).state, "lost");
  assert.deepEqual(slipOutcome([won, push]), { state: "won", label: "Won · 1 push" });
  assert.deepEqual(slipOutcome([won, liveLeg, { state: "pending", detail: "" }]), {
    state: "open",
    label: "1 of 3 won · 1 live · 1 to play",
  });
  assert.equal(slipOutcome([won, { state: "check", detail: "" }]).state, "check");
});

test("polling runs only while a leg is live or about to start", () => {
  const legs = [gameLeg("h2h", "home", null)];
  assert.equal(needsPolling(legs, [{ state: "live", detail: "" }], DURING), true);
  assert.equal(needsPolling(legs, [{ state: "pending", detail: "" }], BEFORE), false);
  assert.equal(needsPolling(legs, [{ state: "pending", detail: "" }], new Date("2026-10-17T19:25:00Z")), true);
  assert.equal(needsPolling(legs, [{ state: "awaiting", detail: "" }], NEXT_DAY), false);
});

test("saved slips: newest first, no duplicates, capped, junk dropped", () => {
  const slip = (id: string, legs: SlipLeg[] = [gameLeg("h2h", "home", null)]): SavedSlip => ({
    id, savedAt: KICKOFF, bookKey: "dk", bookName: "DraftKings", legs, prices: [-150], price: -150, settled: {},
  });
  const one = addSaved([], slip("a"));
  assert.equal(addSaved(one, slip("b")), one, "the same legs at the same book are not saved twice");
  const two = addSaved(one, slip("c", [propLeg("over")]));
  assert.deepEqual(two.map((s) => s.id), ["c", "a"]);

  let many: SavedSlip[] = [];
  for (let i = 0; i < MAX_SAVED + 5; i++) many = addSaved(many, slip(String(i), [propLeg("over", i + 0.5)]));
  assert.equal(many.length, MAX_SAVED);
  assert.equal(many[0].id, String(MAX_SAVED + 4));

  const parsed = parseSavedSlips([
    { ...slip("ok"), settled: { x: { state: "won", detail: "Final" }, y: { state: "live", detail: "" } } },
    { id: "no-legs", savedAt: KICKOFF, legs: [] },
    "junk",
  ]);
  assert.deepEqual(parsed.map((s) => s.id), ["ok"]);
  assert.deepEqual(Object.keys(parsed[0].settled), ["x"]);
});
